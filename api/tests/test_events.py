"""step_event (your TODO): the UI's view of each step, built from REAL node outputs.

The updates come from an actual graph run (astream, stream_mode="updates") with the test fakes,
not from hand-written dicts: 5.5b's lesson, test a component with the input it really gets.
"""

import json
from typing import Any

import pytest

from app.agent.events import step_event
from app.agent.graph import build_graph, thread
from app.agent.state import AgentState
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, hypothesis_json
from tests.test_approval import FakeStore
from tests.test_work_order import draft, tools_with_sop


async def updates(chat: ScriptedChat) -> dict[str, dict[str, Any]]:
    """node -> its update, from a real Q1 run (the last update wins for repeated nodes)."""
    graph = build_graph(tools_with_sop(), chat, clock=lambda: NOW, store=FakeStore())
    seen: dict[str, dict[str, Any]] = {}
    async for update in graph.astream(AgentState(question=Q1), thread("t"), stream_mode="updates"):
        seen.update({node: u for node, u in update.items() if not node.startswith("__")})
    return seen


@pytest.fixture
async def q1() -> dict[str, dict[str, Any]]:
    return await updates(
        ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())
    )


async def test_triage(q1: dict[str, dict[str, Any]]) -> None:
    assert step_event("triage", q1["triage"]) == {"machine": "M-08", "intent": "diagnose"}


async def test_investigate_is_counts_not_the_evidence(q1: dict[str, dict[str, Any]]) -> None:
    event = step_event("investigate", q1["investigate"])
    assert event is not None
    assert set(event) == {"findings", "alarms", "docs", "incidents", "tool_errors", "data_ends_at"}
    assert all(isinstance(event[k], int) for k in set(event) - {"data_ends_at"})
    assert event["data_ends_at"] == q1["investigate"]["data_ends_at"].isoformat()


async def test_hypothesize(q1: dict[str, dict[str, Any]]) -> None:
    h = q1["hypothesize"]["hypothesis"]
    assert step_event("hypothesize", q1["hypothesize"]) == {
        "category": "sensor_fault",
        "confidence": h.confidence,
        "root_cause": h.root_cause,
    }


async def test_guard(q1: dict[str, dict[str, Any]]) -> None:
    assert step_event("guard", q1["guard"]) == {"passed": True, "errors": []}


async def test_draft(q1: dict[str, dict[str, Any]]) -> None:
    assert step_event("draft_work_order", q1["draft_work_order"]) == {
        "title": draft().title,
        "sop": "SOP-GEN-004",
        "warnings": [],
    }


@pytest.mark.parametrize("node", ["report", "await_approval", "save_work_order", "insufficient"])
async def test_nodes_with_their_own_events_produce_no_step(
    node: str, q1: dict[str, dict[str, Any]]
) -> None:
    assert step_event(node, q1.get(node, {"answer": "x"})) is None


async def test_no_event_leaks_the_sop_or_the_evidence(q1: dict[str, dict[str, Any]]) -> None:
    """The contract's point: small, and nothing the browser must not get."""
    for node, update in q1.items():
        text = json.dumps(step_event(node, update), default=str)
        assert len(text) < 600, node
        assert "⚠ STOP" not in text and "Tools and parts" not in text, node  # no SOP text


async def test_a_failed_hypothesis_and_a_refused_draft() -> None:
    from app.gateway.types import AllModelsFailedError

    failed = await updates(ScriptedChat(AllModelsFailedError("all models failed", [])))
    event = step_event("hypothesize", failed["hypothesize"])
    assert event is not None and event["failed"].startswith("hypothesize: all models failed")

    no_draft = {"sop_id": "SOP-GEN-004", "work_order_errors": ["budget: 41000 of 40000 tokens"]}
    assert step_event("draft_work_order", no_draft) == {"failed": ["budget: 41000 of 40000 tokens"]}
