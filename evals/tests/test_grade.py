"""Grading (8.3c): every verdict, citations, work orders, staleness. No LLM, no database."""

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_evals.grade import (
    citation_recall,
    cited_docs,
    diagnosis_verdict,
    grade,
    invalid_citations,
    keywords_match,
    said_category,
    work_order_ok,
)
from agent_evals.results import Result
from agent_evals.scenario import Expect, Scenario

WHEN = datetime(2026, 11, 10, 12, tzinfo=UTC)
BEARING = Expect.model_validate(
    {
        "category": "machine_fault",
        "root_cause_keywords": [["bearing"], ["wear", "worn", "degrad"]],
        "must_cite": ["MAN-CNC-001", "SOP-CNC-002"],
        "work_order": "required",
    }
)
CONTROL = Expect.model_validate({"category": ["no_fault", "unknown"], "work_order": "forbidden"})
MILD = BEARING.model_copy(update={"category": [*BEARING.category, "unknown"]})


def hyp(category: str, root_cause: str = "", citations: list[str] | None = None) -> dict[str, Any]:
    return {"category": category, "root_cause": root_cause, "citations": citations or []}


def result(**over: Any) -> Result:
    fields: dict[str, Any] = {
        "scenario_id": "bearing",
        "status": "paused",
        "thread_id": "t",
        "machine": "M-01",
        "question": "q",
        "wall_seconds": 1.0,
        "cache": False,
        "models": [],
        "eval_now": WHEN,
        "started_at": WHEN,
    }
    return Result(**(fields | over))


# ---------------------------------------------------------------- the verdict (your TODO)


def test_the_right_fault_with_the_right_cause_is_correct() -> None:
    h = hyp("machine_fault", "Spindle bearing WEAR: vibration rose for days")
    assert diagnosis_verdict(BEARING, h) == "correct"


def test_the_right_category_with_the_wrong_cause_is_wrong() -> None:
    assert (
        diagnosis_verdict(BEARING, hyp("machine_fault", "the coolant filter is clogged")) == "wrong"
    )


def test_a_category_that_isnt_expected_is_wrong() -> None:
    assert diagnosis_verdict(BEARING, hyp("sensor_fault", "bearing sensor worn")) == "wrong"
    assert diagnosis_verdict(CONTROL, hyp("machine_fault", "belt slipping")) == "wrong"


def test_unknown_is_wrong_unless_allowed_then_only_acceptable() -> None:
    assert diagnosis_verdict(BEARING, hyp("unknown")) == "wrong"
    assert diagnosis_verdict(MILD, hyp("unknown")) == "acceptable"


def test_no_hypothesis_counts_as_unknown() -> None:
    assert diagnosis_verdict(MILD, None) == "acceptable"
    assert diagnosis_verdict(BEARING, None) == "wrong"


def test_no_fault_on_a_control_is_correct() -> None:
    assert diagnosis_verdict(CONTROL, hyp("no_fault", "all sensors quiet")) == "correct"
    assert diagnosis_verdict(CONTROL, hyp("unknown")) == "acceptable"


# ---------------------------------------------------------------- the helpers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bearing degraded", True),  # stems match, any case
        ("bearing is fine", False),  # second group missing
        ("No bearing wear found", True),  # the known limit: negation still matches (8.4)
    ],
)
def test_keywords_match(text: str, expected: bool) -> None:
    assert keywords_match(text, [["bearing"], ["wear", "degrad"]]) is expected


def test_no_keyword_groups_means_nothing_to_check() -> None:
    assert keywords_match("anything", [])


def test_said_category() -> None:
    assert said_category(None) == "unknown"
    assert said_category(hyp("no_fault")) == "no_fault"


def test_citations_are_parsed_and_the_draft_sop_counts() -> None:
    r = result(
        hypothesis=hyp("machine_fault", citations=["[MAN-CNC-001 §5.1]", "INC-2026-002"]),
        work_order={"sop_ref": "SOP-CNC-002"},
        retrieved_docs=["MAN-CNC-001"],
    )
    assert cited_docs(r) == {"MAN-CNC-001", "INC-2026-002", "SOP-CNC-002"}
    assert citation_recall(r, BEARING) == 1.0
    assert citation_recall(r, CONTROL) is None  # nothing it must cite
    assert invalid_citations(r) == ["INC-2026-002"]  # cited, never retrieved


def test_half_the_documents_is_half_the_recall() -> None:
    r = result(hypothesis=hyp("machine_fault", citations=["[MAN-CNC-001 §5]"]))
    assert citation_recall(r, BEARING) == 0.5


def test_work_order_rules() -> None:
    with_draft, without = result(work_order={"sop_ref": "SOP-CNC-002"}), result()
    assert work_order_ok(with_draft, BEARING) and not work_order_ok(without, BEARING)
    assert work_order_ok(without, CONTROL) and not work_order_ok(with_draft, CONTROL)


# ---------------------------------------------------------------- grade()


def scenario(setup: dict[str, Any]) -> Scenario:
    return Scenario.model_validate(
        {
            "id": "bearing",
            "description": "a bearing scenario",
            "setup": setup,
            "question": "What is wrong with M-01?",
            "expect": BEARING.model_dump(mode="json"),
            "tags": ["typical"],
        }
    )


def test_an_errored_run_gets_no_verdict() -> None:
    g = grade(
        result(status="failed", error="boom"),
        scenario({"machine": "M-01", "fault": "bearing_wear"}),
    )
    assert g.errored and g.verdict is None and g.difficulty == "typical"


def test_a_changed_setup_marks_the_result_stale() -> None:
    s = scenario({"machine": "M-01", "fault": "bearing_wear"})
    same = result(setup=s.setup.model_dump(mode="json"))
    other = result(setup={**s.setup.model_dump(mode="json"), "severity": 0.5})
    assert not grade(same, s).stale and grade(other, s).stale
    assert not grade(result(), s).stale  # not recorded (older runs): can't tell
