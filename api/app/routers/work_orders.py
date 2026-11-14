"""Work orders (6.2): the approval queue's and the technician's view. See app/work_orders.py
for why there is no create, no free update and no delete."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncEngine

from app import work_orders
from app.db.deps import get_engine
from app.db.models import WorkOrderStatus
from app.errors import NotFoundError
from app.schemas import CompleteIn, error
from app.work_orders import InvalidTransition, WorkOrderDetail, WorkOrderSummary

router = APIRouter(prefix="/work-orders", tags=["work orders"])

Engine = Annotated[AsyncEngine, Depends(get_engine)]


@router.get("", response_model=list[WorkOrderSummary])
async def list_work_orders(
    engine: Engine,
    status: Annotated[WorkOrderStatus | None, Query(description="e.g. approved")] = None,
    incident_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WorkOrderSummary]:
    """Newest first."""
    async with engine.connect() as conn:
        return await work_orders.list_work_orders(conn, status, incident_id, limit)


@router.get(
    "/{work_order_id}",
    response_model=WorkOrderDetail,
    responses={404: error("No such work order")},
)
async def get_work_order(work_order_id: int, engine: Engine) -> WorkOrderDetail:
    """One work order with the complete document that was decided on."""
    try:
        async with engine.connect() as conn:
            return await work_orders.get_work_order(conn, work_order_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.post(
    "/{work_order_id}/complete",
    response_model=WorkOrderDetail,
    responses={
        404: error("No such work order"),
        409: error("This status change is not allowed (only approved -> done)"),
    },
)
async def complete(work_order_id: int, body: CompleteIn, engine: Engine) -> WorkOrderDetail:
    """Mark an APPROVED order as done, with who did it; the time is the server's."""
    try:
        async with engine.begin() as conn:  # one transaction: the row lock holds until commit
            return await work_orders.complete(conn, work_order_id, body.by, datetime.now(UTC))
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except InvalidTransition as e:
        raise HTTPException(409, str(e)) from e
