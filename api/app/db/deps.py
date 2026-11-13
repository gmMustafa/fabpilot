"""FastAPI dependencies for database access, shared by all routers."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine


def get_engine(request: Request) -> AsyncEngine:
    """The engine created in the app lifespan (one pool per process). Tests override this."""
    engine: AsyncEngine = request.app.state.engine
    return engine
