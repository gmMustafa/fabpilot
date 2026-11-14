"""Retrieval eval (3.4): run every variant over the gold questions, print + save a report.

    cd api
    uv run python ../evals/retrieval/run.py            # needs the ingested dev DB (app.rag.ingest)

No LLM involved: 0 free requests. Takes ~10 s (one model load, 20 questions x 5 variants).
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import KbChunk
from app.db.session import create_engine
from app.rag.embeddings import BGE_QUERY_PREFIX, FastEmbedder
from app.rag.evaluation import Outcome, Question, first_hit_rank, load_questions, parse_ref, report
from app.rag.retrieval import Filters, Mode, search
from app.runtime import run

HERE = Path(__file__).parent
K = 5


@dataclass(frozen=True)
class Variant:
    name: str
    mode: Mode
    max_per_doc: int | None = None
    query_prefix: bool = True


VARIANTS = [
    Variant("vector", Mode.VECTOR),
    Variant("vector-noprefix", Mode.VECTOR, query_prefix=False),
    Variant("text", Mode.TEXT),
    Variant("hybrid", Mode.HYBRID),
    Variant("hybrid-cap2", Mode.HYBRID, max_per_doc=2),
]


async def check_gold_exists(engine: AsyncEngine, questions: list[Question]) -> None:
    """A typo in the gold set would silently count as a miss for EVERY variant: fail loudly."""
    async with engine.connect() as conn:
        rows = (await conn.execute(select(KbChunk.doc_id, KbChunk.section_ref).distinct())).all()
    sections = {(d, s) for d, s in rows}
    docs = {d for d, _ in rows}
    missing = []
    for q in questions:
        for ref in q.relevant:
            doc, sec = parse_ref(ref)
            ok = (
                doc in docs
                if sec is None
                else any(d == doc and (s == sec or s.startswith(sec + ".")) for d, s in sections)
            )
            if not ok:
                missing.append(f"{q.id}: {ref}")
    if missing:
        raise SystemExit(
            "gold references not found in the DB (typo? not ingested?):\n  " + "\n  ".join(missing)
        )


async def evaluate(
    engine: AsyncEngine, embedder: FastEmbedder, questions: list[Question], v: Variant
) -> list[Outcome]:
    embedder.query_prefix = BGE_QUERY_PREFIX if v.query_prefix else ""
    outcomes = []
    for q in questions:
        hits = await search(
            engine,
            embedder,
            q.question,
            mode=v.mode,
            filters=Filters(machine_type=q.machine_type),
            k=K,
            max_per_doc=v.max_per_doc,
        )
        outcomes.append(
            Outcome(
                question_id=q.id,
                category=q.category,
                section_rank=first_hit_rank(hits, q.relevant),
                doc_rank=first_hit_rank(hits, q.relevant, doc_only=True),
            )
        )
    return outcomes


async def main() -> None:
    settings = get_settings()
    questions = load_questions(HERE / "questions.yaml")
    embedder = FastEmbedder(settings.embedding_model, settings.embedding_cache_dir)
    engine = create_engine(settings)
    try:
        await check_gold_exists(engine, questions)
        results = {v.name: await evaluate(engine, embedder, questions, v) for v in VARIANTS}
    finally:
        await engine.dispose()
        embedder.query_prefix = BGE_QUERY_PREFIX

    header = (
        f"# Retrieval eval\n\n{datetime.now(UTC):%Y-%m-%d %H:%M} UTC · {len(questions)} questions"
        f" · k={K} · model `{embedder.model_id}`\n\n"
        "One question = " + f"{100 / len(questions):.0f}" + " points: small differences are noise."
        "\n\n"
    )
    text = header + report(results, K)
    (HERE / "results.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    run(main())
