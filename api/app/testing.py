"""Pytest fixtures shared by every workspace package's integration tests (api, simulator, ...).

Usage in a package's tests/conftest.py:
    from app.testing import alembic_cfg, migrated_db_url, temp_db_name  # noqa: F401

Each integration test gets its own throwaway database (never the dev DB's data), dropped after.
These fixtures are SYNC on purpose: Alembic's env.py calls asyncio.run(), which can't run inside
the event loop that pytest-asyncio gives an `async def` fixture/test.

Needs the source tree (alembic.ini + migrations/ next to app/): fine for the workspace's editable
install, which is the only place tests run.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]


def libpq_url(database: str | None = None) -> str:
    """Settings URL (postgresql+psycopg://...) -> plain libpq URL, optionally for another DB."""
    url = make_url(str(get_settings().database_url)).set(drivername="postgresql")
    if database is not None:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def sqlalchemy_url(database: str) -> str:
    """Settings URL with another DB name, keeping the async psycopg driver."""
    url = make_url(str(get_settings().database_url)).set(database=database)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def temp_db_name() -> Iterator[str]:
    """Create an empty database, yield its name, drop it afterwards (even if the test failed)."""
    name = f"fabpilot_test_{uuid.uuid4().hex[:8]}"
    admin_url = libpq_url()
    # CREATE/DROP DATABASE can't run inside a transaction block -> autocommit.
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def alembic_cfg(temp_db_name: str) -> Config:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = sqlalchemy_url(temp_db_name)  # env.py prefers this
    return cfg


@pytest.fixture
def migrated_db_url(alembic_cfg: Config, temp_db_name: str) -> str:
    """A throwaway DB with all migrations applied. Returns its async SQLAlchemy URL."""
    command.upgrade(alembic_cfg, "head")
    return sqlalchemy_url(temp_db_name)
