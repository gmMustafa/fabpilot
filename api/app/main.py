"""FabPilot API entrypoint.

Run locally (Windows needs the selector loop for async psycopg; harmless elsewhere):
    uv run uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.agent.budget import budget_from
from app.agent.checkpoint import pooled_checkpointer
from app.agent.service import AgentService
from app.agent.store import SqlAgentRepo
from app.agent.tools import McpTools
from app.config import get_settings
from app.db.session import create_engine
from app.gateway.factory import build_gateway
from app.routers import gateway, health, incidents, machines


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: the engine, and the agent (6.1): checkpointer pool, gateway, run service.
    Shutdown: in reverse order (AsyncExitStack). Nothing here blocks on the database: the API
    starts even when it's down, and /health says so."""
    settings = get_settings()
    async with AsyncExitStack() as stack:
        engine = create_engine(settings)
        stack.push_async_callback(engine.dispose)
        app.state.engine = engine

        checkpointer = await stack.enter_async_context(
            pooled_checkpointer(str(settings.database_url), settings.db_connect_timeout)
        )
        llm = build_gateway(settings, engine=engine)
        stack.push_async_callback(llm.provider.aclose)
        app.state.agent = AgentService(
            SqlAgentRepo(engine),
            llm,
            checkpointer,
            McpTools,  # opened per run, inside the run's task (anyio cancel scopes, 4.1a)
            budget=budget_from(settings),
            max_concurrent=settings.agent_max_concurrent_runs,
        )
        yield


app = FastAPI(
    title="FabPilot API",
    version="0.1.0",
    description="AI maintenance copilot for factories.",
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(machines.router)
app.include_router(gateway.router)
app.include_router(incidents.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Identify the service. Handy as a smoke test and for humans hitting the base URL."""
    return {"service": "fabpilot-api"}
