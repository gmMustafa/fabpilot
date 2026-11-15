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
    assert f"4 of {len(suite)} scenarios run" in text  # not a constant: the suite grows
    assert "| 3/4 (75 %) |" in text  # completion: 3 of the 4 known runs completed
    assert "invented a fault in 1/1 (100 %)" in text
    assert "blamed the MACHINE in 1/1 (100 %)" in text
    assert "gen-bearing-wear-cnc-mill-typical: RateLimitedError" in text
    assert "| b:free | 1 | 0 |" in text and "| a:free | 0 | 1 |" in text
    assert "no-such-scenario: no longer in the suite" in text


def test_report_of_an_empty_run(suite: dict[str, Scenario]) -> None:
    text = render("empty", [], suite)
    assert "# Eval report: empty" in text and "| 0 | 0 | 0 | 0 | 0 | - | - |" in text


def test_repeats_show_every_completed_verdict(suite: dict[str, Scenario]) -> None:
    right = {"category": "no_fault", "root_cause": "all quiet"}
    wrong = {"category": "machine_fault", "root_cause": "belt"}
    every = [
        result("control-m10", hypothesis=wrong),
        result("control-m10", status="failed", error="429"),  # not completed: not shown
        result("control-m10", hypothesis=right),
        result("sensor-drift-m08", hypothesis=wrong),  # run once: no row
    ]
    text = render("r", [every[2], every[3]], suite, every=every)
    assert "## Repeats" in text
    assert "| control-m10 | wrong (?), correct (?) | 1/2 (50 %) |" in text  # model not recorded
    assert "| sensor-drift-m08 |" not in text.split("## Repeats")[1].split("##")[0]


def test_repeats_name_the_model_and_warn_when_models_differ(suite: dict[str, Scenario]) -> None:
    def by(provider: str, model: str) -> list[dict[str, Any]]:
        return [
            {
                "purpose": "agent.hypothesize",
                "provider": provider,
                "model": model,
                "status": "ok",
                "error_type": None,
            }
        ]

    wrong = {"category": "machine_fault", "root_cause": "belt"}
    right = {"category": "no_fault", "root_cause": "all quiet"}
    every = [
        result("control-m10", hypothesis=wrong, llm_calls=by("openrouter", "nvidia/nemotron:free")),
        result("control-m10", hypothesis=right, llm_calls=by("groq", "openai/gpt-oss-120b")),
    ]
    text = render("r", [every[1]], suite, every=every)
    assert "wrong (openrouter:nemotron:free), correct (groq:gpt-oss-120b)" in text
    assert "answered by DIFFERENT models" in text
