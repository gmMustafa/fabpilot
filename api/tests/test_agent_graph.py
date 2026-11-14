"""The whole graph with FakeTools + ScriptedChat: every path, 0 LLM requests, no processes.

ScriptedChat fails on any request the test didn't script, so these tests also PROVE how many
requests each path makes (the ~50/day budget).
"""

import json
from datetime import UTC, datetime
from typing import Any

from app.agent.graph import build_graph, run_question
from app.agent.tools import FakeTools, ToolCallError
from app.gateway.types import OutputTruncatedError
from app.testing import ScriptedChat
from tests.test_agent_nodes import ALARMS, ANOMALIES, fake_tools

NOW = datetime(2026, 11, 14, 13, 0, tzinfo=UTC)
Q1 = "M-08 has a discharge temperature alarm. Is the compressor overheating?"


def hypothesis_json(**kw: Any) -> str:
    h: dict[str, Any] = {
        "category": "sensor_fault",
        "root_cause": "The discharge_temp RTD is drifting; the compressor is not overheating.",
        "confidence": "medium",
        "evidence": [{"id": "F1", "statement": "only discharge_temp moved, isolated"}],
        "citations": ["[MAN-CMP-001 §5.2]"],
        "alternatives_ruled_out": [{"cause": "clogged inlet filter", "why": "filter dp quiet"}],
        "needs_action": True,
        "next_check": "Compare with the mechanical thermometer (SOP-GEN-004).",
    }
    return json.dumps({**h, **kw})


TOOLS_WITH_CITATIONS = {
    ("docs", "search_docs"): {
        "result": [
            {
                "doc_id": "MAN-CMP-001",
                "section_ref": "§5.2",
                "citation": "[MAN-CMP-001 §5.2]",
                "title": "SC-55 manual",
                "text": "discharge_temp climbs while other signals are normal: sensor drift",
            }
        ]
    }
}


async def run(chat: ScriptedChat, tools: FakeTools, question: str = Q1) -> Any:
    return await run_question(build_graph(tools, chat, clock=lambda: NOW), question)


async def test_happy_path_one_request_and_a_code_written_answer() -> None:
    chat = ScriptedChat(hypothesis_json())
    state = await run(chat, fake_tools(TOOLS_WITH_CITATIONS))
    assert state.llm_requests == 1  # triage needed none: "M-08" is in the question
    assert state.answer.startswith("Most likely a FAULTY SENSOR")
    assert "1 d 1 h 9 min ago" in state.answer  # the data-age caveat, computed by code
    assert "Sources: [MAN-CMP-001 §5.2]" in state.answer


async def test_a_rejected_hypothesis_gets_one_retry_with_the_errors() -> None:
    invented = hypothesis_json(citations=["[SOP-CMP-099 §4]"])
    chat = ScriptedChat(invented, hypothesis_json())
    state = await run(chat, fake_tools(TOOLS_WITH_CITATIONS))
    assert state.llm_requests == 2 and state.answer.startswith("Most likely")
    retry_prompt = chat.conversations[1][1].content
    assert "REJECTED" in retry_prompt and "SOP-CMP-099" in retry_prompt


async def test_two_rejections_end_insufficient_instead_of_guessing() -> None:
    invented = hypothesis_json(citations=["[SOP-CMP-099 §4]"])
    chat = ScriptedChat(invented, invented)
    state = await run(chat, fake_tools(TOOLS_WITH_CITATIONS))
    assert state.answer.startswith("I can't give a reliable answer")
    assert "SOP-CMP-099" in state.answer and state.llm_requests == 2


async def test_no_readings_end_insufficient_without_any_request() -> None:
    chat = ScriptedChat()  # any request would fail the test
    no_data = ToolCallError("detect_anomalies: no readings for M-08 in this window")
    state = await run(chat, fake_tools({("sensors", "detect_anomalies"): no_data}))
    assert state.llm_requests == 0
    assert "no sensor readings in the investigation window" in state.answer


async def test_a_cut_alarm_log_triggers_one_follow_up_that_replaces_it() -> None:
    cut = {**ALARMS, "truncated": True, "total_count": 300}
    full = {**ALARMS, "truncated": False}
    choice = json.dumps(
        {"tool": "get_alarm_log", "args": {"machine": "M-08", "limit": 200}, "why": "onset"}
    )
    chat = ScriptedChat(choice, hypothesis_json())

    calls = {"n": 0}

    def alarm_log(args: Any) -> dict[str, Any]:
        calls["n"] += 1
        return cut if calls["n"] == 1 else full  # the follow-up gets the complete log

    tools = fake_tools({**TOOLS_WITH_CITATIONS, ("sensors", "get_alarm_log"): alarm_log})
    state = await run(chat, tools)
    assert state.follow_ups == 1 and state.llm_requests == 2
    assert state.evidence.alarm_log == full  # replaced, so the gap is closed: no 2nd follow-up
    assert state.evidence.anomalies == ANOMALIES


async def test_a_failing_llm_ends_insufficient_not_in_a_traceback() -> None:
    """Live run 5.2b: a reasoning model used all its tokens thinking, and the whole run crashed.
    Free models fail often; the engineer must still get an answer."""
    chat = ScriptedChat(OutputTruncatedError("no answer within max_tokens=1500"))
    state = await run(chat, fake_tools(TOOLS_WITH_CITATIONS))
    assert state.answer.startswith("I can't give a reliable answer: the language model failed")
    assert "max_tokens" in state.answer and state.llm_requests == 1
    assert state.evidence.anomalies is not None  # the evidence was collected anyway
