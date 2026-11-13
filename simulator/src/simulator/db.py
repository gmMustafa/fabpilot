"""Database I/O for the simulator. Sync psycopg on purpose (decision D2): it's a batch writer,
nothing runs concurrently, so async would add complexity for no gain.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import psycopg
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.models import FaultType, MachineStatus
from simulator.alarms import AlarmEvent
from simulator.faults import Fault


def connect(settings: Settings) -> psycopg.Connection:
    """Plain psycopg connection from the app's DATABASE_URL (postgresql+psycopg://... form)."""
    url = make_url(str(settings.database_url)).set(drivername="postgresql")
    # autocommit=True is essential: without it the first SELECT silently opens a transaction,
    # every later `with conn.transaction():` becomes a SAVEPOINT inside it, and NOTHING commits
    # until the connection closes. Backfill hid this (the CLI closes the connection); the live
    # loop never closes, so its writes were invisible to everyone else and lost on Ctrl+C.
    return psycopg.connect(
        url.render_as_string(hide_password=False),
        connect_timeout=settings.db_connect_timeout,
        autocommit=True,
    )


def sensor_ids(conn: psycopg.Connection) -> dict[tuple[str, str], int]:
    """(machine code, sensor name) -> sensors.id. Raises if the plant hasn't been seeded."""
    rows = conn.execute(
        "SELECT m.code, s.name, s.id FROM sensors s JOIN machines m ON m.id = s.machine_id"
    ).fetchall()
    if not rows:
        raise RuntimeError("No sensors in the database. Run the seed first: python -m app.seed")
    return {(code, name): sid for code, name, sid in rows}


@dataclass(frozen=True)
class SensorBatch:
    sensor_id: int
    times: np.ndarray  # datetime64[s], UTC
    values: np.ndarray  # float64


def write_readings(
    conn: psycopg.Connection, batches: Iterable[SensorBatch], start: datetime, end: datetime
) -> int:
    """Replace all readings of these sensors in [start, end) with `batches`. Returns rows written.

    One transaction: DELETE the window, then COPY the new rows. COPY can't do ON CONFLICT, so
    clearing the window first is what makes a re-run idempotent; the transaction means a crash
    halfway leaves the old data in place, never a half-written window.
    """
    batches = list(batches)
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sensor_readings WHERE sensor_id = ANY(%s) AND time >= %s AND time < %s",
            ([b.sensor_id for b in batches], start, end),
        )
        # COPY streams rows in Postgres's bulk-load protocol: no per-row statement, no
        # 65,535-bind-parameter limit. Binary format skips text parsing on both sides.
        with cur.copy(
            "COPY sensor_readings (sensor_id, time, value) FROM STDIN (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["int4", "timestamptz", "float8"])
            utc_times: dict[int, list[datetime]] = {}  # all sensors of a machine share one grid
            for b in batches:
                key = id(b.times)
                if key not in utc_times:
                    utc_times[key] = _to_utc_datetimes(b.times)
                for t, v in zip(utc_times[key], b.values.tolist(), strict=True):
                    copy.write_row((b.sensor_id, t, v))
                written += len(b.values)
    return written


def _to_utc_datetimes(times: np.ndarray) -> list[datetime]:
    """datetime64 (UTC, no tz) -> tz-aware datetimes, so psycopg sends the right instant
    regardless of the session's TimeZone setting."""
    epoch = times.astype("datetime64[s]").astype(np.int64).tolist()
    return [datetime.fromtimestamp(e, UTC) for e in epoch]


def last_readings(conn: psycopg.Connection) -> dict[int, tuple[datetime, float]]:
    """Newest reading per sensor: sensor_id -> (time, value).

    LATERAL + LIMIT 1 per sensor walks the (sensor_id, time) primary-key index backwards: a few
    index lookups instead of scanning millions of rows (DISTINCT ON would read them all).
    """
    rows = conn.execute(
        "SELECT s.id, r.time, r.value FROM sensors s"
        " CROSS JOIN LATERAL ("
        "   SELECT time, value FROM sensor_readings"
        "   WHERE sensor_id = s.id ORDER BY time DESC LIMIT 1"
        " ) r"
    ).fetchall()
    return {sid: (t, v) for sid, t, v in rows}


def update_status(conn: psycopg.Connection, statuses: dict[str, MachineStatus]) -> None:
    """Apply simulated status, with one rule: a schedule never 'repairs' a machine.

    - running/idle: only overwrites running/idle (never DOWN or MAINTENANCE: set by a fault trip
      or a human).
    - DOWN (a fault tripped it): may overwrite running/idle, not MAINTENANCE.
    """
    with conn.transaction():
        for code, status in statuses.items():
            conn.execute(
                "UPDATE machines SET status = %s WHERE code = %s AND status IN ('running', 'idle')",
                (status.value, code),
            )


# ------------------------------------------------------------------ alarms


def write_alarms(
    conn: psycopg.Connection,
    events: Iterable[AlarmEvent],
    machine_codes: Iterable[str],
    start: datetime,
    end: datetime,
) -> int:
    """Replace these machines' alarms raised in [start, end) with `events` (idempotent re-runs).

    Alarms are low volume (D4 in 1.1): plain executemany is fine, no COPY needed.
    """
    machine_ids: dict[str, int] = dict(conn.execute("SELECT code, id FROM machines").fetchall())
    sensors = sensor_ids(conn)
    rows = [
        (
            machine_ids[e.machine_code],
            sensors[(e.machine_code, e.sensor_name)] if e.sensor_name else None,
            e.time,
            e.cleared_at,
            e.code,
            e.severity.value,
            e.message,
        )
        for e in events
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "DELETE FROM alarms WHERE machine_id = ANY(%s) AND time >= %s AND time < %s",
            ([machine_ids[c] for c in machine_codes], start, end),
        )
        cur.executemany(
            "INSERT INTO alarms (machine_id, sensor_id, time, cleared_at, code, severity, message)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    return len(rows)


# ------------------------------------------------------------------ ground truth (sim schema)


def record_fault(conn: psycopg.Connection, fault: Fault, seed: int) -> int:
    """Store an injected fault in sim.fault_injections (the evals' answer key). Returns its id."""
    row = conn.execute(
        "INSERT INTO sim.fault_injections"
        " (fault_type, machine_id, sensor_id, start, full_at, severity, seed)"
        " SELECT %s, m.id, s.id, %s, %s, %s, %s FROM machines m"
        " LEFT JOIN sensors s ON s.machine_id = m.id AND s.name = %s"
        " WHERE m.code = %s RETURNING id",
        (
            fault.type.value,
            fault.start,
            fault.full_at,
            fault.severity,
            seed,
            fault.sensor,
            fault.machine_code,
        ),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown machine {fault.machine_code!r}")
    return int(row[0])


def faults_overlapping(conn: psycopg.Connection, start: datetime, end: datetime) -> list[Fault]:
    """Faults that affect any part of [start, end): started before `end`, not cleared before
    `start`. Live mode and backfill re-apply these so a fault doesn't vanish on regeneration."""
    rows = conn.execute(
        "SELECT f.fault_type, m.code, f.start, f.full_at, f.severity, s.name, f.cleared_at"
        " FROM sim.fault_injections f JOIN machines m ON m.id = f.machine_id"
        " LEFT JOIN sensors s ON s.id = f.sensor_id"
        " WHERE f.start < %s AND (f.cleared_at IS NULL OR f.cleared_at > %s)"
        " ORDER BY f.start",
        (end, start),
    ).fetchall()
    return [
        Fault(FaultType(t), code, st, full, severity=sev, sensor=sensor, cleared_at=cleared)
        for t, code, st, full, sev, sensor, cleared in rows
    ]


def clear_faults(conn: psycopg.Connection, machine_code: str, at: datetime) -> int:
    """Repair: mark the machine's active faults cleared and put it back in service."""
    with conn.transaction():
        cur = conn.execute(
            "UPDATE sim.fault_injections f SET cleared_at = %s FROM machines m"
            " WHERE m.id = f.machine_id AND m.code = %s AND f.cleared_at IS NULL",
            (at, machine_code),
        )
        conn.execute(
            "UPDATE machines SET status = 'running' WHERE code = %s AND status = 'down'",
            (machine_code,),
        )
    return cur.rowcount
