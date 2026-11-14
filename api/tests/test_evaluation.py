"""Retrieval-eval library: gold references, ranks, scores, the real question file, the cap."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.models import SourceType
from app.rag.evaluation import (
    Category,
    Outcome,
    Question,
    first_hit_rank,
    load_questions,
    matches,
    report,
    score,
)
from app.rag.retrieval import RetrievedChunk, cap_per_document

QUESTIONS_FILE = Path(__file__).resolve().parents[2] / "evals" / "retrieval" / "questions.yaml"


@dataclass(frozen=True)
class H:
    doc_id: str
    section_ref: str


def test_section_reference_matches_itself_and_its_subsections() -> None:
    assert matches(H("MAN-PMP-001", "§5"), "MAN-PMP-001 §5")
    assert matches(H("MAN-PMP-001", "§5.1"), "MAN-PMP-001 §5")
    assert not matches(H("MAN-PMP-001", "§50"), "MAN-PMP-001 §5")  # prefix of the NUMBER, not text
    assert not matches(H("MAN-PMP-001", "§4"), "MAN-PMP-001 §5")
    assert not matches(H("SOP-PMP-006", "§5"), "MAN-PMP-001 §5")


def test_document_reference_and_doc_only_mode() -> None:
    assert matches(H("SOP-CNV-008", "§4.2"), "SOP-CNV-008")
    assert matches(H("MAN-PMP-001", "§2"), "MAN-PMP-001 §5", doc_only=True)


def test_first_hit_rank_takes_the_first_of_any_reference() -> None:
    results = [H("A-X", "§1"), H("MAN-PMP-001", "§2"), H("SOP-PMP-006", "§4")]
    assert first_hit_rank(results, ["SOP-PMP-006", "MAN-PMP-001 §2"]) == 2
    assert first_hit_rank(results, ["MAN-CMP-001"]) is None
    assert first_hit_rank(results, ["MAN-PMP-001 §5"], doc_only=True) == 2


def test_scores() -> None:
    outcomes = [
        Outcome("Q01", Category.CODE, section_rank=1, doc_rank=1),
        Outcome("Q02", Category.CODE, section_rank=4, doc_rank=2),
        Outcome("Q03", Category.SYMPTOM, section_rank=None, doc_rank=3),
        Outcome("Q04", Category.SYMPTOM, section_rank=None, doc_rank=None),
    ]
    s = score(outcomes, k=5)
    assert (s.hit_at_k, s.doc_hit_at_k) == (0.5, 0.75)
    assert s.mrr == pytest.approx((1 + 1 / 4) / 4)
    assert score(outcomes, k=3).hit_at_k == 0.25  # rank 4 is outside the top 3


def test_report_has_one_row_per_variant_and_question() -> None:
    o = [Outcome("Q01", Category.CODE, 1, 1), Outcome("Q02", Category.SYMPTOM, None, 2)]
    md = report({"vector": o, "text": o}, k=5)
    assert "| vector | 50% | 100% | 0.50 |" in md
    assert "| Q02 | symptom | - | - |" in md


@pytest.mark.parametrize(
    "bad",
    [
        {"relevant": ["MAN-PMP-1 §5"]},
        {"relevant": ["MAN-PMP-001 5.1"]},  # § missing
        {"relevant": []},
        {"category": "vibes"},
        {"id": "Q1"},
        {"extra": "typo'd key"},
    ],
)
def test_question_schema_rejects_mistakes(bad: dict[str, object]) -> None:
    good: dict[str, object] = {
        "id": "Q01",
        "question": "What does PRESS-LOW mean?",
        "category": "code",
        "relevant": ["MAN-CMP-001 §4"],
    }
    with pytest.raises(ValidationError):
        Question.model_validate({**good, **bad})


def test_the_real_question_file_loads() -> None:
    questions = load_questions(QUESTIONS_FILE)
    assert len(questions) >= 2
    assert len({q.id for q in questions}) == len(questions)


def _chunk(cid: int, doc: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=cid,
        doc_id=doc,
        section_ref="§1",
        section_title="t",
        source_type=SourceType.MANUAL,
        title="t",
        text="t",
        score=1.0 / cid,
    )


def test_cap_per_document_keeps_order_and_limits_each_document() -> None:
    chunks = [_chunk(1, "A"), _chunk(2, "A"), _chunk(3, "A"), _chunk(4, "B"), _chunk(5, "A")]
    assert [c.chunk_id for c in cap_per_document(chunks, 2)] == [1, 2, 4]
