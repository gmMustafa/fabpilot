"""Ingestion (3.2b): corpus -> chunks -> embeddings -> Postgres. Idempotent: run it any time.

    uv run python -m app.rag.ingest

Per document:
    unchanged text AND same model   -> skip (no embedding work)
    new / changed / other model     -> re-chunk, re-embed, replace in ONE transaction
Documents that disappeared from data/docs are deleted: a removed SOP must not stay retrievable.

Embedding happens BEFORE the transaction opens: a transaction held open during seconds of CPU work
blocks other writers for nothing. The write itself (delete + insert) is milliseconds.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import KbChunk, KbDocument
from app.rag.chunking import chunk_document
from app.rag.corpus import Document, load_corpus
from app.rag.embeddings import Embedder

logger = logging.getLogger(__name__)

# Bump when chunking.py changes its output: the hash then changes for every document, so the
# next ingest re-chunks everything (same idea as HASH_VERSION in the prompt cache, 2.2).
CHUNKER_VERSION = 1

_INCIDENT_FIELDS = ("machine", "fault", "false_alarm", "root_cause")


class Action(StrEnum):
    ADD = "add"  # not in the DB yet
    UPDATE = "update"  # in the DB, but its text or the embedding model changed
    SKIP = "skip"  # in the DB with the same content hash AND the same model


@dataclass(frozen=True)
class Stored:
    content_hash: str
    embedding_model: str


def plan_action(stored: Stored | None, content_hash: str, embedding_model: str) -> Action:
    """What ingestion must do with one document, given what the DB has for it (None = nothing)."""

    # Document does not exist in the database
    if stored is None:
        return Action.ADD

    # Text or metadata changed
    if stored.content_hash != content_hash:
        return Action.UPDATE

    # Embedding model changed
    if stored.embedding_model != embedding_model:
        return Action.UPDATE

    # Everything is unchanged
    return Action.SKIP


def content_hash(doc: Document, max_tokens: int) -> str:
    """Everything that changes the stored chunks: metadata, body, chunker version and budget."""
    payload = {
        "chunker": CHUNKER_VERSION,
        "max_tokens": max_tokens,
        "meta": doc.meta.model_dump(mode="json"),
        "body": doc.body,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    chunks_written: int = 0


def _document_row(doc: Document, digest: str, model_id: str) -> dict[str, Any]:
    m = doc.meta
    incident = {k: v for k in _INCIDENT_FIELDS if (v := m.model_dump(mode="json")[k]) is not None}
    return {
        "doc_id": m.doc_id,
        "source_type": m.source_type,
        "machine_types": [t.value for t in m.machine_types],
        "title": m.title,
        "revision": m.revision,
        "effective_date": m.effective_date,
        "meta": incident,
        "content_hash": digest,
        "embedding_model": model_id,
    }


async def ingest(
    engine: AsyncEngine, docs: list[Document], embedder: Embedder, max_tokens: int
) -> IngestReport:
    report = IngestReport()
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(KbDocument.doc_id, KbDocument.content_hash, KbDocument.embedding_model)
        )
        stored = {r.doc_id: Stored(r.content_hash, r.embedding_model) for r in rows}

    for doc in docs:
        doc_id = doc.meta.doc_id
        digest = content_hash(doc, max_tokens)
        action = plan_action(stored.get(doc_id), digest, embedder.model_id)
        if action is Action.SKIP:
            report.skipped.append(doc_id)
            continue

        chunks = chunk_document(doc, embedder.count_tokens, max_tokens)
        vectors = await asyncio.to_thread(embedder.embed_passages, [c.text for c in chunks])
        async with engine.begin() as conn:  # replace atomically: never a half-indexed document
            await conn.execute(delete(KbDocument).where(KbDocument.doc_id == doc_id))  # cascades
            await conn.execute(
                insert(KbDocument).values(_document_row(doc, digest, embedder.model_id))
            )
            await conn.execute(
                insert(KbChunk),
                [
                    {
                        "doc_id": doc_id,
                        "ordinal": c.ordinal,
                        "section_ref": c.section_ref,
                        "section_title": c.section_title,
                        "text": c.text,
                        "token_count": c.token_count,
                        "embedding": vec,
                    }
                    for c, vec in zip(chunks, vectors, strict=True)
                ],
            )
        (report.added if action is Action.ADD else report.updated).append(doc_id)
        report.chunks_written += len(chunks)

    if gone := sorted(stored.keys() - {d.meta.doc_id for d in docs}):
        async with engine.begin() as conn:
            await conn.execute(delete(KbDocument).where(KbDocument.doc_id.in_(gone)))
        report.removed = gone
    return report


async def _main() -> None:
    from app.db.session import create_engine
    from app.rag.embeddings import build_embedder

    settings = get_settings()
    docs = load_corpus(settings.docs_dir)  # validate everything before touching the DB
    embedder = build_embedder(settings.embedding_model, settings.embedding_cache_dir)
    engine = create_engine(settings)
    try:
        r = await ingest(engine, docs, embedder, settings.chunk_max_tokens)
    finally:
        await engine.dispose()
    logger.info(
        "model %s | added %d, updated %d, skipped %d, removed %d | %d chunks written",
        embedder.model_id,
        len(r.added),
        len(r.updated),
        len(r.skipped),
        len(r.removed),
        r.chunks_written,
    )


if __name__ == "__main__":
    from app.runtime import run

    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(message)s")
    run(_main())
