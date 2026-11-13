"""Migration tests against a throwaway database (fixtures in conftest.py)."""

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.testing import libpq_url

EXPECTED_TABLES = {"machines", "sensors", "sensor_readings", "alarms", "incidents", "work_orders"}

pytestmark = pytest.mark.integration


def _tables(db: str) -> set[str]:
    with psycopg.connect(libpq_url(db), connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            " AND tablename <> 'alembic_version'"
        ).fetchall()
    return {r[0] for r in rows}


def _hypertables(db: str) -> dict[str, str]:
    with psycopg.connect(libpq_url(db), connect_timeout=5) as conn:
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
