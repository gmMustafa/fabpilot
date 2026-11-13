"""Simulator <-> Postgres, against a throwaway migrated + seeded database."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.plant import PlantConfig, load_plant
from app.seed import seed
from app.testing import libpq_url, sqlalchemy_url
from simulator.db import connect
from simulator.runner import backfill, live_tick, resume_state
from simulator.signals import DT_SECONDS, utc

pytestmark = pytest.mark.integration

NOW = utc(2026, 11, 17, 12)  # Tue 13:00 Berlin, mid-shift
SEED = 42


@pytest.fixture
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


@pytest.fixture
def conn(
    migrated_db_url: str, temp_db_name: str, plant: PlantConfig
) -> Iterator[psycopg.Connection]:
    """Migrated + seeded throwaway DB, as a sync psycopg connection."""

    async def _seed() -> None:
        engine = create_async_engine(migrated_db_url)
        try:
            await seed(engine, plant)
        finally:
            await engine.dispose()

    asyncio.run(_seed(), loop_factory=asyncio.SelectorEventLoop)
    # The REAL connect() the CLI uses (pointed at the throwaway DB), so its settings are tested.
    settings = get_settings().model_copy(
        update={"database_url": PostgresDsn(sqlalchemy_url(temp_db_name))}
    )
    with connect(settings) as c:
        yield c


def _scalar(conn: psycopg.Connection, sql: str) -> float:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return float(row[0])


def _n_sensors(plant: PlantConfig) -> int:
    return sum(len(plant.sensors_for(m)) for m in plant.machines)


def test_backfill_is_idempotent(conn: psycopg.Connection, plant: PlantConfig) -> None:
    first = backfill(conn, plant, days=1, seed=SEED, now=NOW)
    checksum = _scalar(conn, "SELECT sum(value) FROM sensor_readings")
    second = backfill(conn, plant, days=1, seed=SEED, now=NOW)

    samples = int(timedelta(hours=36).total_seconds()) // DT_SECONDS  # Mon 00:00 -> Tue 12:00
    assert first == second == samples * _n_sensors(plant)
    assert _scalar(conn, "SELECT count(*) FROM sensor_readings") == first  # replaced, not doubled
    assert _scalar(conn, "SELECT sum(value) FROM sensor_readings") == pytest.approx(checksum)


def test_readings_land_on_the_right_sensors_in_utc(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    sensor = (
        "FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id"
        " JOIN machines m ON m.id = s.machine_id WHERE m.code = '{code}' AND s.name = '{name}'"
    )
    # Compressor runs 24/7 inside its pressure band.
    pressure = _scalar(
        conn, "SELECT avg(r.value) " + sensor.format(code="M-08", name="discharge_pressure")
    )
    assert 6.5 < pressure < 7.8
    # CNC spindle is stopped at Tue 03:00 Berlin = 02:00 UTC. Stored as timestamptz, compared
    # in UTC: proves the instant is right, not just the wall-clock digits.
    night_rpm = _scalar(
        conn,
        "SELECT max(r.value) "
        + sensor.format(code="M-01", name="spindle_speed")
        + " AND r.time = '2026-11-17 02:00:00+00'",
    )
    assert night_rpm == 0.0
    first = conn.execute("SELECT min(time) FROM sensor_readings").fetchone()
    assert first is not None and first[0] == utc(2026, 11, 16)


def test_live_tick_continues_after_backfill(conn: psycopg.Connection, plant: PlantConfig) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    state = resume_state(conn, NOW)
    # Windows are [start, end): the backfill's last reading is 11:59:50, so live continues AT
    # 12:00:00. No gap, no overlap.
    assert state.cursor == NOW
    before = state.last[("M-01", "spindle_bearing_temp")]

    rows = live_tick(conn, plant, state, NOW + timedelta(seconds=30), SEED)

    assert rows == 4 * _n_sensors(plant)  # grid points 12:00:00, :10, :20, :30
    after = state.last[("M-01", "spindle_bearing_temp")]
    assert abs(after - before) < 2.0  # no jump at the backfill/live boundary (900 s lag)


def test_live_writes_are_visible_to_other_sessions(
    conn: psycopg.Connection, plant: PlantConfig, temp_db_name: str
) -> None:
    """Regression: writes must COMMIT per tick, not sit in one never-ending transaction.

    Reading through the writer's own connection can't catch that (a session always sees its own
    uncommitted rows), so check from a second, independent connection, like the API would.
    """
    state = resume_state(conn, NOW)
    rows = live_tick(conn, plant, state, NOW + timedelta(seconds=30), SEED)
    with psycopg.connect(libpq_url(temp_db_name), connect_timeout=5) as other:
        seen = other.execute("SELECT count(*) FROM sensor_readings").fetchone()
    assert seen is not None and seen[0] == rows > 0


def test_status_update_never_overrides_down(conn: psycopg.Connection, plant: PlantConfig) -> None:
    conn.execute("UPDATE machines SET status = 'down' WHERE code = 'M-01'")  # autocommit
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    rows: dict[str, str] = dict(conn.execute("SELECT code, status FROM machines").fetchall())
    assert rows["M-01"] == "down"  # set by a fault / a human: the schedule must not 'repair' it
    assert rows["M-08"] == "running"  # compressor runs 24/7
