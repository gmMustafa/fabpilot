"""docs-mcp through a real MCP client, on a small ingested corpus with the FakeEmbedder.

The fake embedder knows no meaning, so these tests check the plumbing (tools, schemas, filters,
reading, errors). Search QUALITY is measured in 3.4 with the real model.
"""

import os
import re
import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer
from mcp.types import CallToolResult, ListToolsResult
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.rag.corpus import load_corpus
from app.rag.ingest import ingest
from app.testing import FakeEmbedder
from docs_mcp.server import create_server

pytestmark = pytest.mark.integration

TOOLS = {"search_docs", "get_document", "get_incident_report", "list_incident_reports"}
DOCS = [
    "manuals/MAN-PMP-001.md",
    "manuals/MAN-CMP-001.md",
    "sops/SOP-PMP-006.md",
    "sops/SOP-GEN-004.md",
    "incidents/INC-2024-008.md",
]


@pytest.fixture
async def db_url(migrated_db_url: str, tmp_path: Path) -> AsyncIterator[str]:
    src = get_settings().docs_dir
    for rel in DOCS:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / rel, tmp_path / rel)
    eng = create_async_engine(migrated_db_url)
    await ingest(eng, load_corpus(tmp_path), FakeEmbedder(), max_tokens=350)
    await eng.dispose()
    yield migrated_db_url


class OneShotClient:
    """A fresh MCP connection per call, inside the test's own task (see sensor-mcp's tests:
    anyio cancel scopes can't span pytest-asyncio's setup and teardown tasks)."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server

    async def list_tools(self) -> ListToolsResult:
        async with Client(self._server) as c:
            return await c.list_tools()

    async def call_tool(self, name: str, args: dict[str, Any]) -> CallToolResult:
        async with Client(self._server) as c:
            return await c.call_tool(name, args)


@pytest.fixture
def client(db_url: str) -> OneShotClient:
    return OneShotClient(create_server(lambda: create_async_engine(db_url), FakeEmbedder))


async def _call(client: OneShotClient, tool: str, args: dict[str, Any]) -> Any:
    r = await client.call_tool(tool, args)
    assert not r.is_error, [c.text for c in r.content if hasattr(c, "text")]
    return r.structured_content


async def _error(client: OneShotClient, tool: str, args: dict[str, Any]) -> str:
    r = await client.call_tool(tool, args)
    assert r.is_error
    return " ".join(c.text for c in r.content if hasattr(c, "text"))


async def test_tools_are_listed_read_only_and_described(client: OneShotClient) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.annotations is not None and t.annotations.read_only_hint, t.name
        assert t.description and len(t.description) > 60, t.name
        assert "ctx" not in t.input_schema.get("properties", {}), t.name


async def test_search_returns_citable_hits(client: OneShotClient) -> None:
    hits = (await _call(client, "search_docs", {"query": "PRESS-HIGH suction_strainer_dp"}))[
        "result"
    ]
    assert 1 <= len(hits) <= 5
    assert all(
        re.fullmatch(r"\[[A-Z]{3}-[A-Z0-9]{3,4}-\d{3}( §[\d.]+)?\]", h["citation"]) for h in hits
    )


async def test_search_filters_by_machine_and_source_type(client: OneShotClient) -> None:
    args = {"query": "pressure filter sensor check", "machine_type": "compressor", "k": 10}
    docs = {h["doc_id"] for h in (await _call(client, "search_docs", args))["result"]}
    assert docs <= {"MAN-CMP-001", "SOP-GEN-004"}  # pump documents are filtered out
    sops = {"query": "strainer", "source_types": ["sop"], "k": 10}
    assert {h["doc_id"] for h in (await _call(client, "search_docs", sops))["result"]} <= {
        "SOP-PMP-006",
        "SOP-GEN-004",
    }


async def test_get_document_section_includes_its_subsections(client: OneShotClient) -> None:
    d = await _call(client, "get_document", {"doc_id": "MAN-PMP-001", "section": "§5"})
    assert d["section"] == "§5" and "§5.1" in d["sections"]
    for sub in ("## §5.1 ", "## §5.2 ", "## §5.3 ", "## §5.4 "):
        assert d["text"].count(sub) == 1, sub  # one heading per section, even if split
    assert "## §4 " not in d["text"]
    assert "MAN-PMP-001 ·" not in d["text"]  # context headers are dropped


async def test_get_whole_sop_has_every_step(client: OneShotClient) -> None:
    d = await _call(client, "get_document", {"doc_id": "SOP-PMP-006"})
    assert re.search(r"^10\. Cavitation check", d["text"], re.MULTILINE)  # the last step


async def test_incident_report_has_fields_and_sections(client: OneShotClient) -> None:
    r = await _call(client, "get_incident_report", {"doc_id": "INC-2024-008"})
    assert (r["machine"], r["fault"], r["false_alarm"]) == ("M-09", "clogged_filter", False)
    assert list(r["sections"]) == [
        "Summary",
        "Timeline",
        "Evidence",
        "Root cause",
        "Corrective actions",
        "Lessons learned",
    ]
    assert "welding beads" in r["root_cause"]  # the frontmatter field
    assert "pipe work" in r["sections"]["Root cause"]  # the narrative section


async def test_errors_say_how_to_fix_the_call(client: OneShotClient) -> None:
    msg = await _error(client, "get_document", {"doc_id": "MAN-PMP-001", "section": "§9"})
    assert "§5.1" in msg  # lists the sections that exist
    assert "not found" in await _error(client, "get_document", {"doc_id": "MAN-XYZ-001"})
    assert "get_document" in await _error(client, "get_incident_report", {"doc_id": "MAN-PMP-001"})
    assert "pattern" in (await _error(client, "get_document", {"doc_id": "pump manual"})).lower()


# ------------------------------------------------------------------ list_incident_reports (4.4b)


@pytest.fixture
async def full_client(migrated_db_url: str) -> OneShotClient:
    """The WHOLE corpus: listing is about completeness, a subset can't test that."""
    eng = create_async_engine(migrated_db_url)
    await ingest(eng, load_corpus(get_settings().docs_dir), FakeEmbedder(), max_tokens=350)
    await eng.dispose()
    return OneShotClient(create_server(lambda: create_async_engine(migrated_db_url), FakeEmbedder))


async def _list(client: OneShotClient, **args: Any) -> Any:
    return await _call(client, "list_incident_reports", args)


async def test_q5_false_alarms_on_m09_in_one_call(full_client: OneShotClient) -> None:
    """Protocol Q5: search needed three queries and luck (Run 3) or Grep (Run 2)."""
    every = await _list(full_client, machine="M-09")
    assert every["total_count"] == 6 and not every["truncated"]
    false = await _list(full_client, machine="M-09", false_alarm=True)
    assert [r["doc_id"] for r in false["reports"]] == ["INC-2025-012"]
    assert false["reports"][0]["fault"] == "sensor_drift"


async def test_fault_none_lists_causes_outside_the_six(full_client: OneShotClient) -> None:
    r = await _list(full_client, fault="none")
    assert {x["doc_id"] for x in r["reports"]} == {
        "INC-2023-007",
        "INC-2024-010",
        "INC-2025-002",
        "INC-2025-016",
        "INC-2026-006",
    }
    assert all(x["fault"] is None for x in r["reports"])


async def test_filters_combine(full_client: OneShotClient) -> None:
    r = await _list(full_client, machine_type="compressor", fault="clogged_filter")
    assert {x["doc_id"] for x in r["reports"]} == {"INC-2024-005", "INC-2025-009"}
    real = await _list(full_client, machine="M-08", false_alarm=False)
    assert "INC-2024-014" not in {
        x["doc_id"] for x in real["reports"]
    }  # that one was a false alarm


async def test_list_is_newest_first_and_says_when_cut(full_client: OneShotClient) -> None:
    r = await _list(full_client, limit=3)
    dates = [x["date"] for x in r["reports"]]
    assert dates == sorted(dates, reverse=True)
    assert r["truncated"] and r["total_count"] == 30


async def test_fault_values_are_in_the_schema(full_client: OneShotClient) -> None:
    """The model sees the valid fault values BEFORE calling (the F5 lesson)."""
    tools = {t.name: t for t in (await full_client.list_tools()).tools}
    schema = tools["list_incident_reports"].input_schema
    options = schema["properties"]["fault"]["anyOf"]
    assert {"const": "none", "type": "string"} in options
    # the FaultType values live in $defs (the property only $refs them), as a client resolves it
    assert "clogged_filter" in schema["$defs"]["FaultType"]["enum"]
    assert "clogged" in (await _error(full_client, "list_incident_reports", {"fault": "gremlins"}))


async def test_descriptions_say_when_to_switch_tools(client: OneShotClient) -> None:
    """F11 / F1: search_docs must point at get_document (procedures) and the list (counting)."""
    tools = {t.name: t for t in (await client.list_tools()).tools}
    desc = tools["search_docs"].description or ""
    assert "get_document" in desc and "list_incident_reports" in desc


def test_no_code_path_reads_the_sim_schema() -> None:
    import app.rag.library
    import app.rag.retrieval
    import docs_mcp.server

    for module in (app.rag.library, app.rag.retrieval, docs_mcp.server):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"\bsim\.|fault_injections|FaultInjection", code), module.__name__


@pytest.mark.embedding
async def test_stdio_subprocess_with_the_real_model() -> None:
    """The real `docs-mcp` process over stdio, real model, the ingested DEV database (like
    Claude Desktop runs it). Deselected by default: loads the ~130 MB model."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "docs_mcp.server"], env=dict(os.environ)
    )
    async with Client(params) as c:
        r = await c.call_tool("search_docs", {"query": "PRESS-HIGH on suction_strainer_dp"})
        assert not r.is_error
        assert "MAN-PMP-001" in {h["doc_id"] for h in r.structured_content["result"]}
