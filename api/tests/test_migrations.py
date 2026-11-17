"""Migration tests against a throwaway database (fixtures in conftest.py)."""

import os
import subprocess
import sys

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.testing import API_DIR, libpq_url, sqlalchemy_url

EXPECTED_TABLES = {
    "machines",
    "sensors",
    "sensor_readings",
    "alarms",
    "incidents",
    "work_orders",
    "llm_calls",
    "llm_cache",
    "documents",
    "chunks",
    "agent_runs",
}

pytestmark = pytest.mark.integration


def _tables(db: str, schema: str = "public") -> set[str]:
    with psycopg.connect(libpq_url(db), connect_timeout=5) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s"
            " AND tablename <> 'alembic_version'",
            (schema,),
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
    assert _tables(temp_db_name, schema="sim") == {"fault_injections"}
    assert _hypertables(temp_db_name) == {"sensor_readings": "1 day"}

    command.downgrade(alembic_cfg, "base")
    assert _tables(temp_db_name) == set()
    assert _tables(temp_db_name, schema="sim") == set()

    command.upgrade(alembic_cfg, "head")  # downgrades left nothing behind that blocks a re-run
    assert _tables(temp_db_name) == EXPECTED_TABLES


def test_models_match_migrations(alembic_cfg: Config) -> None:
    """Fails if someone changes a model but forgets to write the migration (schema drift)."""
    command.upgrade(alembic_cfg, "head")
    command.check(alembic_cfg)  # raises if autogenerate would produce any operation


def test_concurrent_upgrades_are_serialized(alembic_cfg: Config, temp_db_name: str) -> None:
    """10.4c: every pod that needs the schema (api replicas, simulator, seed) runs `alembic
    upgrade head` in an initContainer, so on a fresh install several start AT ONCE. Alembic has
    no cross-process lock: without the advisory lock in env.py, two runs both see an empty DB and
    both create the same tables -> one fails ("already exists"). With it: one migrates, the other
    waits, then finds head and does nothing. Separate PROCESSES, like separate pods."""
    env = {**os.environ, "DATABASE_URL": sqlalchemy_url(temp_db_name)}  # beats .env
    runs = [
        subprocess.Popen(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(3)
    ]
    outputs = [r.communicate(timeout=180)[0] for r in runs]

    failed = [out[-600:] for r, out in zip(runs, outputs, strict=True) if r.returncode != 0]
    assert not failed, "concurrent upgrades failed:\n" + "\n---\n".join(failed)
    assert _tables(temp_db_name) == EXPECTED_TABLES
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    with psycopg.connect(libpq_url(temp_db_name), connect_timeout=5) as conn:
        versions = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    assert versions == [(head,)]
