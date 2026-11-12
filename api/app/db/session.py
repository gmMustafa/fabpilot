"""Database engine construction.

One AsyncEngine per process: it owns the connection pool. Requests borrow connections from it;
they never create their own.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine (psycopg 3 driver) with a small, health-checked pool."""
    return create_async_engine(
        str(settings.database_url),
        # Before handing out a pooled connection, check it's still alive. Costs a tiny round-trip,
        # saves "server closed the connection unexpectedly" errors after a DB restart.
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        # Passed straight to psycopg/libpq. Without it, connecting to a dead host can hang ~minutes.
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )
