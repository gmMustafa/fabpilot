"""The report (8.3c) over a small synthetic run. Only what doesn't depend on the verdict rule
(diagnosis_verdict is tested in test_grade.py), so this stays green while that's a TODO."""

from datetime import UTC, datetime
from typing import Any

import pytest

from agent_evals.report import render
from agent_evals.results import Result
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.config import get_settings
from app.plant import load_plant
from app.rag.corpus import load_corpus

WHEN = datetime(2026, 11, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def suite() -> dict[str, Scenario]:
    settings = get_settings()
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    return {s.id: s for s in load_scenarios(SCENARIOS, load_plant(settings.plant_file), doc_ids)}


def result(scenario_id: str, **over: Any) -> Result:
    fields: dict[str, Any] = {
        "scenario_id": scenario_id,
        "status": "done",
        "thread_id": "t",
        "machine": "M-01",
        "question": "q",
        "wall_seconds": 10.0,
        "cache": False,
        "models": [],
        "eval_now": WHEN,
        "started_at": WHEN,
    }
    return Result(**(fields | over))


def test_report_counts_false_alarms_errors_and_cost(suite: dict[str, Scenario]) -> None:
    calls = [
        {"purpose": "agent.hypothesize", "model": "a:free", "status": "error", "error_type": "429"},
        {"purpose": "agent.hypothesize", "model": "b:free", "status": "ok", "error_type": None},
    ]
    rs = [
        # a control where the agent invented a fault
        result("control-m10", hypothesis={"category": "machine_fault", "root_cause": "belt"}),
        # a drift where it blamed the machine
        result("sensor-drift-m08", hypothesis={"category": "machine_fault", "root_cause": "hot"}),
        # a run that crashed
        result("gen-bearing-wear-cnc-mill-typical", status="failed", error="RateLimitedError"),
        result("trap-overload-reset-m06", llm_calls=calls, usage={"provider_requests": 2}),
        result("no-such-scenario"),
    ]
    text = render("test-run", rs, suite)
    assert "4 of 48 scenarios run" in text
    assert "| 3/4 (75 %) |" in text  # completion: 3 of the 4 known runs completed
    assert "invented a fault in 1/1 (100 %)" in text
    assert "blamed the MACHINE in 1/1 (100 %)" in text
    assert "gen-bearing-wear-cnc-mill-typical: RateLimitedError" in text
    assert "| b:free | 1 | 0 |" in text and "| a:free | 0 | 1 |" in text
    assert "no-such-scenario: no longer in the suite" in text


def test_report_of_an_empty_run(suite: dict[str, Scenario]) -> None:
    text = render("empty", [], suite)
    assert "# Eval report: empty" in text and "| 0 | 0 | 0 | 0 | 0 | - | - |" in text
