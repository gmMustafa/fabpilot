"""Health endpoint: tells Docker / Kubernetes / load balancers whether this instance can serve."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]


def get_engine(request: Request) -> AsyncEngine:
    """Dependency: the engine created in the app lifespan. Tests override this."""
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.get("/health", response_model=HealthResponse)
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
