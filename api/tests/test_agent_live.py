"""The agent against the REAL MCP servers (subprocesses) and the dev DB.

    uv run pytest -m embedding tests/test_agent_live.py   # real servers, no LLM (model load ~5 s)
    uv run pytest -m llm tests/test_agent_live.py         # + real LLM: spends ~1-3 free requests

Needs the seeded, simulated, ingested dev DB (README quickstart + app.rag.ingest).
"""

import pytest

from app.agent.graph import build_graph, run_question
from app.agent.guardrails import cited_doc_ids
from app.agent.tools import McpTools
from app.config import get_settings
from app.db.session import create_engine
from app.gateway.factory import build_gateway


@pytest.mark.embedding
async def test_mcp_tools_reach_both_real_servers() -> None:
    async with McpTools() as tools:
        machine = await tools.call("sensors", "get_machine", {"machine": "M-08"})
        incidents = await tools.call("docs", "list_incident_reports", {"machine": "M-09"})
        hits = await tools.call("docs", "search_docs", {"query": "PRESS-HIGH suction_strainer_dp"})
    assert machine["last_reading_at"] and machine["type"] == "compressor"
    assert incidents["total_count"] == 6
    assert hits["result"] and hits["result"][0]["citation"].startswith("[")


@pytest.mark.llm
async def test_q1_end_to_end_with_the_real_llm() -> None:
    """Protocol Q1 (M-08 drift). Asserts what must hold whatever the model writes: an answer
    that passed the guardrails, citing only retrieved documents, within the request budget."""
    settings = get_settings()
    engine = create_engine(settings)
    gateway = build_gateway(settings, engine=engine)
    try:
        async with McpTools() as tools:
            state = await run_question(
                build_graph(tools, gateway),
                "M-08 has a discharge temperature alarm. Is the compressor overheating?",
            )
    finally:
        await gateway.provider.aclose()
        await engine.dispose()
    assert state.answer and state.hypothesis is not None and not state.guard_errors
    retrieved = state.evidence.cited_ids()
    assert all(cited_doc_ids(c) <= retrieved for c in state.hypothesis.citations)
    assert state.llm_requests <= 3
