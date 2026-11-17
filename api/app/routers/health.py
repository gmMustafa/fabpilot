"""Health endpoint: tells Docker / Kubernetes / load balancers whether this instance can serve."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.deps import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]


@router.get(
    "/health",
    response_model=HealthResponse,
    # The 503 body has the SAME shape (status "degraded"): documented so clients can read it.
    responses={503: {"model": HealthResponse, "description": "Database unreachable"}},
)
async def health(
    response: Response, engine: Annotated[AsyncEngine, Depends(get_engine)]
) -> HealthResponse:
    """Check that the API can reach Postgres. 200 when healthy, 503 when the DB is unreachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    except DBAPIError:
        logger.exception("Health check failed: database unreachable")
        response.status_code = 503

        return HealthResponse(status="degraded", db="error")

    return HealthResponse(status="ok", db="ok")


class LivenessResponse(BaseModel):
    status: Literal["alive"]


@router.get(
    "/livez",
    response_model=LivenessResponse,
    # For the kubelet, not for the dashboard: kept out of the OpenAPI contract (6.3), so the
    # generated frontend types don't change for an endpoint no client calls.
    include_in_schema=False,
)
async def livez() -> LivenessResponse:
    """Liveness (10.4): "does this process still answer?" Touches NO dependency, on purpose.

    Kubernetes RESTARTS a pod whose liveness probe fails. /health checks the DB, so as liveness a
    DB outage would restart every healthy API pod in a loop — fixing nothing. /health is the
    READINESS probe (out of the Service, not restarted); this one only fails when the event loop
    is stuck, the one case a restart does fix.
    """
    return LivenessResponse(status="alive")
