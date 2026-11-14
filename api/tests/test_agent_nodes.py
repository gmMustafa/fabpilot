"""Agent nodes with FakeTools and ScriptedChat: no processes, no database, no LLM requests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agent.nodes import (
    ALARM_LIMIT,
    MAX_FOLLOW_UPS,
    doc_query,
    investigate,
    route_after_investigate,
    rule_intent,
    triage,
)
from app.agent.state import AgentState, Evidence, Intent
from app.agent.tools import FakeTools, ToolCallError
from app.testing import ScriptedChat

DATA_END = "2026-11-13T11:50:40Z"
NOW = datetime(2026, 11, 14, 13, 0, tzinfo=UTC)

MACHINE = {"code": "M-08", "type": "compressor", "last_reading_at": DATA_END, "sensors": []}
ALARMS = {
    "total_count": 2,
    "truncated": False,
    "alarms": [{"code": "TEMP-CRIT"}, {"code": "TEMP-HIGH"}],
}
ANOMALIES = {
    "findings": [{"sensor": "discharge_temp", "kind": "trend", "isolated": True}],
    "quiet_sensors": ["motor_current"],
}


def fake_tools(overrides: dict[tuple[Any, str], Any] | None = None) -> FakeTools:
    answers: dict[tuple[Any, str], Any] = {
        ("sensors", "get_machine"): MACHINE,
        ("sensors", "get_alarm_log"): ALARMS,
        ("sensors", "detect_anomalies"): ANOMALIES,
        ("docs", "list_incident_reports"): {"total_count": 0, "truncated": False, "reports": []},
        ("docs", "search_docs"): {"result": [{"doc_id": "MAN-CMP-001", "section_ref": "§5.2"}]},
        ("sensors", "list_machines"): {
            "result": [
                {
                    "code": "M-08",
                    "name": "Screw Compressor",
                    "type": "compressor",
                    "line": "Line 3",
                },
                {"code": "M-09", "name": "Central Coolant Pump", "type": "pump", "line": "Line 3"},
            ]
        },
    }
    answers.update(overrides or {})
    return FakeTools(answers)


# ------------------------------------------------------------------ triage


@pytest.mark.parametrize(
    ("question", "intent"),
    [  # the eight protocol questions (evals/mcp_sanity/protocol.md)
        ("M-08 has a discharge temperature alarm. Is the compressor overheating?", Intent.DIAGNOSE),
        ("What's wrong with M-01's spindle?", Intent.DIAGNOSE),
        ("Is M-05 OK?", Intent.STATUS),
        ("How many past incidents on M-09 were false alarms?", Intent.HISTORY),
        ("Has the M-08 discharge temperature sensor been a problem before?", Intent.HISTORY),
        (
            "I need to replace the inlet filter on M-08. Give me the full procedure",
            Intent.PROCEDURE,
        ),
    ],
)
def test_rule_intent(question: str, intent: Intent) -> None:
    assert rule_intent(question) is intent


async def test_triage_with_a_machine_code_makes_no_llm_request() -> None:
    chat = ScriptedChat()  # raises if called
    out = await triage(AgentState(question="Is M-05 OK?"), fake_tools(), chat)
    assert out == {"machine": "M-05", "intent": Intent.STATUS}


async def test_triage_falls_back_to_the_llm_and_counts_the_request() -> None:
    chat = ScriptedChat('{"machine": "M-09", "intent": "diagnose"}')
    state = AgentState(question="Why is the central coolant pump so loud?", llm_requests=0)
    out = await triage(state, fake_tools(), chat)
    assert (out["machine"], out["intent"], out["llm_requests"]) == ("M-09", Intent.DIAGNOSE, 1)
    assert "Central Coolant Pump" in chat.conversations[0][1].content  # the plant was shown


async def test_triage_never_trusts_a_made_up_machine_code() -> None:
    chat = ScriptedChat('{"machine": "M-77", "intent": "diagnose"}')
    out = await triage(AgentState(question="the big machine is weird"), fake_tools(), chat)
    assert out["machine"] is None


# ------------------------------------------------------------------ investigate


async def test_window_ends_at_the_data_not_at_now() -> None:
    """F2: a window counted back from now covered almost no data in Phase 4."""
    tools = fake_tools()
    out = await investigate(AgentState(question="q", machine="M-08"), tools, clock=lambda: NOW)
    assert out["data_ends_at"] == datetime(2026, 11, 13, 11, 50, 40, tzinfo=UTC)
    assert out["window_end"] == out["data_ends_at"] + timedelta(minutes=1)
    assert out["window_end"] - out["window_start"] == timedelta(days=3)
    alarm_args = next(a for s, t, a in tools.calls if t == "get_alarm_log")
    assert alarm_args["limit"] == ALARM_LIMIT and alarm_args["end"].startswith("2026-11-13T11:51")


async def test_fresh_data_window_ends_now() -> None:
    fresh = {**MACHINE, "last_reading_at": "2026-11-14T12:59:50Z"}
    out = await investigate(
        AgentState(question="q", machine="M-08"),
        fake_tools({("sensors", "get_machine"): fresh}),
        clock=lambda: NOW,
    )
    assert out["window_end"] == NOW


async def test_a_failing_tool_becomes_evidence_not_a_crash() -> None:
    tools = fake_tools(
        {("sensors", "detect_anomalies"): ToolCallError("detect_anomalies: no readings for M-08")}
    )
    out = await investigate(AgentState(question="q", machine="M-08"), tools, clock=lambda: NOW)
    assert out["evidence"].anomalies is None
    assert "no readings" in out["evidence"].errors[0]


async def test_procedure_questions_search_sops() -> None:
    tools = fake_tools()
    state = AgentState(question="replace the inlet filter", machine="M-08", intent=Intent.PROCEDURE)
    await investigate(state, tools, clock=lambda: NOW)
    search = next(a for s, t, a in tools.calls if t == "search_docs")
    assert search["source_types"] == ["sop"] and search["machine_type"] == "compressor"


def test_doc_query_uses_alarm_codes_and_findings() -> None:
    q = doc_query("Is it overheating?", Evidence(alarm_log=ALARMS, anomalies=ANOMALIES))
    assert "TEMP-CRIT" in q and "discharge_temp trend" in q and "other sensors normal" in q


# ------------------------------------------------------------------ route_after_investigate (TODO)


def state_with(**kw: Any) -> AgentState:
    base = {"question": "q", "machine": "M-08"}
    ev = kw.pop(
        "evidence", Evidence(machine=MACHINE, alarm_log=ALARMS, anomalies=ANOMALIES, docs=[{}])
    )
    return AgentState(**{**base, **kw}, evidence=ev)


def test_route_without_machine_is_insufficient() -> None:
    assert route_after_investigate(state_with(machine=None)) == "insufficient"


def test_route_when_get_machine_failed_is_insufficient() -> None:
    assert route_after_investigate(state_with(evidence=Evidence(errors=["boom"]))) == "insufficient"


def test_route_without_readings_is_insufficient() -> None:
    ev = Evidence(machine=MACHINE, alarm_log=ALARMS, anomalies=None)
    assert route_after_investigate(state_with(evidence=ev)) == "insufficient"


def test_route_with_a_gap_follows_up() -> None:
    ev = Evidence(machine=MACHINE, alarm_log={**ALARMS, "truncated": True}, anomalies=ANOMALIES)
    assert route_after_investigate(state_with(evidence=ev)) == "follow_up"


def test_route_stops_following_up_at_the_limit() -> None:
    ev = Evidence(machine=MACHINE, alarm_log={**ALARMS, "truncated": True}, anomalies=ANOMALIES)
    assert (
        route_after_investigate(state_with(evidence=ev, follow_ups=MAX_FOLLOW_UPS)) == "hypothesize"
    )


def test_route_with_complete_evidence_hypothesizes() -> None:
    assert route_after_investigate(state_with()) == "hypothesize"


def test_stale_data_alone_is_not_insufficient() -> None:
    """The past can still be diagnosed; the report must SAY the data's age (5.2b)."""
    old = {**MACHINE, "last_reading_at": "2026-01-01T00:00:00Z"}
    ev = Evidence(machine=old, alarm_log=ALARMS, anomalies=ANOMALIES, docs=[{}])
    assert route_after_investigate(state_with(evidence=ev)) == "hypothesize"
