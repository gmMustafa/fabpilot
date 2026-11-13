"""FabPilot API entrypoint.

Run locally (Windows needs the selector loop for async psycopg; harmless elsewhere):
    uv run uvicorn app.main:app --reload --loop asyncio:SelectorEventLoop
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.session import create_engine
from app.routers import health, machines


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: build the engine (validates settings). Shutdown: close pooled connections."""
    engine = create_engine(get_settings())
    app.state.engine = engine
    yield
    await engine.dispose()


app = FastAPI(
    title="FabPilot API",
    version="0.1.0",
    description="AI maintenance copilot for factories.",
    lifespan=lifespan,
)
app.include_router(health.router)
app.include_router(machines.router)


@app.get("/")
async def root() -> dict[str, str]:
    """Identify the service. Handy as a smoke test and for humans hitting the base URL."""
    return {"service": "fabpilot-api"}
