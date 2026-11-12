import asyncio
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> Mapping[str, Any]:
    """Run async tests on a SelectorEventLoop.

    On Windows the default is ProactorEventLoop, which async psycopg refuses to use. On Linux the
    selector loop is already the default, so this changes nothing in CI.
    """
    return {"selector": asyncio.SelectorEventLoop}


# ------------------------------------------------------------------ throwaway databases
# Integration tests get their own empty DB (never the dev DB's data), dropped afterwards.
# These fixtures are SYNC on purpose: Alembic's env.py calls asyncio.run(), which can't run inside
# the event loop that pytest-asyncio gives an `async def` fixture/test.


def libpq_url(database: str) -> str:
    """Settings URL (postgresql+psycopg://...) -> plain libpq URL for psycopg, other DB name."""
    url = make_url(str(get_settings().database_url)).set(drivername="postgresql", database=database)
    return url.render_as_string(hide_password=False)


def sqlalchemy_url(database: str) -> str:
    """Settings URL with another DB name, keeping the async psycopg driver."""
    url = make_url(str(get_settings().database_url)).set(database=database)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def temp_db_name() -> Iterator[str]:
    """Create an empty database, yield its name, drop it afterwards (even if the test failed)."""
    name = f"fabpilot_test_{uuid.uuid4().hex[:8]}"
    admin_url = libpq_url(make_url(str(get_settings().database_url)).database or "postgres")
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
