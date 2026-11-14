"""Retrieval evaluation (3.4): gold questions -> hit@k, doc-hit@k, MRR per retrieval variant.

The library part (schema, metrics, report) lives here so it is unit-tested; the runner is
evals/retrieval/run.py.

Gold references: "MAN-PMP-001 §5.1" (that section; §5 also accepts §5.1, §5.2 ...) or
"MAN-PMP-001" (any section of the document). Several references = any of them counts.

Small numbers: with 20 questions one question is worth 5 points. Differences of 10-15 points
can be noise; the per-question matrix shows WHICH questions moved, which matters more.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import MachineType
from app.rag.library import section_in

_REF = re.compile(
    r"^(?P<doc>(?:MAN|SOP)-[A-Z]{3}-\d{3}|INC-\d{4}-\d{3})"  # the document ID
    r"(?: (?P<sec>§\d+(?:\.\d+)*))?$"  # optional section: " §5" or " §5.1"
)


class Category(StrEnum):
    CODE = "code"  # exact alarm code / sensor / document ID: full-text should shine
    SYMPTOM = "symptom"  # described in own words: embeddings should shine
    PROCEDURE = "procedure"  # "how do I ...": SOPs
    FALSE_ALARM = "false_alarm"  # the hard case: one signal lies
    CROSS_MACHINE = "cross_machine"  # shared supplies: air, cooling water


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^Q\d{2}$")
    question: str = Field(min_length=10)
    category: Category
    machine_type: MachineType | None = None  # the agent usually knows it from the incident
    relevant: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _refs_are_well_formed(self) -> Self:
        if bad := [r for r in self.relevant if not _REF.match(r)]:
            raise ValueError(f"{self.id}: malformed references {bad} (e.g. 'MAN-PMP-001 §5.1')")
        return self


def load_questions(path: Path) -> list[Question]:
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    questions = [Question.model_validate(q) for q in raw["questions"]]
    ids = [q.id for q in questions]
    if dupes := sorted({i for i in ids if ids.count(i) > 1}):
        raise ValueError(f"duplicate question ids: {dupes}")
    return questions


def parse_ref(ref: str) -> tuple[str, str | None]:
    m = _REF.match(ref)
    if not m:
        raise ValueError(f"malformed reference: {ref!r}")
    return m["doc"], m["sec"]


class Hit(Protocol):
    @property
    def doc_id(self) -> str: ...

    @property
    def section_ref(self) -> str: ...


def matches(hit: Hit, ref: str, *, doc_only: bool = False) -> bool:
    doc, sec = parse_ref(ref)
    if hit.doc_id != doc:
        return False
    if doc_only or sec is None:
        return True
    return section_in(hit.section_ref, sec)


def first_hit_rank(
    results: Sequence[Hit], relevant: list[str], *, doc_only: bool = False
) -> int | None:
    """1-based rank of the first result matching any relevant reference, or None."""
    for rank, hit in enumerate(results, start=1):
        if any(matches(hit, ref, doc_only=doc_only) for ref in relevant):
            return rank
    return None


@dataclass(frozen=True)
class Outcome:
    question_id: str
    category: Category
    section_rank: int | None  # rank of the first relevant SECTION
    doc_rank: int | None  # rank of the first chunk of a relevant DOCUMENT


@dataclass(frozen=True)
class Scores:
    hit_at_k: float  # share of questions with a relevant section in the top k
    doc_hit_at_k: float  # share with a relevant document in the top k
    mrr: float  # mean of 1/section_rank (0 for a miss): rewards ranking the hit HIGH


def score(outcomes: list[Outcome], k: int) -> Scores:
    if not outcomes:
        raise ValueError("no outcomes to score")
    n = len(outcomes)
    return Scores(
        hit_at_k=sum(o.section_rank is not None and o.section_rank <= k for o in outcomes) / n,
        doc_hit_at_k=sum(o.doc_rank is not None and o.doc_rank <= k for o in outcomes) / n,
        mrr=sum(1 / o.section_rank for o in outcomes if o.section_rank) / n,
    )


def report(results: dict[str, list[Outcome]], k: int) -> str:
    """Markdown: overall table, per-category hit@k, and a per-question matrix (rank or '-')."""
    variants = list(results)
    lines = [
        f"| variant | hit@{k} | doc-hit@{k} | MRR |",
        "|---|---|---|---|",
    ]
    for v in variants:
        s = score(results[v], k)
        lines.append(f"| {v} | {s.hit_at_k:.0%} | {s.doc_hit_at_k:.0%} | {s.mrr:.2f} |")

    categories = list(dict.fromkeys(o.category for o in results[variants[0]]))
    lines += ["", f"hit@{k} per category:", "", "| category | n | " + " | ".join(variants) + " |"]
    lines.append("|---|---|" + "---|" * len(variants))
    for cat in categories:
        cells = []
        for v in variants:
            subset = [o for o in results[v] if o.category is cat]
            cells.append(f"{score(subset, k).hit_at_k:.0%}")
        n = sum(o.category is cat for o in results[variants[0]])
        lines.append(f"| {cat.value} | {n} | " + " | ".join(cells) + " |")

    lines += ["", "Rank of the first relevant section (- = not in the top k):", ""]
    lines.append("| question | category | " + " | ".join(variants) + " |")
    lines.append("|---|---|" + "---|" * len(variants))
    for i, o in enumerate(results[variants[0]]):
        ranks = [results[v][i].section_rank for v in variants]
        cells = [str(r) if r is not None and r <= k else "-" for r in ranks]
        lines.append(f"| {o.question_id} | {o.category.value} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
