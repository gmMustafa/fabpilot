"""Retrieval with the REAL model over the dev DB's ingested corpus (marker `embedding`).

Needs `uv run python -m app.rag.ingest` to have run against DATABASE_URL. A first sanity check,
not the evaluation: 3.4 measures recall@5 on 20 questions.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import MachineType
from app.db.session import create_engine
from app.rag.corpus import load_corpus
from app.rag.embeddings import Embedder, build_embedder
from app.rag.retrieval import NO_FILTERS, Filters, Mode, search

pytestmark = pytest.mark.embedding


@pytest.fixture(scope="module")
def embedder() -> Embedder:
    s = get_settings()
    return build_embedder(s.embedding_model, s.embedding_cache_dir)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_engine(get_settings())
    yield eng
    await eng.dispose()


async def _docs(
    engine: AsyncEngine, emb: Embedder, q: str, mode: Mode, filters: Filters = NO_FILTERS
) -> list[str]:
    return [r.doc_id for r in await search(engine, emb, q, mode=mode, filters=filters, k=5)]


async def test_text_search_puts_the_exact_code_row_first(
    engine: AsyncEngine, embedder: Embedder
) -> None:
    """Exact terms are full-text search's job: the alarm-code table row of the pump manual."""
    r = await search(engine, embedder, "PRESS-HIGH on suction_strainer_dp", mode=Mode.TEXT, k=5)
    assert r[0].citation == "[MAN-PMP-001 §4]"


async def test_hybrid_keeps_the_pump_manual_in_the_top_five(
    engine: AsyncEngine, embedder: Embedder
) -> None:
    """Only top 5, not top 1-3: RRF fuses per CHUNK, and the manual's relevance is split over
    §4 (text list) and §5.1 (vector list), while incident timelines appear in both lists.
    Seen in 3.3; whether to fuse per document is a question for the 3.4 eval."""
    docs = await _docs(engine, embedder, "PRESS-HIGH on suction_strainer_dp", Mode.HYBRID)
    assert "MAN-PMP-001" in docs


async def test_vector_finds_a_paraphrase_without_shared_codes(
    engine: AsyncEngine, embedder: Embedder
) -> None:
    docs = await _docs(
        engine, embedder, "the basket in front of the water pump is full of dirt", Mode.VECTOR
    )
    assert {"MAN-PMP-001", "SOP-PMP-006"} & set(docs)


async def test_filter_keeps_results_on_the_machine_type(
    engine: AsyncEngine, embedder: Embedder
) -> None:
    allowed = {
        d.meta.doc_id
        for d in load_corpus(get_settings().docs_dir)
        if MachineType.COMPRESSOR in d.meta.machine_types  # compressor docs + generic SOPs
    }
    f = Filters(machine_type=MachineType.COMPRESSOR)
    docs = await _docs(engine, embedder, "temperature rising", Mode.HYBRID, f)
    assert docs and set(docs) <= allowed
