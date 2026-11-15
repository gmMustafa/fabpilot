"""Read endpoints for machines, sensors and aggregated sensor readings.

A thin HTTP adapter: the queries and their validation live in app.plant_data (shared with the MCP
server, 4.1a). Here we only parse HTTP parameters and map domain errors to status codes.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncEngine

from app import plant_data
from app.db.deps import get_engine
from app.plant_data import Bucket, InvalidQueryError, NotFoundError
from app.schemas import (
    AlarmLog,
    ErrorOut,
    InvalidRequestOut,
    MachineDetail,
    MachineOut,
    ReadingsOut,
    error,
)

router = APIRouter(prefix="/machines", tags=["machines"])

Engine = Annotated[AsyncEngine, Depends(get_engine)]


def _http(e: NotFoundError | InvalidQueryError) -> HTTPException:
    return HTTPException(404 if isinstance(e, NotFoundError) else 422, str(e))


@router.get("", response_model=list[MachineOut])
async def list_machines(
    engine: Engine,
    line: Annotated[str | None, Query(description='Filter by line, e.g. "Line 3"')] = None,
) -> list[MachineOut]:
    """All machines (optionally of one line), ordered by code."""
    async with engine.connect() as conn:
        return await plant_data.list_machines(conn, line)


@router.get("/{code}", response_model=MachineDetail, responses={404: error("No such machine")})
async def get_machine(code: str, engine: Engine) -> MachineDetail:
    """One machine with its sensors and their normal operating ranges."""
    try:
        async with engine.connect() as conn:
            return await plant_data.get_machine(conn, code)
    except NotFoundError as e:
        raise _http(e) from e


@router.get(
    "/{code}/readings",
    response_model=ReadingsOut,
    responses={
        404: error("No such machine or sensor"),
        422: error("Malformed request, or an invalid time range", InvalidRequestOut | ErrorOut),
    },
)
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
    try:
        async with engine.connect() as conn:
            return await plant_data.get_readings(conn, code, sensor, start, end, bucket)
    except (NotFoundError, InvalidQueryError) as e:
        raise _http(e) from e


@router.get(
    "/{code}/alarms",
    response_model=AlarmLog,
    responses={
        404: error("No such machine"),
        422: error("Malformed request, or an invalid range / limit", InvalidRequestOut | ErrorOut),
    },
)
async def get_alarms(
    code: str,
    engine: Engine,
    start: Annotated[datetime | None, Query(description="ISO 8601 with timezone")] = None,
    end: Annotated[
        datetime | None, Query(description="ISO 8601 with timezone; default now")
    ] = None,
    limit: Annotated[int, Query(description="At most this many, newest first")] = 100,
    min_severity: Annotated[str | None, Query(description="info, warning or critical")] = None,
) -> AlarmLog:
    """One machine's alarms in [start, end), newest first (7.3: the machine page's timeline).
    `truncated` + `total_count` say when the list is cut: then the EARLIEST alarms (when the
    problem started) are the ones missing."""
    try:
        query = plant_data.validate_alarm_query(start, end, limit, min_severity)
        async with engine.connect() as conn:
            return await plant_data.get_alarm_log(conn, code, query)
    except (NotFoundError, InvalidQueryError) as e:
        raise _http(e) from e
