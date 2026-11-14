"""FabPilot docs MCP server (4.2): search and read manuals, SOPs and past incident reports.

    uv run docs-mcp                       # stdio
    uv run docs-mcp --http --port 8102    # streamable HTTP, endpoint http://127.0.0.1:8102/mcp

An adapter: retrieval lives in app.rag.retrieval (3.3), reading in app.rag.library. The pattern
for the model is search -> cite -> read: a search hit is one chunk; get_document returns the
whole section (an SOP procedure, a troubleshooting table) the hit belongs to.

The embedding model loads ONCE in the lifespan (~5 s, first run downloads ~130 MB); afterwards a
search costs milliseconds. STDIO RULE as in sensor-mcp: stdout is the protocol, logs go to stderr.
"""

import argparse
import logging
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.models import MachineType, SourceType
from app.db.session import create_engine
from app.errors import InvalidQueryError, NotFoundError
from app.rag import library
from app.rag.embeddings import Embedder, build_embedder
from app.rag.library import DocumentText, FaultFilter, IncidentList, IncidentReport
from app.rag.retrieval import Filters, RetrievedChunk, search

logger = logging.getLogger("docs_mcp")

INSTRUCTIONS = """\
Knowledge base of the FabPilot demo plant (SYNTHETIC documents): machine manuals (MAN-...),
standard operating procedures (SOP-...) and past incident reports (INC-...).
Workflow: search_docs -> cite hits as their `citation` (e.g. [MAN-PMP-001 §5.1]) ->
get_document(doc_id, section) for the full section when you need all of it (procedures!).
For "which / how many incidents" use list_incident_reports (complete), not search_docs (ranked).
Past incidents are analogies, not proof: the current machine data decides.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DocId = Annotated[
    str,
    Field(
        description="Document id, e.g. 'MAN-PMP-001', 'SOP-GEN-004', 'INC-2025-003'",
        pattern=r"^(MAN|SOP)-[A-Z]{3}-\d{3}$|^INC-\d{4}-\d{3}$",
    ),
]


@dataclass(frozen=True)
class AppState:
    engine: AsyncEngine
    embedder: Embedder


def create_server(
    make_engine: Callable[[], AsyncEngine] | None = None,
    make_embedder: Callable[[], Embedder] | None = None,
) -> MCPServer:
    """Build the server. Tests inject a throwaway DB and the deterministic FakeEmbedder."""

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[AppState]:
        settings = get_settings()
        engine = make_engine() if make_engine else create_engine(settings)
        embedder = (
            make_embedder()
            if make_embedder
            else build_embedder(settings.embedding_model, settings.embedding_cache_dir)
        )
        try:
            yield AppState(engine=engine, embedder=embedder)
        finally:
            await engine.dispose()

    server = MCPServer("fabpilot-docs", instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool(annotations=READ_ONLY)
    async def search_docs(
        ctx: Context[AppState],
        query: Annotated[
            str,
            Field(
                min_length=3,
                description="What you need, in plain words or with exact codes "
                "(e.g. 'PRESS-LOW suction_strainer_dp', 'spindle temperature rising overnight')",
            ),
        ],
        machine_type: Annotated[
            MachineType | None,
            Field(description="Only documents for this machine type (generic SOPs always match)"),
        ] = None,
        source_types: Annotated[
            list[SourceType] | None,
            Field(description="Only these kinds: manual, sop, incident_report. Default: all"),
        ] = None,
        k: Annotated[int, Field(ge=1, le=10, description="Number of hits")] = 5,
    ) -> list[RetrievedChunk]:
        """Search manuals, SOPs and past incident reports (hybrid: meaning + exact terms).

        Returns the k chunks MOST SIMILAR to the query, each with its `citation` (quote it
        as-is), title and text. Use exact alarm codes and sensor names when you have them;
        describe symptoms otherwise.
        - One hit = ONE chunk of a section. Before recommending a procedure or quoting its
          steps, read the whole section with get_document: a chunk may miss steps.
        - Ranked, not complete: for "which / how many incidents" questions (on a machine, of a
          fault, false alarms) use list_incident_reports instead. machine_type filters by TYPE;
          there is no machine-code filter here."""
        state = ctx.request_context.lifespan_context
        filters = Filters(machine_type=machine_type, source_types=tuple(source_types or ()))
        return await search(state.engine, state.embedder, query, filters=filters, k=k)

    @server.tool(annotations=READ_ONLY)
    async def get_document(
        ctx: Context[AppState],
        doc_id: DocId,
        section: Annotated[
            str | None,
            Field(
                description="e.g. '§5' (includes §5.1, §5.2 ...) or '§4'. Omit for the whole "
                "document (long manuals: prefer a section)",
                pattern=r"^§\d+(\.\d+)*$",
            ),
        ] = None,
    ) -> DocumentText:
        """Read a document, or one section with its subsections, as continuous text.

        Use it after search_docs when you need the complete section: all steps of a procedure,
        a full troubleshooting table, or the alarm-code list. `sections` lists what exists."""
        with _as_tool_errors():
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await library.get_document(conn, doc_id, section)

    @server.tool(annotations=READ_ONLY)
    async def get_incident_report(ctx: Context[AppState], doc_id: DocId) -> IncidentReport:
        """A past incident report (INC-...) with its facts as fields (machine, fault, false_alarm,
        root_cause, date) and its narrative per section (Summary, Timeline, Evidence ...).

        Past incidents are ANALOGIES: they show what a pattern meant before, not what it means
        now. Compare their Evidence with the current machine data before concluding. To find
        which reports exist for a machine or fault, use list_incident_reports first."""
        with _as_tool_errors():
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await library.get_incident_report(conn, doc_id)

    @server.tool(annotations=READ_ONLY)
    async def list_incident_reports(
        ctx: Context[AppState],
        machine: Annotated[
            str | None, Field(description="Machine code, e.g. 'M-09'", pattern=r"^M-\d{2}$")
        ] = None,
        machine_type: Annotated[
            MachineType | None, Field(description="Only incidents on this machine type")
        ] = None,
        fault: Annotated[
            FaultFilter | None,
            Field(
                description="A simulated fault type, or 'none' for causes outside those six "
                "(soft foot, air leak, heater band, ...)"
            ),
        ] = None,
        false_alarm: Annotated[
            bool | None, Field(description="true: only false alarms; false: only real faults")
        ] = None,
        limit: Annotated[
            int,
            Field(ge=1, le=library.MAX_INCIDENTS, description="Max reports, newest first"),
        ] = 50,
    ) -> IncidentList:
        """List past incident reports by their facts: machine, machine type, fault, false alarm.

        COMPLETE, not ranked: use it for "which / how many" questions ("false alarms on M-09",
        "has this sensor failed before?"). Each entry has the report's date, machine, fault,
        false_alarm and root_cause; read one in full with get_incident_report. `total_count` and
        `truncated` say whether the list is cut. `fault: null` in an entry means a cause outside
        the six simulated faults: filter for those reports with fault="none"."""
        with _as_tool_errors():
            async with ctx.request_context.lifespan_context.engine.connect() as conn:
                return await library.list_incident_reports(
                    conn,
                    machine=machine,
                    machine_type=machine_type,
                    fault=fault,
                    false_alarm=false_alarm,
                    limit=limit,
                )

    return server


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Domain errors -> ToolError with our message (the model can fix the call); anything else
    stays a generic failure, details only in the server log."""
    try:
        yield
    except (NotFoundError, InvalidQueryError) as e:
        raise ToolError(str(e)) from e


def main() -> None:
    parser = argparse.ArgumentParser(description="FabPilot docs MCP server")
    parser.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8102)
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level="INFO", format="%(levelname)s %(message)s")

    from app.runtime import run  # SelectorEventLoop on Windows (see sensor-mcp)

    server = create_server()
    if args.http:
        run(server.run_streamable_http_async(host=args.host, port=args.port))
    else:
        run(server.run_stdio_async())


if __name__ == "__main__":
    main()
