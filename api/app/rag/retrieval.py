"""Retrieval (3.3): vector search, full-text search, and both fused with Reciprocal Rank Fusion.

Why two searches: embeddings find MEANING ("strainer blocked" ~ "clogged suction basket") but are
weak at exact TERMS (PRESS-LOW, suction_strainer_dp, SOP-GEN-004); full-text search is the opposite.
Hybrid = run both, fuse the rankings.

    uv run python -m app.rag.retrieval "PRESS-LOW on suction_strainer_dp" --machine-type pump

Filters (machine type, source type) are applied INSIDE both searches, before ranking: a
compressor chunk can't push a pump chunk out of the top k if it's never a candidate.
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, computed_field
from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    any_,
    cast,
    func,
    literal,
    literal_column,
    select,
)
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import KbChunk, KbDocument, MachineType, SourceType
from app.rag.embeddings import Embedder

RRF_K = 60  # the constant from the original RRF paper (Cormack et al., 2009); rarely worth tuning
CANDIDATES = 20  # per search: enough depth that fusion has something to work with


class Mode(StrEnum):
    VECTOR = "vector"
    TEXT = "text"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class Filters:
    machine_type: MachineType | None = None  # generic SOPs list all types, so they always match
    source_types: tuple[SourceType, ...] = ()  # empty = all


NO_FILTERS = Filters()  # shared default: frozen, so it can't be mutated between calls


class RetrievedChunk(BaseModel):
    chunk_id: int
    doc_id: str
    section_ref: str
    section_title: str
    source_type: SourceType
    title: str
    text: str
    score: float  # RRF score (hybrid) or the fused score of a single list; compare within a query

    @computed_field  # type: ignore[prop-decorator]  # pydantic's documented pattern; mypy can't
    @property
    def citation(self) -> str:
        """What the agent writes in its answer, e.g. "[MAN-PMP-001 §5.1]"."""
        return f"[{self.doc_id} {self.section_ref}]".replace(" ]", "]")


def rrf_fuse(
    ranked_lists: Sequence[Sequence[int]], k: int = RRF_K, limit: int = 10
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: combine rankings without comparing their scores.

    Each list holds chunk ids, best first. An id at (1-based) rank r in a list earns 1 / (k + r);
    an id's score is the sum over all lists it appears in (absent from a list = earns nothing).
    Returns [(id, score), ...] best first, at most `limit` entries.

    - Ties: the smaller id first (deterministic: same input, same output, testable).
    - Empty input, or only empty lists -> [].
    - An id appearing twice in ONE list counts only at its best (first) position there.
    - Raises ValueError if k < 1 or limit < 1.

    Why ranks, not scores: cosine distance (0..2) and ts_rank_cd (unbounded) are on scales that
    can't be added. Ranks are always comparable. k damps the advantage of the very top ranks:
    with k=60, rank 1 (1/61) is worth barely more than rank 2 (1/62), so agreement between the
    lists matters more than either list's single favourite.
    """
    if k < 1:
        raise ValueError("k must be at least 1")

    if limit < 1:
        raise ValueError("limit must be at least 1")

    scores: dict[int, float] = {}

    for lst in ranked_lists:
        seen = set()

        for rank, cid in enumerate(lst, start=1):
            # Avoid counting duplicate IDs in the same list
            if cid in seen:
                continue

            seen.add(cid)

            # Add reciprocal rank score
            scores[cid] = scores.get(cid, 0.0) + 1 / (k + rank)

    # Highest score first, smaller ID first in case of ties
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))

    return ranked[:limit]


def _filtered(stmt: Select[int], filters: Filters) -> Select[int]:
    stmt = stmt.join(KbDocument, KbDocument.doc_id == KbChunk.doc_id)
    if filters.machine_type is not None:
        # :type = ANY(machine_types): any_() is the ARRAY form (.any() is for relationships).
        stmt = stmt.where(literal(filters.machine_type.value) == any_(KbDocument.machine_types))
    if filters.source_types:
        stmt = stmt.where(KbDocument.source_type.in_(filters.source_types))
    return stmt


def or_tsquery(query: str) -> ColumnElement[Any]:
    """websearch_to_tsquery joins words with AND, so a natural question ("pump strainer clogged")
    matches only chunks containing EVERY word -- usually none. Turning the top-level & into | makes
    it "any of these words"; ts_rank_cd still ranks chunks with more matches higher. Phrases from
    codes (press-low <-> ...) stay phrases. Limitation: a negated term (-word) would become
    "OR NOT word"; the agent doesn't write negations."""
    english: ColumnElement[Any] = literal_column("'english'::regconfig")
    return cast(
        func.replace(cast(func.websearch_to_tsquery(english, query), Text), " & ", " | "),
        TSQUERY,
    )


async def vector_search(
    conn: AsyncConnection, query_vec: list[float], filters: Filters, limit: int = CANDIDATES
) -> list[int]:
    stmt = _filtered(select(KbChunk.id), filters)
    stmt = stmt.order_by(KbChunk.embedding.cosine_distance(query_vec), KbChunk.id).limit(limit)
    return list((await conn.execute(stmt)).scalars())


async def text_search(
    conn: AsyncConnection, query: str, filters: Filters, limit: int = CANDIDATES
) -> list[int]:
    tsq = or_tsquery(query)
    stmt = _filtered(select(KbChunk.id), filters).where(KbChunk.tsv.op("@@")(tsq))
    stmt = stmt.order_by(func.ts_rank_cd(KbChunk.tsv, tsq).desc(), KbChunk.id).limit(limit)
    return list((await conn.execute(stmt)).scalars())


async def _hydrate(conn: AsyncConnection, scored: list[tuple[int, float]]) -> list[RetrievedChunk]:
    if not scored:
        return []
    rows = await conn.execute(
        select(
            KbChunk.id,
            KbChunk.doc_id,
            KbChunk.section_ref,
            KbChunk.section_title,
            KbChunk.text,
            KbDocument.source_type,
            KbDocument.title,
        )
        .join(KbDocument, KbDocument.doc_id == KbChunk.doc_id)
        .where(KbChunk.id.in_([cid for cid, _ in scored]))
    )
    by_id = {r.id: r for r in rows}
    return [
        RetrievedChunk(
            chunk_id=cid,
            doc_id=by_id[cid].doc_id,
            section_ref=by_id[cid].section_ref,
            section_title=by_id[cid].section_title,
            source_type=by_id[cid].source_type,
            title=by_id[cid].title,
            text=by_id[cid].text,
            score=score,
        )
        for cid, score in scored
        if cid in by_id  # deleted between search and hydrate by a concurrent ingest: drop it
    ]


def cap_per_document(chunks: list[RetrievedChunk], max_per_doc: int) -> list[RetrievedChunk]:
    """Keep the ranking, but at most `max_per_doc` chunks of any one document (3.3 finding: a
    drift question returned 5 chunks of ONE incident and pushed the procedure out of the top k)."""
    per_doc: dict[str, int] = {}
    kept: list[RetrievedChunk] = []
    for c in chunks:
        if per_doc.get(c.doc_id, 0) < max_per_doc:
            per_doc[c.doc_id] = per_doc.get(c.doc_id, 0) + 1
            kept.append(c)
    return kept


async def search(
    engine: AsyncEngine,
    embedder: Embedder,
    query: str,
    *,
    mode: Mode = Mode.HYBRID,
    filters: Filters = NO_FILTERS,
    k: int = 5,
    max_per_doc: int | None = None,
) -> list[RetrievedChunk]:
    """Top-k chunks for `query`. All modes return RRF-style scores, so results look the same.

    max_per_doc: diversity cap, applied AFTER fusion (so the fused order is kept). Fusion then
    keeps every candidate, so the cap has enough to fill k slots from other documents.
    """
    if not query.strip():
        return []
    if max_per_doc is not None and max_per_doc < 1:
        raise ValueError("max_per_doc must be >= 1")
    lists: list[list[int]] = []
    async with engine.connect() as conn:
        if mode in (Mode.VECTOR, Mode.HYBRID):
            # CPU-bound model call: off the event loop, or every other request waits for it.
            vec = await asyncio.to_thread(embedder.embed_query, query)
            lists.append(await vector_search(conn, vec, filters))
        if mode in (Mode.TEXT, Mode.HYBRID):
            lists.append(await text_search(conn, query, filters))
        if max_per_doc is None:
            return await _hydrate(conn, rrf_fuse(lists, limit=k))
        everything = await _hydrate(conn, rrf_fuse(lists, limit=CANDIDATES * len(lists)))
    return cap_per_document(everything, max_per_doc)[:k]


async def _main(args: argparse.Namespace) -> None:
    from app.config import get_settings
    from app.db.session import create_engine
    from app.rag.embeddings import build_embedder

    settings = get_settings()
    embedder = build_embedder(settings.embedding_model, settings.embedding_cache_dir)
    engine = create_engine(settings)
    filters = Filters(
        machine_type=MachineType(args.machine_type) if args.machine_type else None,
        source_types=tuple(SourceType(s) for s in args.source_type),
    )
    modes = list(Mode) if args.mode == "all" else [Mode(args.mode)]
    try:
        for mode in modes:
            print(f"\n== {mode.value}")
            for r in await search(
                engine, embedder, args.query, mode=mode, filters=filters, k=args.k
            ):
                print(f"  {r.score:.4f}  {r.citation:<22} {r.section_title[:60]}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    from app.runtime import run

    parser = argparse.ArgumentParser(description="Search the knowledge base.")
    parser.add_argument("query")
    parser.add_argument("--mode", default="all", choices=["all", *[m.value for m in Mode]])
    parser.add_argument("--machine-type", choices=[t.value for t in MachineType])
    parser.add_argument(
        "--source-type", action="append", default=[], choices=[s.value for s in SourceType]
    )
    parser.add_argument("-k", type=int, default=5)
    run(_main(parser.parse_args()))
