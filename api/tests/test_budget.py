"""The run budget (5.4b): refuse() (your TODO), the meter, and a run that hits its limits."""

import pytest
from langgraph.graph import END, START, StateGraph

from app.agent.budget import RunBudget, usage_line
from app.agent.graph import RECURSION_LIMIT, build_graph, run_question
from app.agent.state import AgentState
from app.gateway.structured import Spend, StructuredOutputError, chat_structured
from app.gateway.types import AllModelsFailedError, Attempt, ChatMessage, RateLimitedError
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, hypothesis_json
from tests.test_structured import Out
from tests.test_work_order import draft, tools_with_sop

BUDGET = RunBudget(max_attempts=12, max_tokens=40_000, per_call=4)


# ------------------------------------------------------------------ refuse (your TODO)


def test_a_fresh_run_may_call() -> None:
    assert BUDGET.refuse(0, 0) is None


def test_exactly_one_worst_case_call_left_is_still_allowed() -> None:
    """8 used, 4 left = per_call: the worst case still fits, so the call is allowed."""
    assert BUDGET.refuse(8, 0) is None


def test_less_than_one_worst_case_call_left_is_refused() -> None:
    assert BUDGET.refuse(9, 0) == (
        "budget: 9 of 12 provider requests used, the next call may need up to 4"
    )


def test_the_token_limit() -> None:
    assert BUDGET.refuse(0, 39_999) is None
    assert BUDGET.refuse(0, 40_000) == "budget: 40000 of 40000 tokens used"


def test_both_reasons_at_once() -> None:
    assert BUDGET.refuse(12, 50_000) == (
        "budget: 12 of 12 provider requests used, the next call may need up to 4; "
        "budget: 50000 of 40000 tokens used"
    )


def test_a_budget_smaller_than_one_call_is_a_config_error() -> None:
    with pytest.raises(ValueError, match="can't cover one call"):
        RunBudget(max_attempts=3, per_call=4)


# ------------------------------------------------------------------ the meter


async def test_the_meter_counts_both_calls_of_a_repair_round() -> None:
    chat = ScriptedChat(
        ScriptedChat.Reply("not json", provider_requests=2, tokens=100),
        ScriptedChat.Reply('{"machine": "M-08", "score": 1}', provider_requests=1, tokens=50),
    )
    spend = Spend()
    await chat_structured(
        chat, [ChatMessage(role="user", content="q")], Out, purpose="t", spend=spend
    )
    assert (spend.calls, spend.attempts, spend.tokens) == (2, 3, 150)


async def test_the_meter_bills_a_failed_call_with_all_its_attempts() -> None:
    """4 attempts, all failed: before 5.4b the agent counted this as "1 request"."""
    failed = AllModelsFailedError("all models failed", [Attempt("m", 10, error="429")] * 4)
    spend = Spend()
    with pytest.raises(AllModelsFailedError):
        await chat_structured(
            ScriptedChat(failed),
            [ChatMessage(role="user", content="q")],
            Out,
            purpose="t",
            spend=spend,
        )
    assert (spend.calls, spend.attempts) == (1, 4)


async def test_a_failure_without_attempt_info_counts_as_one() -> None:
    spend = Spend()
    with pytest.raises(RateLimitedError):
        await chat_structured(
            ScriptedChat(RateLimitedError("429")),
            [ChatMessage(role="user", content="q")],
            Out,
            purpose="t",
            spend=spend,
        )
    assert spend.attempts == 1


async def test_the_meter_still_counts_when_the_repair_also_fails() -> None:
    chat = ScriptedChat(
        ScriptedChat.Reply("no", tokens=10), ScriptedChat.Reply("still no", tokens=10)
    )
    spend = Spend()
    with pytest.raises(StructuredOutputError):
        await chat_structured(
            chat, [ChatMessage(role="user", content="q")], Out, purpose="t", spend=spend
        )
    assert (spend.calls, spend.tokens) == (2, 20)


# ------------------------------------------------------------------ through the graph


async def test_a_normal_run_reports_calls_requests_and_tokens() -> None:
    chat = ScriptedChat(
        ScriptedChat.Reply(hypothesis_json(needs_action=True), tokens=3_200),
        ScriptedChat.Reply(draft().model_dump_json(), tokens=2_500),
    )
    state = await run_question(build_graph(tools_with_sop(), chat, clock=lambda: NOW), Q1)
    assert (state.llm_requests, state.llm_attempts, state.llm_tokens) == (2, 2, 5_700)
    assert state.answer is not None
    assert state.answer.endswith("(2 LLM call(s), 2 provider request(s), 5,700 tokens)")
    assert usage_line(state) in state.answer


async def test_no_room_for_the_draft_means_no_draft_call_but_a_diagnosis() -> None:
    """4 requests allowed, the hypothesis took 1: 3 left < 4 per call. The draft is refused
    BEFORE its call (ScriptedChat has no second answer: a call would fail the test)."""
    chat = ScriptedChat(hypothesis_json(needs_action=True))
    graph = build_graph(tools_with_sop(), chat, clock=lambda: NOW, budget=RunBudget(4, 40_000, 4))
    state = await run_question(graph, Q1)
    assert state.work_order is None and state.llm_attempts == 1
    assert state.work_order_errors == [
        "budget: 1 of 4 provider requests used, the next call may need up to 4"
    ]
    assert state.answer is not None and state.answer.startswith("Most likely a FAULTY SENSOR")


async def test_a_token_heavy_hypothesis_stops_the_draft() -> None:
    chat = ScriptedChat(ScriptedChat.Reply(hypothesis_json(needs_action=True), tokens=41_000))
    state = await run_question(build_graph(tools_with_sop(), chat, clock=lambda: NOW), Q1)
    assert state.work_order is None
    assert state.work_order_errors == ["budget: 41000 of 40000 tokens used"]


async def test_an_exhausted_budget_ends_as_insufficient_with_the_reason() -> None:
    """The run already spent its budget (e.g. a resumed run): no hypothesis call at all."""
    graph = build_graph(tools_with_sop(), ScriptedChat(), clock=lambda: NOW)
    tight = AgentState(question=Q1, llm_attempts=12)
    out = AgentState.model_validate(await graph.ainvoke(tight, {"recursion_limit": 20}))
    assert out.answer is not None
    assert "LLM budget is used up" in out.answer and "12 of 12 provider requests" in out.answer


async def test_a_routing_loop_is_cut_off_by_the_step_limit() -> None:
    """A router that never says END (a bug) must stop after RECURSION_LIMIT steps."""
    g = StateGraph(AgentState)
    g.add_node("spin", lambda state: {"follow_ups": state.follow_ups + 1})
    g.add_edge(START, "spin")
    g.add_conditional_edges("spin", lambda state: "spin", {"spin": "spin", END: END})
    with pytest.raises(RuntimeError, match=f"exceeded {RECURSION_LIMIT} steps"):
        await run_question(g.compile(), "loop")
