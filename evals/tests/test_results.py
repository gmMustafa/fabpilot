"""Eval results (8.3b): what a run records, and resuming. No LLM, no database."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_evals.results import (
    Result,
    append,
    done_ids,
    from_state,
    latest,
    read,
    tools_called,
)
from app.agent.state import AgentState, Evidence

WHEN = datetime(2026, 11, 10, 12, tzinfo=UTC)


def run_fields(scenario_id: str = "s1", **over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "scenario_id": scenario_id,
        "status": "done",
        "thread_id": "t1",
        "machine": "M-01",
        "question": "What is wrong?",
        "wall_seconds": 1.0,
        "cache": False,
        "models": ["model-a:free"],
        "eval_now": WHEN,
        "started_at": WHEN,
    }
    return fields | over


def full_state() -> AgentState:
    evidence = Evidence(
        machine={"code": "M-01"},
        alarm_log={"alarms": []},
        anomalies={"findings": []},
        incidents={"reports": [{"doc_id": "INC-CNC-001"}]},
        docs=[{"doc_id": "MAN-CNC-001"}],
    )
    return AgentState(
        question="q",
        evidence=evidence,
        sop_text="SOP text",
        follow_up_calls=[
            {"tool": "get_readings", "args": {"sensor": "x"}, "ok": True},
            {"tool": "get_alarm_log", "args": {}, "ok": False},  # failed: not "called"
        ],
        llm_requests=2,
        llm_attempts=3,
        llm_tokens=900,
    )


def test_tools_called_covers_every_evidence_field_and_follow_ups() -> None:
    assert tools_called(full_state()) == [
        "get_machine",
        "get_alarm_log",
        "detect_anomalies",
        "list_incident_reports",
        "search_docs",
        "get_document",
        "get_readings",
    ]
    assert tools_called(AgentState(question="q")) == []


def test_from_state_keeps_what_grading_needs() -> None:
    r = from_state(full_state(), **run_fields())
    assert r.retrieved_docs == ["INC-CNC-001", "MAN-CNC-001"]  # what a citation may name
    assert (r.usage.llm_calls, r.usage.provider_requests, r.usage.tokens) == (2, 3, 900)
    assert r.follow_up_calls[1]["ok"] is False  # the failed choice is still recorded
    assert r.completed


def test_a_crash_before_any_checkpoint_is_still_a_result() -> None:
    r = from_state(None, **run_fields(status="failed", thread_id=None, error="boom"))
    assert r.hypothesis is None and not r.completed


def test_results_round_trip_through_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    first = from_state(full_state(), **run_fields("a"))
    append(path, first)
    append(path, from_state(None, **run_fields("b", status="failed", error="429")))
    assert read(path)[0] == first
    assert [r.scenario_id for r in read(path)] == ["a", "b"]


def test_resume_skips_only_completed_scenarios(tmp_path: Path) -> None:
    path = tmp_path / "run.jsonl"
    append(path, Result(**run_fields("ok")))
    append(path, Result(**run_fields("crashed", status="failed", error="boom")))
    append(path, Result(**run_fields("quota", llm_error="429 on every model")))
    # retried and completed on resume: the NEWEST line decides
    append(path, Result(**run_fields("retried", status="failed", error="boom")))
    append(path, Result(**run_fields("retried")))
    assert done_ids(path) == {"ok", "retried"}
    assert latest(read(path))["retried"].status == "done"


def test_reading_a_missing_file_is_an_empty_run(tmp_path: Path) -> None:
    assert read(tmp_path / "none.jsonl") == [] and done_ids(tmp_path / "none.jsonl") == set()
