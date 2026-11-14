"""The Postgres checkpointer (5.4): a paused run survives the process that started it.

    night shift, 02:00:  run_question(...)          -> pauses; state in Postgres under thread_id
    deploy / crash / new terminal
    shift leader, 07:00: resume(graph, decision, thread_id)  -> any process, same thread_id

The library owns its tables (checkpoints, checkpoint_blobs, checkpoint_writes and its own
checkpoint_migrations): setup() creates/upgrades them, idempotently. Alembic is told to ignore
them (migrations/env.py), so two tools share the database with clear ownership.

One psycopg connection (autocommit, dict rows: what the saver requires) is enough for the CLI;
the API (Phase 6) will need a connection pool instead.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.agent.graph import checkpoint_serde


def libpq_url(database_url: str, connect_timeout: int) -> str:
    """postgresql+psycopg://... (SQLAlchemy) -> postgresql://...?connect_timeout=N (libpq)."""
    url = make_url(database_url).set(
        drivername="postgresql", query={"connect_timeout": str(connect_timeout)}
    )
    return url.render_as_string(hide_password=False)


@asynccontextmanager
async def postgres_checkpointer(
    database_url: str, connect_timeout: int = 3
) -> AsyncIterator[AsyncPostgresSaver]:
    """A ready saver (tables set up) that restores only CHECKPOINT_TYPES."""
    async with AsyncPostgresSaver.from_conn_string(
        libpq_url(database_url, connect_timeout), serde=checkpoint_serde()
    ) as saver:
        await saver.setup()
        yield saver
