"""Plant data queries, independent of HOW they are served (4.1a).

One core, several adapters: the REST API (routers/machines.py) maps these errors to HTTP 404/422,
the MCP server (sensor_mcp) maps them to tool errors an LLM can read and act on. Neither adapter
contains query or validation logic of its own, so the two can't drift apart.

Reads only the PUBLIC schema. The `sim` schema (injected faults) is the evaluation's answer key:
nothing here may touch it (a test greps for it; in Phase 10 a DB role enforces it too).
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Alarm, AlarmSeverity, Machine, Sensor
from app.schemas import AlarmOut, MachineDetail, MachineOut, ReadingBucket, ReadingsOut, SensorOut

MAX_POINTS = 500  # enough for any chart; protects DB, network, browser -- and an LLM's context
DEFAULT_RANGE = timedelta(hours=24)
MAX_ALARM_RANGE = timedelta(days=31)
MAX_ALARMS = 200  # an alarm list is LLM context: 200 lines is already a lot of tokens


class NotFoundError(LookupError):
    """A machine or sensor that doesn't exist. REST: 404."""


class InvalidQueryError(ValueError):
    """A request that can't be answered as asked; the message says how to fix it. REST: 422."""


class Bucket(StrEnum):
    """Allowed aggregation sizes. A fixed ladder (not free text) keeps caching and charts sane."""

    S10 = "10s"  # raw resolution: one reading per sensor every 10 s
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H6 = "6h"
    D1 = "1d"

    @property
    def interval(self) -> timedelta:
        number, unit = int(self.value[:-1]), self.value[-1]
        return timedelta(**{_UNITS[unit]: number})


_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def pick_bucket(span: timedelta, requested: Bucket | None) -> Bucket:
    """The requested bucket if it fits MAX_POINTS, or the smallest one that does."""
    if requested is not None:
        points = math.ceil(span / requested.interval)
        if points > MAX_POINTS:
            raise InvalidQueryError(
                f"bucket={requested.value} over this range gives {points} points "
                f"(max {MAX_POINTS}); choose a larger bucket or a shorter range"
            )
        return requested
    for bucket in Bucket:
        if math.ceil(span / bucket.interval) <= MAX_POINTS:
            return bucket
    raise InvalidQueryError(f"range too long: even bucket=1d exceeds {MAX_POINTS} points")


def resolve_range(
    start: datetime | None, end: datetime | None, default: timedelta = DEFAULT_RANGE
) -> tuple[datetime, datetime]:
    """Half-open [start, end), both timezone-aware. Defaults: end = now, start = end - default."""
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise InvalidQueryError(f"{name} needs a timezone, e.g. 2026-11-12T06:00:00Z")
    end = end or datetime.now(UTC)
    start = start or end - default
    if end <= start:
        raise InvalidQueryError("end must be after start")
    return start, end


async def list_machines(conn: AsyncConnection, line: str | None = None) -> list[MachineOut]:
    """All machines (optionally of one line), ordered by code."""
    query = select(Machine.__table__).order_by(Machine.code)
    if line is not None:
        query = query.where(Machine.line == line)
    rows = (await conn.execute(query)).mappings().all()
    return [MachineOut.model_validate(r) for r in rows]


async def _machine(conn: AsyncConnection, code: str) -> Machine:
    row = (await conn.execute(select(Machine.__table__).where(Machine.code == code))).first()
    if row is None:
        raise NotFoundError(f"machine {code!r} not found (codes look like 'M-07')")
    return Machine(**row._mapping)


async def get_machine(conn: AsyncConnection, code: str) -> MachineDetail:
    """One machine with its sensors and their normal operating ranges."""
    machine = await _machine(conn, code)
    sensors = (
        await conn.execute(
            select(Sensor.__table__).where(Sensor.machine_id == machine.id).order_by(Sensor.id)
        )
    ).mappings()
    return MachineDetail(
        **MachineOut.model_validate(machine).model_dump(),
        sensors=[SensorOut.model_validate(s) for s in sensors],
    )


async def fetch_buckets(
    conn: AsyncConnection,
    sensor_id: int,
    start: datetime,
    end: datetime,
    bucket: timedelta,
) -> list[ReadingBucket]:
    """One row per time bucket in [start, end): bucket start, avg, min, max, count. Oldest first.

    Buckets with no readings are simply absent (no gap-filling: the chart shows the gap).
    """
    query = text("""
        SELECT
            time_bucket(:bucket, time) AS bucket_start,
            AVG(value) AS avg,
            MIN(value) AS min,
            MAX(value) AS max,
            COUNT(*) AS count
        FROM sensor_readings
        WHERE sensor_id = :sensor_id
          AND time >= :start
          AND time < :end
        GROUP BY bucket_start
        ORDER BY bucket_start
    """)
    result = await conn.execute(
        query, {"sensor_id": sensor_id, "start": start, "end": end, "bucket": bucket}
    )
    return [
        ReadingBucket(
            time=row["bucket_start"],
            avg=float(row["avg"]),
            min=float(row["min"]),
            max=float(row["max"]),
            count=row["count"],
        )
        for row in result.mappings()
    ]


async def get_readings(
    conn: AsyncConnection,
    code: str,
    sensor: str,
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: Bucket | None = None,
) -> ReadingsOut:
    """Readings of one sensor aggregated per time bucket (avg / min / max / count)."""
    start, end = resolve_range(start, end)
    chosen = pick_bucket(end - start, bucket)
    machine = await _machine(conn, code)
    spec = (
        await conn.execute(
            select(Sensor.id, Sensor.unit).where(
                Sensor.machine_id == machine.id, Sensor.name == sensor
            )
        )
    ).first()
    if spec is None:
        names = (
            await conn.execute(select(Sensor.name).where(Sensor.machine_id == machine.id))
        ).scalars()
        raise NotFoundError(f"machine {code!r} has no sensor {sensor!r}; it has: {sorted(names)}")
    points = await fetch_buckets(conn, spec.id, start, end, chosen.interval)
    return ReadingsOut(
        machine=code,
        sensor=sensor,
        unit=spec.unit,
        bucket=chosen.value,
        start=start,
        end=end,
        points=points,
    )


@dataclass(frozen=True)
class AlarmQuery:
    start: datetime
    end: datetime
    limit: int
    min_severity: AlarmSeverity | None


_SEVERITY_ORDER = [AlarmSeverity.INFO, AlarmSeverity.WARNING, AlarmSeverity.CRITICAL]


def validate_alarm_query(
    start: datetime | None,
    end: datetime | None,
    limit: int,
    min_severity: str | None,
) -> AlarmQuery:
    """Check and normalise an alarm-log request. Raises InvalidQueryError with a FIXING hint.

    The message is read by an LLM (MCP tool error) or a developer (HTTP 422): say what was wrong
    AND what a valid request looks like, so the caller can correct itself in one retry.

    Rules:
    - start/end: as resolve_range() (timezone-aware, end after start, default last 24 h)
    - the range may not exceed MAX_ALARM_RANGE (31 days)
    - 1 <= limit <= MAX_ALARMS
    - min_severity: None, or one of "info" | "warning" | "critical" (case-insensitive)
    """
    start, end = resolve_range(start, end)

    # Messages are derived from the constants/enum, never hard-coded: when a limit changes, the
    # hint changes with it. "got X" + "allowed Y" lets the model correct itself in one retry.
    if (end - start) > MAX_ALARM_RANGE:
        raise InvalidQueryError(
            f"alarm range too long: at most {MAX_ALARM_RANGE.days} days, "
            f"got {(end - start).days} days; narrow start/end or query several windows"
        )

    if not 1 <= limit <= MAX_ALARMS:
        raise InvalidQueryError(
            f"limit must be between 1 and {MAX_ALARMS}, got {limit}; "
            "to see more, filter by machine, min_severity or a shorter time range"
        )

    severity = None
    if min_severity is not None:
        try:
            severity = AlarmSeverity(min_severity.lower())
        except ValueError:
            valid = ", ".join(s.value for s in AlarmSeverity)
            raise InvalidQueryError(
                f"min_severity must be one of: {valid}; got {min_severity!r}"
            ) from None

    return AlarmQuery(
        start=start,
        end=end,
        limit=limit,
        min_severity=severity,
    )


async def get_alarm_log(
    conn: AsyncConnection, code: str | None, query: AlarmQuery
) -> list[AlarmOut]:
    """Alarms raised in [start, end), newest first, at least `min_severity`, at most `limit`.

    code=None: all machines (plant-wide view: "which lines alarmed around 03:10?").
    """
    stmt = (
        select(
            Alarm.time,
            Alarm.cleared_at,
            Alarm.code,
            Alarm.severity,
            Alarm.message,
            Machine.code.label("machine"),
            Sensor.name.label("sensor"),
        )
        .join(Machine, Machine.id == Alarm.machine_id)
        .outerjoin(Sensor, Sensor.id == Alarm.sensor_id)  # machine-level alarms have no sensor
        .where(Alarm.time >= query.start, Alarm.time < query.end)
        .order_by(Alarm.time.desc(), Alarm.id.desc())
        .limit(query.limit)
    )
    if code is not None:
        stmt = stmt.where(Machine.id == (await _machine(conn, code)).id)
    if query.min_severity is not None:
        allowed = _SEVERITY_ORDER[_SEVERITY_ORDER.index(query.min_severity) :]
        stmt = stmt.where(Alarm.severity.in_(allowed))
    rows = (await conn.execute(stmt)).mappings().all()
    return [AlarmOut.model_validate(r) for r in rows]
