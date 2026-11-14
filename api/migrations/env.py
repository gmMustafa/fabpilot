"""Alembic environment: how `alembic upgrade/downgrade/revision` connect to the database."""

from logging.config import fileConfig
from typing import cast

from alembic import context
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import pool
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import SchemaItem

import app.db.models  # noqa: F401  # registers every model on Base.metadata (for autogenerate)
from app.config import get_settings
from app.db.base import Base
from app.runtime import run

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: the default (True) silently DISABLES every logger that
    # already exists. Any process that runs migrations in code (test fixtures, the eval harness,
    # an API migrating on startup) would lose its own logs from then on, including the "loud"
    # ERRORs the gateway relies on (found in 2.3 via an order-dependent test failure).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Autogenerate compares this metadata (our models) with the live database to draft migrations.
target_metadata = Base.metadata


def _database_url() -> str:
    """URL from an explicit override (tests use this), else from app settings / DATABASE_URL."""
    override = config.attributes.get("database_url")
    return str(override) if override else str(get_settings().database_url)


def include_object(
    obj: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: SchemaItem | None,
) -> bool:
    """Tell autogenerate which DB objects are ours to manage.

    TimescaleDB's create_hypertable() adds a "<table>_time_idx" index by itself. It exists in the
    DB but not in our models, so without this filter autogenerate would emit drop_index() for it.
    """
    is_timescale_default_index = (
        type_ == "index"
        and reflected
        and compare_to is None
        and name is not None
        and name.endswith("_time_idx")
    )
    # LangGraph's checkpoint tables (5.4) are created and migrated by the library itself
    # (AsyncPostgresSaver.setup(), with its own checkpoint_migrations table). Not ours: without
    # this, autogenerate would emit DROP TABLE for every paused agent run.
    is_langgraph_table = (
        type_ == "table" and reflected and compare_to is None and (name in LANGGRAPH_TABLES)
    )
    return not (is_timescale_default_index or is_langgraph_table)


LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


# ALLOW-list of schemas autogenerate may manage. Needed once a model lives outside `public`
# (sim.fault_injections): include_schemas=True alone reflects TimescaleDB's internal schemas
# (_timescaledb_catalog, ...) and autogenerate would emit DROP TABLE for its hypertable metadata.
# An allow-list (not a block-list) stays safe when an extension adds a new internal schema.
MANAGED_SCHEMAS = {None, "public", "sim"}  # None = the connection's default schema


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    return name in MANAGED_SCHEMAS if type_ == "schema" else True


def run_migrations_offline() -> None:
    """'Offline' mode: emit SQL to stdout instead of executing it (`alembic upgrade head --sql`).

    Useful for DBA review of exactly what a migration will run in production.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        include_schemas=True,
        include_name=include_name,
        compare_server_default=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Teach reflection the pgvector type: otherwise autogenerate / `alembic check` reads
    # chunks.embedding back as an unknown NullType and reports a phantom type change every run.
    # ischema_names is a PostgreSQL-dialect attribute, not on the generic Dialect type.
    cast(PGDialect, connection.dialect).ischema_names["vector"] = VECTOR
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        # Also diff DB-side defaults, so a missing server_default shows up in `alembic check`.
        compare_server_default=True,
        include_schemas=True,
        include_name=include_name,  # ...but only the schemas we own
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # NullPool: a migration run is a one-shot CLI process, a connection pool would be pointless.
    # connect_timeout: without it, an unreachable host can hang the CLI forever (seen on Windows
    # with "localhost" -> ::1 while Docker only publishes on 127.0.0.1).
    engine = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": get_settings().db_connect_timeout},
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    # SelectorEventLoop: async psycopg can't use Windows' default ProactorEventLoop (see 0.3).
    run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
