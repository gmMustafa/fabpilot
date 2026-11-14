"""The incident agent as a LangGraph graph (5.2b). The drawing: docs/agent-design.md §2.

    START -> triage -> investigate -> [route_after_investigate]
                          follow_up -> [route_after_investigate]   (max MAX_FOLLOW_UPS)
                          hypothesize -> guard -> [route_after_guard]
                                                    hypothesize   (max 1 retry)
                                                    draft_work_order -> report   (a fault to act on)
                                                    report                       (nothing to do)
                          insufficient -> END
    report -> [route_after_report]
                await_approval -> [route_after_approval]   (5.3, only with a store)
                                    await_approval   (sent back: ask again)
                                    save_work_order -> END
                END   (no draft, or no store: draft-only mode for tests and evals)

The engineer sees the full report BEFORE deciding: report runs first, then the run pauses.
Not yet: the Postgres checkpointer (5.4); 5.3 uses InMemorySaver (a pause lives in one process).

The nodes are plain functions; here they become closures over their dependencies (tools,
gateway, clock, store), so tests build the same graph with FakeTools and ScriptedChat.
"""

from collections.abc import Callable, Hashable
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent import approval, nodes, reasoning, work_order
from app.agent.approval import WorkOrderStore
from app.agent.state import AgentState, Approval
from app.agent.tools import Tools
from app.gateway.structured import ChatModel

Graph = CompiledStateGraph[AgentState, None, AgentState, AgentState]


def build_graph(
    tools: Tools,
    gateway: ChatModel,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    *,
    store: WorkOrderStore | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Graph:
    """With a `store`, a drafted work order PAUSES the run for a human decision (5.3); that
    needs a checkpointer to keep the paused state (default: in memory, one process)."""

    async def triage(state: AgentState) -> dict[str, Any]:
        return await nodes.triage(state, tools, gateway)

    async def investigate(state: AgentState) -> dict[str, Any]:
        return await nodes.investigate(state, tools, clock)

    async def follow_up(state: AgentState) -> dict[str, Any]:
        return await reasoning.follow_up(state, tools, gateway, clock)

    async def hypothesize(state: AgentState) -> dict[str, Any]:
        return await reasoning.hypothesize(state, gateway, clock)

    async def draft_work_order(state: AgentState) -> dict[str, Any]:
        return await work_order.draft_work_order(state, tools, gateway)

    def guard(state: AgentState) -> dict[str, Any]:
        return reasoning.guard(state, clock)

    def report(state: AgentState) -> dict[str, Any]:
        return reasoning.report(state, clock)

    def insufficient(state: AgentState) -> dict[str, Any]:
        return reasoning.report_insufficient(state, clock)

    def route_after_report(state: AgentState) -> str:
        return "await_approval" if store is not None and state.work_order is not None else END

    g = StateGraph(AgentState)
    for name, fn in [
        ("triage", triage),
        ("investigate", investigate),
        ("follow_up", follow_up),
        ("hypothesize", hypothesize),
        ("guard", guard),
        ("draft_work_order", draft_work_order),
        ("report", report),
        ("insufficient", insufficient),
    ]:
        g.add_node(name, fn)

    after_evidence: dict[Hashable, str] = {  # dict is invariant: annotate what LangGraph wants
        "follow_up": "follow_up",
        "hypothesize": "hypothesize",
        "insufficient": "insufficient",
    }
    g.add_edge(START, "triage")
    g.add_edge("triage", "investigate")
    g.add_conditional_edges("investigate", nodes.route_after_investigate, after_evidence)
    g.add_conditional_edges("follow_up", nodes.route_after_investigate, after_evidence)
    g.add_edge("hypothesize", "guard")
    g.add_conditional_edges(
        "guard",
        reasoning.route_after_guard,
        {
            "hypothesize": "hypothesize",
            "draft_work_order": "draft_work_order",
            "report": "report",
            "insufficient": "insufficient",
        },
    )
    g.add_edge("draft_work_order", "report")
    g.add_edge("insufficient", END)

    if store is None:
        g.add_edge("report", END)
        return g.compile(checkpointer=checkpointer)

    async def save_work_order(state: AgentState) -> dict[str, Any]:
        return await approval.save_work_order(state, store, clock)

    g.add_node("await_approval", approval.await_approval)
    g.add_node("save_work_order", save_work_order)
    after_report: dict[Hashable, str] = {"await_approval": "await_approval", END: END}
    g.add_conditional_edges("report", route_after_report, after_report)
    after_decision: dict[Hashable, str] = {
        "await_approval": "await_approval",
        "save_work_order": "save_work_order",
    }
    g.add_conditional_edges("await_approval", approval.route_after_approval, after_decision)
    g.add_edge("save_work_order", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def thread(thread_id: str) -> RunnableConfig:
    """The run's name in the checkpointer: how a paused run is found again."""
    return {"configurable": {"thread_id": thread_id}}


async def run_question(graph: Graph, question: str, thread_id: str = "default") -> AgentState:
    """Run until the end OR until the run pauses for approval; return the state at that point."""
    out = await graph.ainvoke(AgentState(question=question), thread(thread_id))
    state = AgentState.model_validate(out)
    # Every path passes report or insufficient, both of which set `answer` (also before a pause);
    # if that ever breaks, fail HERE, loudly, instead of handing the engineer an empty reply.
    if state.answer is None:
        raise RuntimeError("the graph ended without an answer: a path misses report/insufficient")
    return state


async def pending_approval(graph: Graph, thread_id: str = "default") -> dict[str, Any] | None:
    """What the paused run is waiting for (the draft + why the last answer was sent back), or
    None if the run isn't paused."""
    snapshot = await graph.aget_state(thread(thread_id))
    return snapshot.interrupts[0].value if snapshot.interrupts else None


async def resume(
    graph: Graph, decision: Approval | dict[str, Any], thread_id: str = "default"
) -> AgentState:
    """Hand the human's decision to the paused run; it re-asks (still paused) or saves."""
    if await pending_approval(graph, thread_id) is None:
        # Resuming a run that isn't waiting would re-run nodes: refuse, never save twice.
        raise RuntimeError(f"run {thread_id!r} is not waiting for an approval")
    value = decision.model_dump(mode="json") if isinstance(decision, Approval) else decision
    return AgentState.model_validate(await graph.ainvoke(Command(resume=value), thread(thread_id)))
