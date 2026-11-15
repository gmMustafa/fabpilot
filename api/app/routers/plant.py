"""The plant overview (7.2): every machine's tile in ONE request, for the dashboard's grid."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncEngine

from app import plant_data
from app.db.deps import get_engine
from app.schemas import PlantOverview

router = APIRouter(prefix="/plant", tags=["plant"])

Engine = Annotated[AsyncEngine, Depends(get_engine)]


@router.get("/overview", response_model=PlantOverview)
async def plant_overview(engine: Engine) -> PlantOverview:
    """All machines by line with their ACTIVE alarms (count, worst severity, newest). The
    dashboard polls this every few seconds: one request for the whole plant."""
    async with engine.connect() as conn:
        return await plant_data.plant_overview(conn, datetime.now(UTC))
