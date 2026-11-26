"""Simulator <-> Postgres, against a throwaway migrated + seeded database."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db.models import FaultType
from app.plant import PlantConfig, ShiftPattern, load_plant
from app.seed import seed
from app.testing import libpq_url, sqlalchemy_url
from simulator import runner
from simulator.db import connect
from simulator.runner import backfill, clear, inject, live_tick, resume_state
from simulator.signals import DT_SECONDS, utc

pytestmark = pytest.mark.integration

NOW = utc(2026, 11, 17, 12)  # Tue 13:00 Berlin, mid-shift
SEED = 42


@pytest.fixture
def plant() -> PlantConfig:
    # These tests check the two-shift physics (machines OFF at night); the demo plant.yaml may
    # run continuous, so pin the pattern here.
    return load_plant(get_settings().plant_file).model_copy(
        update={"shifts": ShiftPattern.TWO_SHIFT_WEEKDAYS}
    )


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


def _old_alarm(conn: psycopg.Connection, machine: str, code: str, cleared: bool) -> None:
    """An alarm raised two days before NOW, i.e. before a 1-day backfill's window."""
    conn.execute(
        "INSERT INTO alarms (machine_id, time, cleared_at, code, severity, message)"
        " SELECT id, %s, %s, %s, 'critical', 'old' FROM machines WHERE code = %s",
        (
            NOW - timedelta(days=2),
            NOW - timedelta(days=2) + timedelta(hours=1) if cleared else None,
            code,
            machine,
        ),
    )
    conn.commit()


def _cleared_at(conn: psycopg.Connection, code: str) -> object:
    row = conn.execute("SELECT cleared_at FROM alarms WHERE code = %s", (code,)).fetchone()
    assert row is not None
    return row[0]


def test_a_rewrite_closes_alarms_left_open_before_its_window(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    """B2: from the window's start the new history is the truth. Before, an alarm raised before
    the window stayed open forever (M-08: TEMP-CRIT "active" for days over a healthy history)."""
    _old_alarm(conn, "M-01", "OLD-OPEN", cleared=False)
    _old_alarm(conn, "M-01", "OLD-CLEARED", cleared=True)
    cleared_before = _cleared_at(conn, "OLD-CLEARED")

    backfill(conn, plant, days=1, seed=SEED, now=NOW)

    window_start = NOW.replace(hour=0) - timedelta(days=1)  # backfill: midnight `days` ago
    assert _cleared_at(conn, "OLD-OPEN") == window_start  # closed where the new history begins
    assert _cleared_at(conn, "OLD-CLEARED") == cleared_before  # finished history: untouched


def test_rewriting_one_machine_leaves_other_machines_alarms_alone(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    """inject rewrites ONE machine: another machine's open alarm is not its business."""
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    _old_alarm(conn, "M-02", "OTHER-OPEN", cleared=False)

    inject(
        conn,
        plant,
        FaultType.BEARING_WEAR,
        "M-01",
        seed=SEED,
        duration=timedelta(hours=30),
        now=NOW,
    )

    assert _cleared_at(conn, "OTHER-OPEN") is None


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


# ------------------------------------------------------------------ faults (1.4c)


def _count(conn: psycopg.Connection, sql: str) -> int:
    return int(_scalar(conn, sql))


def _m02_checksum(conn: psycopg.Connection) -> float:
    return _scalar(
        conn,
        "SELECT sum(r.value) FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id"
        " JOIN machines m ON m.id = s.machine_id WHERE m.code = 'M-02'",
    )


def test_inject_records_ground_truth_rewrites_one_machine_and_raises_alarms(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    other_before = _m02_checksum(conn)

    result = inject(
        conn,
        plant,
        FaultType.BEARING_WEAR,
        "M-01",
        seed=SEED,
        duration=timedelta(hours=30),
        now=NOW,
    )

    truth = conn.execute(
        "SELECT f.fault_type, m.code, f.cleared_at FROM sim.fault_injections f"
        " JOIN machines m ON m.id = f.machine_id"
    ).fetchall()
    assert truth == [("bearing_wear", "M-01", None)]
    codes = {
        r[0]
        for r in conn.execute(
            "SELECT a.code FROM alarms a JOIN machines m ON m.id = a.machine_id"
            " WHERE m.code = 'M-01'"
        ).fetchall()
    }
    assert "VIB-HIGH" in codes
    assert result.alarms == _count(conn, "SELECT count(*) FROM alarms")
    assert _m02_checksum(conn) == pytest.approx(other_before)  # other machines untouched


def test_coolant_failure_trips_the_machine(conn: psycopg.Connection, plant: PlantConfig) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    result = inject(conn, plant, FaultType.COOLANT_PUMP_FAILURE, "M-01", seed=SEED, now=NOW)
    assert result.tripped
    status = conn.execute("SELECT status FROM machines WHERE code = 'M-01'").fetchone()
    assert status == ("down",)
    assert _count(conn, "SELECT count(*) FROM alarms WHERE code = 'TRIP'") == 1


def test_inject_is_atomic(
    conn: psycopg.Connection, plant: PlantConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure halfway (here: writing alarms) must leave NO ground truth and NO new readings."""
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    readings_before = _scalar(conn, "SELECT sum(value) FROM sensor_readings")

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("disk full")

    monkeypatch.setattr(runner, "write_alarms", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        inject(conn, plant, FaultType.BEARING_WEAR, "M-01", seed=SEED, now=NOW)

    assert _count(conn, "SELECT count(*) FROM sim.fault_injections") == 0
    assert _scalar(conn, "SELECT sum(value) FROM sensor_readings") == pytest.approx(readings_before)


def test_invalid_fault_writes_nothing(conn: psycopg.Connection, plant: PlantConfig) -> None:
    with pytest.raises(ValueError, match="can't happen"):
        inject(conn, plant, FaultType.COOLANT_PUMP_FAILURE, "M-04", seed=SEED, now=NOW)
    assert _count(conn, "SELECT count(*) FROM sim.fault_injections") == 0


def test_live_mode_keeps_applying_an_active_fault(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    inject(
        conn,
        plant,
        FaultType.SENSOR_DRIFT,
        "M-08",
        seed=SEED,
        sensor="discharge_temp",
        duration=timedelta(hours=6),
        now=NOW,
    )
    state = resume_state(conn, NOW)
    live_tick(conn, plant, state, NOW + timedelta(seconds=30), SEED)
    latest = _scalar(
        conn,
        "SELECT r.value FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id"
        " JOIN machines m ON m.id = s.machine_id"
        " WHERE m.code = 'M-08' AND s.name = 'discharge_temp' ORDER BY r.time DESC LIMIT 1",
    )
    assert latest > 95.0  # still drifted above its 75-95 °C band: the fault didn't vanish


def test_clear_repairs_the_machine(conn: psycopg.Connection, plant: PlantConfig) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    inject(conn, plant, FaultType.COOLANT_PUMP_FAILURE, "M-01", seed=SEED, now=NOW)
    assert clear(conn, "M-01", now=NOW) == 1
    assert conn.execute("SELECT status FROM machines WHERE code = 'M-01'").fetchone() == (
        "running",
    )
    assert _count(conn, "SELECT count(*) FROM sim.fault_injections WHERE cleared_at IS NULL") == 0


def _m01_vibration_max(conn: psycopg.Connection) -> float:
    return _scalar(
        conn,
        "SELECT max(r.value) FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id"
        " JOIN machines m ON m.id = s.machine_id"
        " WHERE m.code = 'M-01' AND s.name = 'spindle_vibration'",
    )


def test_reset_machine_erases_a_fault_and_its_history(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    """Eval isolation (8.3a): after a reset the next scenario must not find the last one."""
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    healthy = _m01_vibration_max(conn)
    inject(conn, plant, FaultType.BEARING_WEAR, "M-01", seed=SEED, now=NOW)
    inject(conn, plant, FaultType.SENSOR_DRIFT, "M-08", seed=SEED, sensor="discharge_temp", now=NOW)
    assert _m01_vibration_max(conn) > 2 * healthy

    runner.reset_machine(conn, plant, "M-01", seed=SEED, days=1, now=NOW)

    faults = conn.execute(
        "SELECT m.code FROM sim.fault_injections f JOIN machines m ON m.id = f.machine_id"
    ).fetchall()
    assert faults == [("M-08",)]  # only that machine's ground truth is gone
    # its history is healthy again: clear() would have kept the bad readings
    assert _m01_vibration_max(conn) == pytest.approx(healthy, rel=0.25)
    vib_alarms = _count(
        conn,
        "SELECT count(*) FROM alarms a JOIN machines m ON m.id = a.machine_id"
        " WHERE m.code = 'M-01' AND a.code = 'VIB-HIGH'",
    )
    assert vib_alarms == 0


def test_reset_machine_brings_a_tripped_machine_back(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    backfill(conn, plant, days=1, seed=SEED, now=NOW)
    assert inject(conn, plant, FaultType.COOLANT_PUMP_FAILURE, "M-01", seed=SEED, now=NOW).tripped
    runner.reset_machine(conn, plant, "M-01", seed=SEED, days=1, now=NOW)
    # mid-shift on a Tuesday: running (the schedule), not 'down' (update_status never undoes that)
    assert conn.execute("SELECT status FROM machines WHERE code = 'M-01'").fetchone() == (
        "running",
    )
    assert _count(conn, "SELECT count(*) FROM alarms WHERE code = 'TRIP'") == 0


def test_reset_machine_rejects_an_unknown_machine(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    with pytest.raises(ValueError, match="unknown machine"):
        runner.reset_machine(conn, plant, "M-99", seed=SEED, days=1, now=NOW)


def test_reset_rewrites_back_to_the_earliest_fault_start(
    conn: psycopg.Connection, plant: PlantConfig
) -> None:
    """A 3-day fault reset with days=1: the 2 older days must be healthy again too. Found with a
    24/7 plant, where those days ran (in two shifts they were a weekend, so the gap hid)."""
    continuous = plant.model_copy(update={"shifts": ShiftPattern.CONTINUOUS})
    backfill(conn, continuous, days=3, seed=SEED, now=NOW)
    healthy = _m01_vibration_max(conn)
    inject(conn, continuous, FaultType.BEARING_WEAR, "M-01", seed=SEED, now=NOW)  # 3 days
    runner.reset_machine(conn, continuous, "M-01", seed=SEED, days=1, now=NOW)
    assert _m01_vibration_max(conn) == pytest.approx(healthy, rel=0.25)
