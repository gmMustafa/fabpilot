"""Read endpoints for machines, sensors and aggregated sensor readings."""

import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text  # text(): for your fetch_buckets query
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.deps import get_engine
from app.db.models import Machine, Sensor
from app.schemas import MachineDetail, MachineOut, ReadingBucket, ReadingsOut, SensorOut

router = APIRouter(prefix="/machines", tags=["machines"])

Engine = Annotated[AsyncEngine, Depends(get_engine)]

MAX_POINTS = 500  # enough for any chart; protects DB, network and browser from huge requests
DEFAULT_RANGE = timedelta(hours=24)


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
    """The requested bucket if it fits MAX_POINTS (else 422), or the smallest one that does."""
    if requested is not None:
        points = math.ceil(span / requested.interval)
        if points > MAX_POINTS:
            raise HTTPException(
                422,
                f"bucket={requested.value} over this range gives {points} points "
                f"(max {MAX_POINTS}); choose a larger bucket or a shorter range",
            )
        return requested
    for bucket in Bucket:
        if math.ceil(span / bucket.interval) <= MAX_POINTS:
            return bucket
    raise HTTPException(422, f"range too long: even bucket=1d exceeds {MAX_POINTS} points")


async def _machine_or_404(conn: AsyncConnection, code: str) -> Machine:
    row = (await conn.execute(select(Machine.__table__).where(Machine.code == code))).first()
    if row is None:
        raise HTTPException(404, f"machine {code!r} not found")
    return Machine(**row._mapping)


# ------------------------------------------------------------------ endpoints


@router.get("", response_model=list[MachineOut])
async def list_machines(
    engine: Engine,
    line: Annotated[str | None, Query(description='Filter by line, e.g. "Line 3"')] = None,
) -> list[MachineOut]:
    """All machines (optionally of one line), ordered by code."""
    query = select(Machine.__table__).order_by(Machine.code)
    if line is not None:
        query = query.where(Machine.line == line)
    async with engine.connect() as conn:
        rows = (await conn.execute(query)).mappings().all()
    return [MachineOut.model_validate(r) for r in rows]


@router.get("/{code}", response_model=MachineDetail)
async def get_machine(code: str, engine: Engine) -> MachineDetail:
    """One machine with its sensors and their normal operating ranges."""
    async with engine.connect() as conn:
        machine = await _machine_or_404(conn, code)
        sensors = (
            await conn.execute(
                select(Sensor.__table__).where(Sensor.machine_id == machine.id).order_by(Sensor.id)
            )
        ).mappings()
        return MachineDetail(
            **MachineOut.model_validate(machine).model_dump(),
            sensors=[SensorOut.model_validate(s) for s in sensors],
        )


@router.get("/{code}/readings", response_model=ReadingsOut)
async def get_readings(
    code: str,
    engine: Engine,
    sensor: Annotated[str, Query(description="Sensor name, e.g. spindle_vibration")],
    start: Annotated[datetime | None, Query(description="ISO 8601 with timezone")] = None,
    end: Annotated[
        datetime | None, Query(description="ISO 8601 with timezone; default now")
    ] = None,
    bucket: Annotated[Bucket | None, Query(description="Default: smallest that fits")] = None,
) -> ReadingsOut:
    """Readings of one sensor aggregated per time bucket (avg / min / max / count)."""
    for name, value in (("start", start), ("end", end)):
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, f"{name} needs a timezone, e.g. 2026-11-12T06:00:00Z")
    end = end or datetime.now(UTC)
    start = start or end - DEFAULT_RANGE
    if end <= start:
        raise HTTPException(422, "end must be after start")
    chosen = pick_bucket(end - start, bucket)

    async with engine.connect() as conn:
        machine = await _machine_or_404(conn, code)
        spec = (
            await conn.execute(
                select(Sensor.id, Sensor.unit).where(
                    Sensor.machine_id == machine.id, Sensor.name == sensor
                )
            )
        ).first()
        if spec is None:
            raise HTTPException(404, f"machine {code!r} has no sensor {sensor!r}")
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
        query,
        {
            "sensor_id": sensor_id,
            "start": start,
            "end": end,
            "bucket": bucket,
        },
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
