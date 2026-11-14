"""FabPilot sensor MCP server (4.1a): machines, sensor readings and alarms as agent tools.

    uv run sensor-mcp                       # stdio (Claude Desktop / Claude Code / our agent)
    uv run sensor-mcp --http --port 8101    # streamable HTTP, endpoint http://127.0.0.1:8101/mcp

An adapter, like the REST router: every query and every validation rule lives in app.plant_data.
This module only declares the tools (name, typed parameters, docstring = what the model reads)
and turns domain errors into ToolError, i.e. text the model can act on.

STDIO RULE: in stdio mode stdout IS the protocol channel (JSON-RPC). A single print() corrupts
it and the client disconnects. Logs go to stderr only.
"""

import argparse
import logging
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncEngine

from app import plant_data
from app.config import get_settings
from app.db.session import create_engine
from app.plant_data import Bucket, InvalidQueryError, NotFoundError
from app.schemas import AlarmOut, MachineDetail, MachineOut, ReadingsOut

logger = logging.getLogger("sensor_mcp")

INSTRUCTIONS = """\
Live and historical data of the FabPilot demo plant (10 machines, 3 lines; SYNTHETIC data).
Typical workflow: list_machines -> get_machine (sensors and their normal ranges) ->
get_alarm_log (what fired, when) -> get_readings (the signal around that time).
All times are ISO 8601 with a timezone (UTC recommended), ranges are [start, end).
"""

# Every tool here only reads, and only this plant's database.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)

MachineCode = Annotated[str, Field(description="Machine code, e.g. 'M-07'", pattern=r"^M-\d{2}$")]
Start = Annotated[
    datetime | None,
    Field(description="Range start, ISO 8601 with timezone. Default: end minus 24 h"),
]
End = Annotated[datetime | None, Field(description="Range end (exclusive). Default: now")]


@dataclass(frozen=True)
class AppState:
    engine: AsyncEngine


def create_server(make_engine: Callable[[], AsyncEngine] | None = None) -> MCPServer:
    """Build the server. Tests pass `make_engine` to point it at a throwaway database."""

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[AppState]:
        engine = make_engine() if make_engine else create_engine(get_settings())
        try:
            yield AppState(engine=engine)  # one pool for the server's lifetime, not per call
        finally:
            await engine.dispose()

    server = MCPServer("fabpilot-sensors", instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool(annotations=READ_ONLY)
    async def list_machines(
        ctx: Context[AppState],
        line: Annotated[str | None, Field(description="Only this line, e.g. 'Line 3'")] = None,
    ) -> list[MachineOut]:
        """List the plant's machines with code, name, line, type and current status.

        Start here to find a machine's code. Status is the live operating state
        (running / idle / stopped / down)."""
        async with ctx.request_context.lifespan_context.engine.connect() as conn:
            return await plant_data.list_machines(conn, line)

    @server.tool(annotations=READ_ONLY)
    async def get_machine(ctx: Context[AppState], machine: MachineCode) -> MachineDetail:
        """One machine with all its sensors: name, kind, unit and NORMAL operating range.

        Use the sensor names for get_readings, and the normal range to judge a value: a reading
        outside normal_min..normal_max while the machine runs is abnormal."""
        with _as_tool_errors():
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await plant_data.get_machine(conn, machine)

    @server.tool(annotations=READ_ONLY)
    async def get_readings(
        ctx: Context[AppState],
        machine: MachineCode,
        sensor: Annotated[str, Field(description="Sensor name from get_machine")],
        start: Start = None,
        end: End = None,
        bucket: Annotated[
            Bucket | None,
            Field(description="Aggregation size. Default: the finest that gives <= 500 points"),
        ] = None,
    ) -> ReadingsOut:
        """Readings of ONE sensor aggregated into time buckets (avg, min, max, count per bucket).

        min/max keep short spikes visible that the average hides. Missing buckets mean no data
        (machine off). Raw resolution is 10 s; long ranges are aggregated to at most 500 points."""
        with _as_tool_errors():
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await plant_data.get_readings(conn, machine, sensor, start, end, bucket)

    @server.tool(annotations=READ_ONLY)
    async def get_alarm_log(
        ctx: Context[AppState],
        machine: Annotated[
            str | None,
            Field(description="Machine code, e.g. 'M-07'. Omit for ALL machines (plant-wide)"),
        ] = None,
        start: Start = None,
        end: End = None,
        min_severity: Annotated[
            str | None, Field(description="'info', 'warning' or 'critical'. Default: all")
        ] = None,
        limit: Annotated[int, Field(description="Max alarms returned, newest first")] = 50,
    ) -> list[AlarmOut]:
        """Alarms the machine controllers raised in a time range, newest first.

        Each alarm has its code (e.g. VIB-HIGH, PRESS-LOW, TRIP), the sensor it came from (none
        for machine-level alarms like TRIP), and cleared_at (null = still active). Omit `machine`
        to see the whole plant: alarms on several machines at once point at a shared cause."""
        with _as_tool_errors():
            query = plant_data.validate_alarm_query(start, end, limit, min_severity)
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await plant_data.get_alarm_log(conn, machine, query)

    return server


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Domain errors -> ToolError: the client gets is_error=True with OUR message, which says how
    to fix the call. Anything else stays a generic failure (details only in the server log):
    internal errors are not the model's to fix, and their text may leak internals."""
    try:
        yield
    except (NotFoundError, InvalidQueryError) as e:
        raise ToolError(str(e)) from e


def main() -> None:
    parser = argparse.ArgumentParser(description="FabPilot sensor MCP server")
    parser.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8101)
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level="INFO", format="%(levelname)s %(message)s")

    # app.runtime.run = asyncio.run with the SelectorEventLoop: server.run() would use anyio's
    # default loop, which on Windows is the Proactor loop async psycopg can't use (0.3).
    from app.runtime import run

    server = create_server()
    if args.http:
        run(server.run_streamable_http_async(host=args.host, port=args.port))
    else:
        run(server.run_stdio_async())


if __name__ == "__main__":
    main()
