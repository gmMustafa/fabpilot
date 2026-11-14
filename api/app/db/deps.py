"""FastAPI dependencies for database access, shared by all routers."""

from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

if TYPE_CHECKING:  # only for the annotation: keeps the agent out of the import graph
    from app.agent.service import AgentService


def get_engine(request: Request) -> AsyncEngine:
    """The engine created in the app lifespan (one pool per process). Tests override this."""
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_agent(request: Request) -> "AgentService":
    """The AgentService created in the app lifespan (6.1). Tests override this with fakes."""
    agent: AgentService = request.app.state.agent
    return agent
