"""The incident agent as a LangGraph graph (5.2b). The drawing: docs/agent-design.md §2.

    START -> triage -> investigate -> [route_after_investigate]
                          follow_up -> [route_after_investigate]   (max MAX_FOLLOW_UPS)
                          hypothesize -> guard -> [route_after_guard]
                                                    hypothesize   (max 1 retry)
                                                    report -> END
                          insufficient -> END

Not yet: the work order + approval (5.2c / 5.3) and the Postgres checkpointer (5.4).

The nodes are plain functions; here they become closures over their dependencies (tools,
gateway, clock), so tests build the same graph with FakeTools and ScriptedChat.
"""

from collections.abc import Callable, Hashable
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent import nodes, reasoning
from app.agent.state import AgentState
from app.agent.tools import Tools
from app.gateway.structured import ChatModel


def build_graph(
    tools: Tools,
    gateway: ChatModel,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CompiledStateGraph[AgentState, None, AgentState, AgentState]:
    async def triage(state: AgentState) -> dict[str, Any]:
        return await nodes.triage(state, tools, gateway)

    async def investigate(state: AgentState) -> dict[str, Any]:
        return await nodes.investigate(state, tools, clock)

    async def follow_up(state: AgentState) -> dict[str, Any]:
        return await reasoning.follow_up(state, tools, gateway, clock)

    async def hypothesize(state: AgentState) -> dict[str, Any]:
        return await reasoning.hypothesize(state, gateway, clock)

    def guard(state: AgentState) -> dict[str, Any]:
        return reasoning.guard(state, clock)

    def report(state: AgentState) -> dict[str, Any]:
        return reasoning.report(state, clock)

    def insufficient(state: AgentState) -> dict[str, Any]:
        return reasoning.report_insufficient(state, clock)

    g = StateGraph(AgentState)
    for name, fn in [
        ("triage", triage),
        ("investigate", investigate),
        ("follow_up", follow_up),
        ("hypothesize", hypothesize),
        ("guard", guard),
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
        {"hypothesize": "hypothesize", "report": "report", "insufficient": "insufficient"},
    )
    g.add_edge("report", END)
    g.add_edge("insufficient", END)
    return g.compile()


async def run_question(
    graph: CompiledStateGraph[AgentState, None, AgentState, AgentState], question: str
) -> AgentState:
    """Run the graph to its end and return the final state, as a model again."""
    result = await graph.ainvoke(AgentState(question=question))
    return AgentState.model_validate(result)
