"""The CI gate (8.5): the spec for gate.check (TODO(Mustafa)). Pure: no files, no LLM."""

from typing import Any

from agent_evals.gate import Thresholds, check
from agent_evals.summary import Counts, Summary

T = Thresholds()  # accuracy >= 80 %, completion >= 80 %, no false alarm, no drift blamed


def make(**over: Any) -> Summary:
    """A passing smoke run (7 scenarios, all correct), with `over` changed."""
    fields: dict[str, Any] = {
        "name": "t",
        "eval_now": None,
        "suite_size": 51,
        "totals": Counts(n=7, correct=7),
        "accuracy": 1.0,
        "completion": 1.0,
        "controls": 1,
        "invented": 0,
        "drifts": 1,
        "blamed": 0,
        "citation_recall": 1.0,
        "citations_valid": 1.0,
        "work_order_ok": 1.0,
        "tools_ok": 1.0,
        "by_fault": {},
        "diagnosed_by": {},
        "requests": 20,
        "tokens": 36000,
        "median_seconds": 23.0,
        "stale": 0,
        "judge": None,
    }
    return Summary(**(fields | over))


def test_a_good_run_passes() -> None:
    assert check(make(), T) == []


def test_nothing_ran_is_infrastructure_only() -> None:
    failures = check(make(totals=Counts(), accuracy=None, completion=None), T)
    assert len(failures) == 1 and failures[0].startswith("infrastructure:")


def test_low_completion_is_infrastructure_not_regression() -> None:
    # 2 of 7 completed, both correct: the agent isn't worse, the quota ran out
    s = make(totals=Counts(n=7, correct=2, errored=5), accuracy=1.0, completion=2 / 7)
    failures = check(s, T)
    assert len(failures) == 1 and failures[0].startswith("infrastructure:")


def test_nothing_completed_fails_once() -> None:
    s = make(totals=Counts(n=7, errored=7), accuracy=None, completion=0.0)
    assert [f.split(":")[0] for f in check(s, T)] == ["infrastructure"]


def test_low_accuracy_is_a_regression_with_numbers() -> None:
    s = make(totals=Counts(n=7, correct=5, wrong=2), accuracy=5 / 7)
    failures = check(s, T)
    assert len(failures) == 1 and failures[0].startswith("regression:")
    assert "71" in failures[0]  # the number is in the message, not just "failed"


def test_one_false_alarm_fails_even_when_accuracy_is_fine() -> None:
    s = make(controls=9, invented=1, totals=Counts(n=51, correct=48, wrong=3), accuracy=48 / 51)
    failures = check(s, T)
    assert len(failures) == 1 and failures[0].startswith("regression:")


def test_drift_threshold_is_configurable() -> None:
    s = make(drifts=11, blamed=2)  # the Groq baseline: 2/11 = 18 %
    assert len(check(s, T)) == 1
    assert check(s, Thresholds(max_drift_blamed=0.2)) == []


def test_no_controls_or_drifts_cannot_break_their_rules() -> None:
    assert check(make(controls=0, invented=0, drifts=0, blamed=0), T) == []


def test_stale_results_fail() -> None:
    failures = check(make(stale=2), T)
    assert len(failures) == 1 and failures[0].startswith("regression:")


def test_every_broken_rule_is_reported() -> None:
    s = make(totals=Counts(n=7, correct=4, wrong=3), accuracy=4 / 7, invented=1, blamed=1)
    assert len(check(s, T)) == 3  # accuracy, false alarm, drift: all of them, not the first
