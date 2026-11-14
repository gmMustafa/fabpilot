"""sensor-mcp through a REAL MCP client: tool list, schemas, results, errors, stdio subprocess.

The in-process Client speaks the same protocol as Claude Desktop or our agent, so these tests see
exactly what an LLM sees: tool names, JSON schemas, structured results, error texts.
"""

import os
import re
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from mcp.server import MCPServer
from mcp.types import CallToolResult, ListToolsResult
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.plant import load_plant
from app.seed import seed
from sensor_mcp.server import create_server

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 16, 8, tzinfo=UTC)  # readings 08:00-10:00 UTC, every 10 s
TOOLS = {"list_machines", "get_machine", "get_readings", "get_alarm_log"}


@pytest.fixture
async def db_url(migrated_db_url: str) -> AsyncIterator[str]:
    """A migrated, seeded DB: M-01 spindle_bearing_temp readings and five alarms on two machines."""
    eng = create_async_engine(migrated_db_url)
    await seed(eng, load_plant(get_settings().plant_file))
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO sensor_readings (sensor_id, time, value)"
                " SELECT s.id, t, 40.0 FROM sensors s JOIN machines m ON m.id = s.machine_id,"
                "  generate_series(:t0, :t0 + interval '2 hours' - interval '10 seconds',"
                "                  interval '10 seconds') AS t"
                " WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            ),
            {"t0": T0},
        )
        await conn.execute(
            text(
                "INSERT INTO alarms (machine_id, sensor_id, time, code, severity, message)"
                " SELECT m.id, s.id, :t0 + (n || ' minutes')::interval, a.code, a.sev, a.msg"
                " FROM (VALUES"
                "   (1, 'M-01', 'spindle_vibration', 'VIB-HIGH', 'warning', 'vib 2.9'),"
                "   (2, 'M-01', 'spindle_vibration', 'VIB-CRIT', 'critical', 'vib 3.5'),"
                "   (3, 'M-01', NULL, 'TRIP', 'critical', 'tripped'),"
                "   (4, 'M-08', 'discharge_temp', 'TEMP-HIGH', 'warning', 'temp 96'),"
                "   (5, 'M-08', 'inlet_filter_dp', 'PRESS-HIGH', 'warning', 'dp 52')"
                " ) AS a(n, machine, sensor, code, sev, msg)"
                " JOIN machines m ON m.code = a.machine"
                " LEFT JOIN sensors s ON s.machine_id = m.id AND s.name = a.sensor"
            ),
            {"t0": T0},
        )
    await eng.dispose()
    yield migrated_db_url


class OneShotClient:
    """Opens a fresh MCP connection per call, INSIDE the calling test's task.

    Why not `async with Client(server) as c: yield c` in a fixture: anyio cancel scopes must be
    exited in the task that entered them, and pytest-asyncio runs fixture setup and teardown in
    different tasks ("Attempted to exit cancel scope in a different task"). Per-call connections
    cost a few ms (the server's lifespan runs each time) and keep every test self-contained.
    """

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
    return OneShotClient(create_server(lambda: create_async_engine(db_url)))


async def _call(client: OneShotClient, tool: str, args: dict[str, Any]) -> Any:
    r = await client.call_tool(tool, args)
    assert not r.is_error, [c.text for c in r.content if hasattr(c, "text")]
    return r.structured_content


async def _error(client: OneShotClient, tool: str, args: dict[str, Any]) -> str:
    r = await client.call_tool(tool, args)
    assert r.is_error
    return " ".join(c.text for c in r.content if hasattr(c, "text"))


# ------------------------------------------------------------------ what the model sees


async def test_tools_are_listed_read_only_and_described(client: OneShotClient) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.annotations is not None and t.annotations.read_only_hint, t.name
        assert t.description and len(t.description) > 40, t.name  # the model decides from this
        assert "ctx" not in t.input_schema.get("properties", {}), t.name  # internal, not an arg
        assert t.output_schema is not None, t.name  # structured results


async def test_parameters_carry_descriptions_for_the_model(client: OneShotClient) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    props = tools["get_readings"].input_schema["properties"]
    assert {"machine", "sensor", "start", "end", "bucket"} <= set(props)
    assert all("description" in p for p in props.values())
    assert tools["get_readings"].input_schema["required"] == ["machine", "sensor"]


# ------------------------------------------------------------------ results


async def test_list_machines_and_filter_by_line(client: OneShotClient) -> None:
    every = (await _call(client, "list_machines", {}))["result"]
    line3 = (await _call(client, "list_machines", {"line": "Line 3"}))["result"]
    assert len(every) == 10
    assert [m["code"] for m in line3] == ["M-08", "M-09", "M-10"]


async def test_get_machine_lists_sensors_with_normal_ranges(client: OneShotClient) -> None:
    m = await _call(client, "get_machine", {"machine": "M-09"})
    names = {s["name"] for s in m["sensors"]}
    assert "suction_strainer_dp" in names
    dp = next(s for s in m["sensors"] if s["name"] == "suction_strainer_dp")
    assert (dp["normal_min"], dp["normal_max"]) == (0.05, 0.3)


async def test_get_readings_aggregates(client: OneShotClient) -> None:
    r = await _call(
        client,
        "get_readings",
        {
            "machine": "M-01",
            "sensor": "spindle_bearing_temp",
            "start": "2026-11-16T08:00:00Z",
            "end": "2026-11-16T10:00:00Z",
            "bucket": "1h",
        },
    )
    assert r["bucket"] == "1h" and r["unit"] == "°C"
    assert [p["count"] for p in r["points"]] == [360, 360]  # 2 h of 10-s readings


async def test_alarm_log_plant_wide_newest_first(client: OneShotClient) -> None:
    args = {"start": "2026-11-16T08:00:00Z", "end": "2026-11-16T09:00:00Z"}
    alarms = (await _call(client, "get_alarm_log", args))["result"]
    assert [a["code"] for a in alarms] == [
        "PRESS-HIGH",
        "TEMP-HIGH",
        "TRIP",
        "VIB-CRIT",
        "VIB-HIGH",
    ]
    trip = next(a for a in alarms if a["code"] == "TRIP")
    assert trip["sensor"] is None and trip["machine"] == "M-01"  # machine-level alarm


async def test_alarm_log_filters(client: OneShotClient) -> None:
    base = {"start": "2026-11-16T08:00:00Z", "end": "2026-11-16T09:00:00Z"}
    m01 = (await _call(client, "get_alarm_log", {**base, "machine": "M-01"}))["result"]
    crit = (await _call(client, "get_alarm_log", {**base, "min_severity": "CRITICAL"}))["result"]
    two = (await _call(client, "get_alarm_log", {**base, "limit": 2}))["result"]
    assert {a["machine"] for a in m01} == {"M-01"} and len(m01) == 3
    assert {a["code"] for a in crit} == {"VIB-CRIT", "TRIP"}
    assert [a["code"] for a in two] == ["PRESS-HIGH", "TEMP-HIGH"]


# ------------------------------------------------------------------ errors the model can fix


async def test_unknown_machine_and_sensor_say_what_exists(client: OneShotClient) -> None:
    msg = await _error(client, "get_machine", {"machine": "M-99"})
    assert "M-99" in msg and "not found" in msg
    msg = await _error(client, "get_readings", {"machine": "M-09", "sensor": "spindle_speed"})
    assert "suction_strainer_dp" in msg  # lists the sensors that DO exist: one retry fixes it


async def test_schema_rejects_malformed_codes_before_any_query(client: OneShotClient) -> None:
    assert "pattern" in (await _error(client, "get_machine", {"machine": "pump nine"})).lower()


@pytest.mark.parametrize(
    ("args", "hint"),
    [
        ({"start": "2026-11-16T08:00:00"}, "timezone"),
        ({"start": "2026-11-16T09:00:00Z", "end": "2026-11-16T08:00:00Z"}, "after"),
        ({"start": "2026-01-01T00:00:00Z", "end": "2026-11-16T00:00:00Z"}, "31"),
        ({"limit": 0}, "200"),
        ({"limit": 5000}, "200"),
        ({"min_severity": "urgent"}, "critical"),
    ],
)
async def test_bad_alarm_queries_explain_how_to_fix_them(
    client: OneShotClient, args: dict[str, Any], hint: str
) -> None:
    """Your TODO (validate_alarm_query): each message must contain the fixing hint."""
    assert hint in await _error(client, "get_alarm_log", args)


# ------------------------------------------------------------------ the ground-truth wall


def test_no_code_path_reads_the_sim_schema() -> None:
    """sim.* holds the injected faults = the evals' answer key. The agent's tools must never see
    it. A static check now; in Phase 10 a DB role without grants on `sim` enforces it too."""
    import app.plant_data
    import sensor_mcp.server

    for module in (app.plant_data, sensor_mcp.server):
        source = Path(module.__file__ or "").read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert not re.search(r"\bsim\.|fault_injections|FaultInjection", code), module.__name__


# ------------------------------------------------------------------ the real entry point


async def test_stdio_subprocess_serves_the_tools(db_url: str) -> None:
    """The actual `sensor-mcp` process over stdio, as Claude Desktop starts it. Catches what
    in-process tests can't: a print() on stdout, the Windows event loop, the entry point."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sensor_mcp.server"],
        env={**os.environ, "DATABASE_URL": db_url},
    )
    async with Client(params) as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS
        r = await c.call_tool("list_machines", {"line": "Line 1"})
        assert not r.is_error and len(r.structured_content["result"]) == 4
