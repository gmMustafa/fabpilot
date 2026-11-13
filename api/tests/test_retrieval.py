"""Retrieval: rrf_fuse (your TODO), filters and full-text search against a migrated DB.

The fake embedder knows no meaning, so DB tests here check plumbing (filters, ordering, OR
semantics, citations). Retrieval QUALITY with the real model is in test_retrieval_live.py and 3.4.
"""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.models import KbChunk, MachineType, SourceType
from app.rag.corpus import load_corpus
from app.rag.ingest import ingest
from app.rag.retrieval import Filters, Mode, rrf_fuse, search, text_search, vector_search
from app.testing import FakeEmbedder

# ------------------------------------------------------------------ rrf_fuse (your TODO)


def test_rrf_rewards_agreement_between_lists() -> None:
    # 2 is 2nd in both lists; 1 and 3 are each 1st in one list only.
    fused = rrf_fuse([[1, 2, 4], [3, 2, 5]], k=60)
    assert [cid for cid, _ in fused][:1] == [2]
    assert fused[0][1] == pytest.approx(2 / 62)


def test_rrf_scores_and_order() -> None:
    fused = rrf_fuse([[10, 20], [20, 30]], k=1)
    # 10: 1/2 | 20: 1/3 + 1/2 | 30: 1/3
    assert fused == [(20, pytest.approx(1 / 3 + 1 / 2)), (10, 0.5), (30, pytest.approx(1 / 3))]


def test_rrf_single_list_keeps_its_order() -> None:
    assert [cid for cid, _ in rrf_fuse([[7, 3, 9]])] == [7, 3, 9]


def test_rrf_ties_break_by_smaller_id() -> None:
    assert [cid for cid, _ in rrf_fuse([[5], [2]])] == [2, 5]  # both 1/61


def test_rrf_duplicate_in_one_list_counts_once_at_best_rank() -> None:
    fused = dict(rrf_fuse([[1, 2, 1]], k=60))
    assert fused[1] == pytest.approx(1 / 61)


def test_rrf_limit() -> None:
    assert len(rrf_fuse([list(range(50))], limit=5)) == 5


@pytest.mark.parametrize("lists", [[], [[]], [[], []]])
def test_rrf_empty_input(lists: list[list[int]]) -> None:
    assert rrf_fuse(lists) == []


@pytest.mark.parametrize(("k", "limit"), [(0, 5), (-1, 5), (60, 0)])
def test_rrf_rejects_bad_parameters(k: int, limit: int) -> None:
    with pytest.raises(ValueError):
        rrf_fuse([[1]], k=k, limit=limit)


# ------------------------------------------------------------------ against a real DB

DOCS = [
    "manuals/MAN-PMP-001.md",
    "manuals/MAN-CMP-001.md",
    "sops/SOP-PMP-006.md",
    "sops/SOP-GEN-004.md",
    "incidents/INC-2024-008.md",
]


@pytest.fixture
async def engine(migrated_db_url: str, tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    src = get_settings().docs_dir
    for rel in DOCS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / rel, tmp_path / rel)
    eng = create_async_engine(migrated_db_url)
    await ingest(eng, load_corpus(tmp_path), FakeEmbedder(), max_tokens=350)
    yield eng
    await eng.dispose()


async def _docs_of(engine: AsyncEngine, chunk_ids: list[int]) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(KbChunk.id, KbChunk.doc_id).where(KbChunk.id.in_(chunk_ids))
        )
        by_id = {cid: doc for cid, doc in rows}  # Row unpacks like a tuple (2.1: no .tuples())
    return [by_id[c] for c in chunk_ids]


@pytest.mark.integration
async def test_text_search_finds_alarm_codes_and_sensor_names(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        ids = await text_search(conn, "PRESS-HIGH suction_strainer_dp", Filters())
    assert (await _docs_of(engine, ids))[0] in {"MAN-PMP-001", "SOP-PMP-006"}


@pytest.mark.integration
async def test_text_search_uses_or_so_natural_questions_match(engine: AsyncEngine) -> None:
    """With AND, a question containing ANY word absent from a chunk matches nothing."""
    async with engine.connect() as conn:
        ids = await text_search(conn, "why is the pump strainer clogged so often", Filters())
    assert ids  # "often" appears nowhere, yet strainer chunks are found


@pytest.mark.integration
async def test_machine_type_filter_keeps_generic_sops(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        ids = await text_search(
            conn, "pressure filter sensor", Filters(machine_type=MachineType.COMPRESSOR)
        )
    docs = set(await _docs_of(engine, ids))
    assert "MAN-CMP-001" in docs
    assert "SOP-GEN-004" in docs  # generic: lists all five machine types
    assert not docs & {"MAN-PMP-001", "SOP-PMP-006", "INC-2024-008"}  # pump-only documents


@pytest.mark.integration
async def test_source_type_filter(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        ids = await text_search(conn, "strainer", Filters(source_types=(SourceType.SOP,)))
    assert set(await _docs_of(engine, ids)) <= {"SOP-PMP-006", "SOP-GEN-004"}


@pytest.mark.integration
async def test_vector_search_ranks_the_identical_text_first(engine: AsyncEngine) -> None:
    """Fake vectors: only an IDENTICAL text has distance 0. That's enough to test the ORDER BY."""
    async with engine.connect() as conn:
        target_id, target_text = (
            await conn.execute(select(KbChunk.id, KbChunk.text).order_by(KbChunk.id).limit(1))
        ).one()
        ids = await vector_search(conn, FakeEmbedder().embed_query(target_text), Filters())
    assert ids[0] == target_id


@pytest.mark.integration
async def test_hybrid_search_returns_citable_chunks(engine: AsyncEngine) -> None:
    results = await search(engine, FakeEmbedder(), "PRESS-HIGH suction_strainer_dp", k=3)
    assert 1 <= len(results) <= 3
    assert results == sorted(results, key=lambda r: -r.score)
    top = results[0]
    assert top.citation == f"[{top.doc_id} {top.section_ref}]"
    assert top.text.startswith(top.doc_id)  # the context header is part of what's returned


@pytest.mark.integration
async def test_blank_query_returns_nothing(engine: AsyncEngine) -> None:
    assert await search(engine, FakeEmbedder(), "   ", mode=Mode.HYBRID) == []
