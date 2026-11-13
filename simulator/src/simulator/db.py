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
from app.db.models import MachineStatus


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
    """Set running/idle from the simulation, but never overwrite DOWN or MAINTENANCE: those are
    set by faults (1.4) or humans, and a schedule must not 'repair' a broken machine."""
    with conn.transaction():
        for code, status in statuses.items():
            conn.execute(
                "UPDATE machines SET status = %s WHERE code = %s AND status IN ('running', 'idle')",
                (status.value, code),
            )
