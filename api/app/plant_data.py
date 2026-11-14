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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Alarm, AlarmSeverity, Machine, Sensor

# `X as X` = explicit re-export: routers and sensor-mcp import the errors from here.
from app.errors import InvalidQueryError as InvalidQueryError
from app.errors import NotFoundError as NotFoundError
from app.schemas import (
    AlarmLog,
    AlarmOut,
    MachineDetail,
    MachineOut,
    ReadingBucket,
    ReadingsOut,
    SensorOut,
)

MAX_POINTS = 500  # enough for any chart; protects DB, network, browser -- and an LLM's context
DEFAULT_RANGE = timedelta(hours=24)
MAX_ALARM_RANGE = timedelta(days=31)
MAX_ALARMS = 200  # an alarm list is LLM context: 200 lines is already a lot of tokens


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


def format_duration(td: timedelta) -> str:
    """Exact, human, for messages the model reads: "7 d 0 h 14 min", "25 h 29 min", "14 min".
    timedelta.days alone rounds down: "at most 7 days, got 7 days" for 7 d 14 min (F6)."""
    minutes = int(td.total_seconds() // 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h {mins} min"
    if hours:
        return f"{hours} h {mins} min"
    return f"{mins} min"


COVERAGE_TOLERANCE = timedelta(minutes=5)  # live data lags a little; don't nag about seconds


def coverage_note(
    start: datetime,
    end: datetime,
    data_starts_at: datetime | None,
    data_ends_at: datetime | None,
    tolerance: timedelta = COVERAGE_TOLERANCE,
) -> str | None:
    """One sentence for the model when the requested window [start, end) and the machine's data
    don't match, or None when the data covers the window (within `tolerance` at both ends).

    Why: an empty or short result has three possible meanings (nothing happened / machine off /
    no data). Without this sentence a model reads "no alarms" over a window that starts after the
    last reading as "healthy right now" (Run 3, Q3), or `cleared_at: null` as "still active" (Q2).

    Cases, checked in this order:
    1. no data at all (data_starts_at or data_ends_at is None):
       "no readings for this machine at all: nothing can be said about it"
    2. data ends before the window starts:
       "the window starts after the last reading (<data_ends_at>): there is no data in it;
        nothing can be said about this period"
    3. data starts after the window ends:
       "the window ends before the first reading (<data_starts_at>): there is no data in it"
    4. otherwise one or both of these sentences, joined with " ":
       - data ends > tolerance before `end`:
         "data ends <data_ends_at>, <format_duration(end - data_ends_at)> before the end of the
          window: nothing can be said about the last <same duration>."
       - data starts > tolerance after `start`:
         "data starts <data_starts_at>, <format_duration(data_starts_at - start)> after the start
          of the window."
       ... or None if neither applies.
    Times are formatted as ISO 8601 UTC to the minute: f"{t:%Y-%m-%d %H:%M} UTC".
    """

    def fmt(t: datetime) -> str:
        return f"{t:%Y-%m-%d %H:%M} UTC"

    # Case 1: No data at all
    if data_starts_at is None or data_ends_at is None:
        return "no readings for this machine at all: nothing can be said about it"

    # Case 2: Data ends before the requested window
    if data_ends_at < start:
        return (
            f"the window starts after the last reading ({fmt(data_ends_at)}): "
            "there is no data in it; nothing can be said about this period"
        )

    # Case 3: Data starts after the requested window
    # >=: windows are half-open [start, end), so data starting AT `end` isn't in it either.
    if data_starts_at >= end:
        return (
            f"the window ends before the first reading ({fmt(data_starts_at)}): "
            "there is no data in it"
        )

    # Case 4: Partial coverage
    notes = []

    if end - data_ends_at > tolerance:
        duration = format_duration(end - data_ends_at)
        notes.append(
            f"data ends {fmt(data_ends_at)}, {duration} before the end of the "
            f"window: nothing can be said about the last {duration}."
        )

    if data_starts_at - start > tolerance:
        duration = format_duration(data_starts_at - start)
        notes.append(
            f"data starts {fmt(data_starts_at)}, {duration} after the start of the window."
        )

    return " ".join(notes) if notes else None


async def data_range(
    conn: AsyncConnection, code: str | None = None
) -> tuple[datetime | None, datetime | None]:
    """First and last reading of one machine (or of the whole plant), from the INDEX.

    One LATERAL probe per sensor on the (sensor_id, time) primary key: a few dozen index
    lookups instead of scanning millions of rows with min()/max().
    """
    # f-string SQL is safe HERE: `where` is one of two fixed strings of ours; the machine code
    # itself is still a bound parameter.
    where = "WHERE m.code = :code" if code is not None else ""
    row = (
        await conn.execute(
            text(f"""
                SELECT min(f.time), max(l.time)
                FROM sensors s
                JOIN machines m ON m.id = s.machine_id
                LEFT JOIN LATERAL (
                    SELECT time FROM sensor_readings WHERE sensor_id = s.id ORDER BY time LIMIT 1
                ) f ON true
                LEFT JOIN LATERAL (
                    SELECT time FROM sensor_readings WHERE sensor_id = s.id
                    ORDER BY time DESC LIMIT 1
                ) l ON true
                {where}
            """),
            {"code": code} if code is not None else {},
        )
    ).one()
    return row[0], row[1]


async def _last_readings(conn: AsyncConnection) -> dict[str, datetime | None]:
    rows = await conn.execute(
        text("""
            SELECT m.code, max(l.time)
            FROM machines m JOIN sensors s ON s.machine_id = m.id
            LEFT JOIN LATERAL (
                SELECT time FROM sensor_readings WHERE sensor_id = s.id ORDER BY time DESC LIMIT 1
            ) l ON true
            GROUP BY m.code
        """)
    )
    return {code: last for code, last in rows}


async def list_machines(conn: AsyncConnection, line: str | None = None) -> list[MachineOut]:
    """All machines (optionally of one line), ordered by code."""
    query = select(Machine.__table__).order_by(Machine.code)
    if line is not None:
        query = query.where(Machine.line == line)
    rows = (await conn.execute(query)).mappings().all()
    last = await _last_readings(conn)
    return [MachineOut.model_validate({**r, "last_reading_at": last.get(r["code"])}) for r in rows]


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
    _, last = await data_range(conn, code)
    return MachineDetail(
        **MachineOut.model_validate(machine).model_dump(exclude={"last_reading_at"}),
        last_reading_at=last,
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
    first, last = await data_range(conn, code)
    return ReadingsOut(
        machine=code,
        sensor=sensor,
        unit=spec.unit,
        bucket=chosen.value,
        start=start,
        end=end,
        points=points,
        data_ends_at=last,
        coverage_note=coverage_note(start, end, first, last),
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
            f"alarm range too long: at most {format_duration(MAX_ALARM_RANGE)}, "
            f"got {format_duration(end - start)}; narrow start/end or query several windows"
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


async def get_alarm_log(conn: AsyncConnection, code: str | None, query: AlarmQuery) -> AlarmLog:
    """Alarms raised in [start, end), newest first, at least `min_severity`, at most `limit`,
    plus how many matched in TOTAL: a list cut at `limit` must say so (F12: a truncated list,
    newest first, silently loses the EARLIEST alarms, i.e. when the problem started).

    code=None: all machines (plant-wide view: "which lines alarmed around 03:10?").
    """
    columns = (
        Alarm.time,
        Alarm.cleared_at,
        Alarm.code,
        Alarm.severity,
        Alarm.message,
        Machine.code.label("machine"),
        Sensor.name.label("sensor"),
    )
    base = (
        select(*columns)
        .join(Machine, Machine.id == Alarm.machine_id)
        .outerjoin(Sensor, Sensor.id == Alarm.sensor_id)  # machine-level alarms have no sensor
        .where(Alarm.time >= query.start, Alarm.time < query.end)
    )
    if code is not None:
        base = base.where(Machine.id == (await _machine(conn, code)).id)
    if query.min_severity is not None:
        allowed = _SEVERITY_ORDER[_SEVERITY_ORDER.index(query.min_severity) :]
        base = base.where(Alarm.severity.in_(allowed))
    total = (await conn.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await conn.execute(base.order_by(Alarm.time.desc(), Alarm.id.desc()).limit(query.limit))
    ).mappings()
    alarms = [AlarmOut.model_validate(r) for r in rows]
    first, last = await data_range(conn, code)
    return AlarmLog(
        start=query.start,
        end=query.end,
        total_count=total,
        truncated=total > len(alarms),
        alarms=alarms,
        data_ends_at=last,
        coverage_note=coverage_note(query.start, query.end, first, last),
    )


MAX_ANOMALY_RANGE = timedelta(days=7)
ANOMALY_DEFAULT_RANGE = timedelta(days=3)


async def minute_means(
    conn: AsyncConnection, code: str, start: datetime | None, end: datetime | None
) -> tuple[MachineDetail, datetime, datetime, dict[str, list[tuple[datetime, float]]]]:
    """1-minute averages of EVERY sensor of one machine in [start, end), for anomaly detection.

    One query for all sensors (not one per sensor): ~50 000 rows for a week, aggregated in
    Postgres, so only minute means cross the network.
    """
    start, end = resolve_range(start, end, default=ANOMALY_DEFAULT_RANGE)
    if end - start > MAX_ANOMALY_RANGE:
        raise InvalidQueryError(
            f"anomaly window too long: at most {format_duration(MAX_ANOMALY_RANGE)}, "
            f"got {format_duration(end - start)}; analyse the days around the incident"
        )
    machine = await get_machine(conn, code)
    rows = await conn.execute(
        text("""
            SELECT s.name, time_bucket('1 minute', r.time) AS minute, AVG(r.value) AS avg
            FROM sensor_readings r JOIN sensors s ON s.id = r.sensor_id
            JOIN machines m ON m.id = s.machine_id
            WHERE m.code = :code AND r.time >= :start AND r.time < :end
            GROUP BY s.name, minute
            ORDER BY s.name, minute
        """),
        {"code": code, "start": start, "end": end},
    )
    series: dict[str, list[tuple[datetime, float]]] = {}
    for name, minute, avg in rows:
        series.setdefault(name, []).append((minute, float(avg)))
    return machine, start, end, series
