"""Migration tests against a throwaway database (never touches the dev DB's data).

Sync tests on purpose: Alembic's env.py calls asyncio.run(), which can't run inside the event loop
pytest-asyncio would give an `async def` test.
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
EXPECTED_TABLES = {"machines", "sensors", "sensor_readings", "alarms", "incidents", "work_orders"}

pytestmark = pytest.mark.integration


def _libpq_url(database: str) -> str:
    """Settings URL (postgresql+psycopg://...) -> plain libpq URL for psycopg, other DB name."""
    url = make_url(str(get_settings().database_url)).set(drivername="postgresql", database=database)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def temp_db_name() -> Iterator[str]:
    """Create an empty database, yield its name, drop it afterwards (even if the test failed)."""
    name = f"fabpilot_test_{uuid.uuid4().hex[:8]}"
    admin_url = _libpq_url(make_url(str(get_settings().database_url)).database or "postgres")
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
    # env.py prefers this over DATABASE_URL (see _database_url there).
    url = make_url(str(get_settings().database_url)).set(database=temp_db_name)
    cfg.attributes["database_url"] = url.render_as_string(hide_password=False)
    return cfg


def _tables(db: str) -> set[str]:
    with psycopg.connect(_libpq_url(db), connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename <> 'alembic_version'"
        ).fetchall()
    return {r[0] for r in rows}


def _hypertables(db: str) -> dict[str, str]:
    with psycopg.connect(_libpq_url(db), connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT hypertable_name, time_interval::text FROM timescaledb_information.dimensions"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def test_upgrade_downgrade_roundtrip(alembic_cfg: Config, temp_db_name: str) -> None:
    """Every migration applies on an empty DB, fully reverts, and re-applies cleanly."""
    command.upgrade(alembic_cfg, "head")
    assert _tables(temp_db_name) == EXPECTED_TABLES
    assert _hypertables(temp_db_name) == {"sensor_readings": "1 day"}

    command.downgrade(alembic_cfg, "base")
    assert _tables(temp_db_name) == set()

    command.upgrade(alembic_cfg, "head")  # downgrades left nothing behind that blocks a re-run
    assert _tables(temp_db_name) == EXPECTED_TABLES


def test_models_match_migrations(alembic_cfg: Config) -> None:
    """Fails if someone changes a model but forgets to write the migration (schema drift)."""
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # raises if autogenerate would produce any operation
