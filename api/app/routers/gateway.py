"""LLM gateway usage: GET /gateway/stats (2.5). Read-only; counts and model names, no prompts."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.deps import get_engine
from app.gateway.stats import fetch_stats
from app.schemas import GatewayStats

router = APIRouter(prefix="/gateway", tags=["gateway"])

Engine = Annotated[AsyncEngine, Depends(get_engine)]


@router.get("/stats")
async def gateway_stats(
    engine: Engine,
    # Bounded window: the query stays on the time index and can't scan all of history.
    hours: Annotated[int, Query(ge=1, le=720, description="Look-back window (max 30 days)")] = 24,
) -> GatewayStats:
    """Requests, failures, cache hits and per-model latency over the last `hours`."""
    now = datetime.now(UTC)
    since = now - timedelta(hours=hours)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    async with engine.connect() as conn:  # read-only: no transaction to commit
        totals, models = await fetch_stats(conn, since, day_start)
    return GatewayStats(window_hours=hours, since=since, totals=totals, models=models)
