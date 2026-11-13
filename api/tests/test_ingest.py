"""Ingestion: plan_action (your TODO), then idempotency against a throwaway migrated DB."""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.models import KbChunk, KbDocument
from app.rag.chunking import chunk_document
from app.rag.corpus import load_corpus
from app.rag.ingest import Action, Stored, ingest, plan_action
from app.testing import FakeEmbedder

# ------------------------------------------------------------------ plan_action (your TODO)


def test_new_document_is_added() -> None:
    assert plan_action(None, "h1", "model-a") is Action.ADD


def test_unchanged_document_is_skipped() -> None:
    assert plan_action(Stored("h1", "model-a"), "h1", "model-a") is Action.SKIP


def test_changed_text_is_updated() -> None:
    assert plan_action(Stored("h1", "model-a"), "h2", "model-a") is Action.UPDATE


def test_new_embedding_model_is_updated_even_if_text_is_unchanged() -> None:
    """Vectors from two models live in different spaces: keeping the old ones would make every
    similarity score between a new-model query and an old-model chunk meaningless."""
    assert plan_action(Stored("h1", "model-a"), "h1", "model-b") is Action.UPDATE


def test_both_changed_is_updated() -> None:
    assert plan_action(Stored("h1", "model-a"), "h2", "model-b") is Action.UPDATE


# ------------------------------------------------------------------ ingest() against a real DB

DOCS = ["manuals/MAN-PMP-001.md", "sops/SOP-PMP-006.md", "incidents/INC-2024-008.md"]
MAX_TOKENS = 350


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """A small private copy of the corpus, so tests can edit and delete files."""
    src = get_settings().docs_dir
    for rel in DOCS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / rel, tmp_path / rel)
    return tmp_path


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


async def _count(engine: AsyncEngine, model: type[KbDocument] | type[KbChunk]) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(select(func.count()).select_from(model))).scalar_one())


@pytest.mark.integration
async def test_first_run_adds_everything(engine: AsyncEngine, docs_dir: Path) -> None:
    docs, emb = load_corpus(docs_dir), FakeEmbedder()
    r = await ingest(engine, docs, emb, MAX_TOKENS)

    assert sorted(r.added) == ["INC-2024-008", "MAN-PMP-001", "SOP-PMP-006"]
    expected_chunks = sum(len(chunk_document(d, emb.count_tokens, MAX_TOKENS)) for d in docs)
    assert r.chunks_written == expected_chunks == await _count(engine, KbChunk)

    async with engine.connect() as conn:
        inc = (
            await conn.execute(select(KbDocument).where(KbDocument.doc_id == "INC-2024-008"))
        ).one()
        assert inc.machine_types == ["pump"]
        assert inc.meta["machine"] == "M-09" and inc.meta["fault"] == "clogged_filter"
        assert inc.embedding_model == "fake-embedder-1"
        # A Core connection returns ROWS of columns, not ORM objects: select the columns we need.
        chunk = (await conn.execute(select(KbChunk.text, KbChunk.embedding).limit(1))).one()
    # The vector survives the round trip through pgvector unchanged (float32 precision).
    assert chunk.embedding == pytest.approx(emb.embed_query(chunk.text), abs=1e-6)


@pytest.mark.integration
async def test_second_run_skips_everything_and_embeds_nothing(
    engine: AsyncEngine, docs_dir: Path
) -> None:
    docs, emb = load_corpus(docs_dir), FakeEmbedder()
    await ingest(engine, docs, emb, MAX_TOKENS)
    chunks, embedded = await _count(engine, KbChunk), emb.passages_embedded

    r = await ingest(engine, docs, emb, MAX_TOKENS)
    assert (r.added, r.updated, r.removed) == ([], [], [])
    assert len(r.skipped) == 3
    assert emb.passages_embedded == embedded  # skipping really costs nothing
    assert await _count(engine, KbChunk) == chunks


@pytest.mark.integration
async def test_changed_document_is_replaced_not_duplicated(
    engine: AsyncEngine, docs_dir: Path
) -> None:
    emb = FakeEmbedder()
    await ingest(engine, load_corpus(docs_dir), emb, MAX_TOKENS)

    sop = docs_dir / "sops/SOP-PMP-006.md"
    sop.write_text(sop.read_text(encoding="utf-8") + "\nAn added sentence.\n", encoding="utf-8")
    r = await ingest(engine, load_corpus(docs_dir), emb, MAX_TOKENS)

    assert r.updated == ["SOP-PMP-006"] and len(r.skipped) == 2
    async with engine.connect() as conn:
        dupes = await conn.execute(
            text("SELECT doc_id, ordinal FROM chunks GROUP BY 1, 2 HAVING count(*) > 1")
        )
        assert dupes.all() == []
        texts = (
            await conn.execute(select(KbChunk.text).where(KbChunk.doc_id == "SOP-PMP-006"))
        ).scalars()
        assert any("An added sentence." in t for t in texts)


@pytest.mark.integration
async def test_new_model_reindexes_everything(engine: AsyncEngine, docs_dir: Path) -> None:
    docs = load_corpus(docs_dir)
    await ingest(engine, docs, FakeEmbedder("model-a"), MAX_TOKENS)
    r = await ingest(engine, docs, FakeEmbedder("model-b"), MAX_TOKENS)
    assert len(r.updated) == 3 and r.skipped == []
    async with engine.connect() as conn:
        models = (await conn.execute(select(KbDocument.embedding_model).distinct())).scalars()
        assert list(models) == ["model-b"]  # never a mix of two vector spaces


@pytest.mark.integration
async def test_deleted_file_is_removed_with_its_chunks(engine: AsyncEngine, docs_dir: Path) -> None:
    emb = FakeEmbedder()
    await ingest(engine, load_corpus(docs_dir), emb, MAX_TOKENS)
    (docs_dir / "sops/SOP-PMP-006.md").unlink()

    r = await ingest(engine, load_corpus(docs_dir), emb, MAX_TOKENS)
    assert r.removed == ["SOP-PMP-006"]
    async with engine.connect() as conn:
        left = await conn.execute(select(func.count()).where(KbChunk.doc_id == "SOP-PMP-006"))
        assert left.scalar_one() == 0  # ON DELETE CASCADE took the chunks along
