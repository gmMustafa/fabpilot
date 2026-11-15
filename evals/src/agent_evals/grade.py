"""Grade one eval result against its scenario (8.3c). Pure functions: no LLM, no database.

Grading reads the CURRENT scenario files, so changing a rule re-grades every old run for free.
If a scenario's SETUP changed since the run, its result answers a different question: it is
flagged `stale` (re-run it). A changed expectation only needs a re-grade.

Known limit (8.4 addresses it): keyword matching can't read negation. "No bearing wear found"
contains "bearing" and "wear", so it matches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from agent_evals.results import Result
from agent_evals.scenario import Expect, Scenario

Verdict = Literal["correct", "acceptable", "wrong"]
DIFFICULTIES = ("typical", "mild", "advanced")
# MAN-CNC-001, SOP-GEN-004, INC-2026-002: the document ids citations quote (corpus.py)
_DOC_ID = re.compile(r"\b(?:MAN|SOP|INC)-[A-Z0-9]+-\d{3}\b")


@dataclass(frozen=True)
class Grade:
    scenario_id: str
    errored: bool  # the agent didn't get to answer: never counted as wrong
    verdict: Verdict | None  # None when errored
    category: str  # what the agent said; "unknown" when it gave no hypothesis
    expected: list[str]
    root_cause: str
    citation_recall: float | None  # None: the scenario names no must_cite documents
    invalid_citations: list[str]  # cited but never retrieved = invented
    work_order_ok: bool
    tools_ok: bool
    stale: bool
    fault: str | None
    difficulty: str | None
    tags: list[str]


def keywords_match(text: str, groups: list[list[str]]) -> bool:
    """Every group matches, any word of a group counts; case-insensitive, by substring, so a
    stem like 'degrad' matches 'degraded'. No groups = nothing to check = True."""
    lowered = text.lower()
    return all(any(word in lowered for word in group) for group in groups)


def said_category(hypothesis: dict[str, Any] | None) -> str:
    """No hypothesis ('insufficient data') is an abstention: the same as answering unknown."""
    return str(hypothesis["category"]) if hypothesis else "unknown"


def diagnosis_verdict(expect: Expect, hypothesis: dict[str, Any] | None) -> Verdict:
    """The rule behind the headline number. `hypothesis` is the result's dict (category,
    root_cause, ...) or None.

    The headline accuracy counts only "correct": an allowed abstention is "acceptable", never
    "correct", so an agent can't look accurate by saying unknown.
    """
    category = said_category(hypothesis)

    # 1. Category is not among the expected categories.
    if category not in expect.category:
        return "wrong"

    # 2. Allowed abstention is acceptable, not correct.
    if category == "unknown":
        return "acceptable"

    # 3. Correctly identifying no fault.
    if category == "no_fault":
        return "correct"

    # 4. Fault category must also identify the correct root cause.
    # (hypothesis can't be None here: None was graded as "unknown" above)
    root_cause = str((hypothesis or {}).get("root_cause", ""))

    if keywords_match(root_cause, expect.root_cause_keywords):
        return "correct"

    return "wrong"


def cited_docs(result: Result) -> set[str]:
    """Document ids the answer cites: the hypothesis's citations, plus the draft's SOP (the
    repair procedure shows up in the work order, not in the diagnosis)."""
    citations = (result.hypothesis or {}).get("citations", [])
    ids = {m for c in citations for m in _DOC_ID.findall(str(c))}
    if result.work_order and result.work_order.get("sop_ref"):
        ids.add(str(result.work_order["sop_ref"]))
    return ids


def citation_recall(result: Result, expect: Expect) -> float | None:
    """Share of the documents the answer SHOULD cite that it does."""
    if not expect.must_cite:
        return None
    return len(set(expect.must_cite) & cited_docs(result)) / len(expect.must_cite)


def invalid_citations(result: Result) -> list[str]:
    """Hypothesis citations naming a document the run never retrieved (the guardrails should
    make this empty: measuring it checks that they do). The draft's SOP is read by the
    work-order step itself, so only the diagnosis's citations are checked."""
    citations = (result.hypothesis or {}).get("citations", [])
    named = {m for c in citations for m in _DOC_ID.findall(str(c))}
    return sorted(named - set(result.retrieved_docs))


def work_order_ok(result: Result, expect: Expect) -> bool:
    has_draft = result.work_order is not None
    return {"required": has_draft, "forbidden": not has_draft, "optional": True}[expect.work_order]


def grade(result: Result, scenario: Scenario) -> Grade:
    expect = scenario.expect
    setup_now = scenario.setup.model_dump(mode="json")
    errored = not result.completed
    return Grade(
        scenario_id=scenario.id,
        errored=errored,
        verdict=None if errored else diagnosis_verdict(expect, result.hypothesis),
        category=said_category(result.hypothesis),
        expected=[str(c) for c in expect.category],
        root_cause=str((result.hypothesis or {}).get("root_cause", "")),
        citation_recall=citation_recall(result, expect),
        invalid_citations=invalid_citations(result),
        work_order_ok=work_order_ok(result, expect),
        tools_ok=set(expect.tools) <= set(result.tools),
        stale=result.setup is not None and result.setup != setup_now,
        fault=str(scenario.setup.fault) if scenario.setup.fault else None,
        difficulty=next((t for t in scenario.tags if t in DIFFICULTIES), None),
        tags=list(scenario.tags),
    )
