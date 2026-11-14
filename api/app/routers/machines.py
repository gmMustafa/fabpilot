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
from app.schemas import MachineDetail, MachineOut, ReadingsOut

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


@router.get("/{code}", response_model=MachineDetail)
async def get_machine(code: str, engine: Engine) -> MachineDetail:
    """One machine with its sensors and their normal operating ranges."""
    try:
        async with engine.connect() as conn:
            return await plant_data.get_machine(conn, code)
    except NotFoundError as e:
        raise _http(e) from e


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
    try:
        async with engine.connect() as conn:
            return await plant_data.get_readings(conn, code, sensor, start, end, bucket)
    except (NotFoundError, InvalidQueryError) as e:
        raise _http(e) from e
