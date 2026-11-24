"""compare.py (8.6d): McNemar, pairing, comparability. Pure functions, no database, no LLM."""

from typing import Any

import pytest

from agent_evals import compare
from agent_evals.grade import Grade
from agent_evals.manifest import Manifest
from agent_evals.summary import Counts, Summary

# ---------------------------------------------------------------- McNemar: TODO(Mustafa)


@pytest.mark.parametrize(
    ("b", "c", "p"),
    [
        (5, 1, 0.21875),  # 2 * (1 + 6) / 64: "5 vs 1" is NOT significant
        (6, 0, 0.03125),  # 2 * 1/64: all six discordant pairs one way: significant
        (10, 10, 1.0),  # perfectly balanced: the doubled tail exceeds 1, capped
        (0, 0, 1.0),  # no discordant pairs: no evidence of any difference
    ],
)
def test_mcnemar_exact_p(b: int, c: int, p: float) -> None:
    assert compare.mcnemar_exact_p(b, c) == pytest.approx(p)


def test_mcnemar_is_symmetric() -> None:
    assert compare.mcnemar_exact_p(7, 2) == pytest.approx(compare.mcnemar_exact_p(2, 7))


# ---------------------------------------------------------------- pairing


def g(sid: str, verdict: str | None, errored: bool = False) -> Grade:
    return Grade(
        scenario_id=sid,
        errored=errored,
        verdict=None if errored else verdict,  # type: ignore[arg-type]
        category="machine_fault",
        expected=["machine_fault"],
        root_cause="",
        citation_recall=None,
        invalid_citations=[],
        work_order_ok=True,
        tools_ok=True,
        stale=False,
        fault="bearing_wear",
        difficulty="typical",
        tags=[],
    )


def m(**over: Any) -> Manifest:
    fields: dict[str, Any] = {
        "routes": ("groq:openai/gpt-oss-120b@low",),
        "temperature": 0.0,
        "agent_hash": "aaa",
        "data_hash": "ddd",
        "git_sha": "1111",
        "git_dirty": False,
    }
    return Manifest(**(fields | over))


def run(
    name: str, grades: list[Grade], manifests: list[Manifest | None] | None = None
) -> compare.Run:
    s = Summary(
        name=name,
        eval_now=None,
        suite_size=len(grades),
        totals=Counts(n=len(grades)),
        accuracy=None,
        completion=None,
        controls=0,
        invented=0,
        drifts=0,
        blamed=0,
        citation_recall=None,
        citations_valid=None,
        work_order_ok=None,
        tools_ok=None,
        by_fault={},
        by_tag={},
        diagnosed_by={},
        requests=0,
        tokens=0,
        median_seconds=0.0,
        stale=0,
        judge=None,
    )
    return compare.Run(
        name=name,
        model=name,
        grades={x.scenario_id: x for x in grades},
        manifests=manifests if manifests is not None else [m()],
        summary=s,
    )


def test_pairing_counts_only_scenarios_both_completed() -> None:
    ref = run(
        "ref",
        [
            g("a", "correct"),
            g("b", "correct"),
            g("c", "wrong"),
            g("d", "correct"),
            g("e", "correct"),
        ],
    )
    other = run(
        "other",
        [
            g("a", "correct"),
            g("b", "wrong"),
            g("c", "correct"),
            g("d", None, errored=True),
            g("e", "acceptable"),
        ],
    )
    p = compare.pair(ref, other)
    assert p.n == 4  # d is errored on one side: not comparable, never "wrong"
    assert (p.both_right, p.ref_only, p.other_only, p.both_wrong) == (1, 2, 1, 0)
    assert p.ref_only_ids == ["b", "e"]  # "acceptable" is not correct, as in accuracy
    assert p.other_only_ids == ["c"]


# ---------------------------------------------------------------- comparability


def test_runs_that_differ_only_in_the_model_are_comparable() -> None:
    a = run("a", [], [m()])
    b = run("b", [], [m(routes=("groq:qwen/qwen3.8-27b@none",))])
    assert compare.comparability([a, b]) == ([], [])


def test_different_agent_code_or_data_is_invalid() -> None:
    problems, _ = compare.comparability(
        [run("a", [], [m()]), run("b", [], [m(agent_hash="bbb", data_hash="eee")])]
    )
    assert any("agent code" in p for p in problems) and any("data differs" in p for p in problems)


def test_the_baselines_temperature_label_is_corrected_visibly() -> None:
    """8.6b: rows recorded before 8.6a say 0.2, but every agent call ran at 0.0."""
    baseline = run("baseline", [], [m(temperature=0.2, data_hash=None)])
    candidate = run("candidate", [], [m(temperature=0.0)])
    problems, notes = compare.comparability([baseline, candidate])
    assert problems == []
    assert any("mislabel" in n and "baseline" in n for n in notes)
    assert any("data_hash not recorded" in n for n in notes)


def test_a_new_row_at_another_temperature_is_not_relabelled() -> None:
    """Only the documented legacy mislabel is corrected: a NEW 0.2 is a real difference."""
    problems, _ = compare.comparability([run("a", [], [m(temperature=0.2)]), run("b", [], [m()])])
    assert any("temperature differs" in p for p in problems)
