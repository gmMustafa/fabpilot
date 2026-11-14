"""Work orders after the decision (6.2): read them, and mark approved ones as done.

Deliberately NOT a full CRUD, because a full CRUD would undermine the human-in-the-loop:
- Create: ONLY through a decision on an agent run (POST /runs/{id}/decision). A
  POST /work-orders would be a back door: an order no agent drafted and nobody approved.
- Read: list (filter by status / incident) and one by id.
- Update: only allowed STATUS TRANSITIONS (check_transition), each with who and when.
- Delete: never. The audit trail stays; a rejected order is a record too.

Like plant_data: no HTTP here; the router maps NotFoundError -> 404, InvalidTransition -> 409.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import WorkOrder, WorkOrderPriority, WorkOrderStatus
from app.errors import NotFoundError


class InvalidTransition(Exception):
    """The work order's current status doesn't allow the requested one (HTTP 409)."""


class WorkOrderSummary(BaseModel):
    id: int
    incident_id: int
    thread_id: str | None
    status: WorkOrderStatus
    priority: WorkOrderPriority
    title: str | None  # from the signed draft
    sop_ref: str | None
    created_at: datetime
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    review_note: str | None
    completed_by: str | None
    completed_at: datetime | None


class WorkOrderDetail(WorkOrderSummary):
    draft: dict[str, Any] | None  # the complete document that was decided on


def check_transition(current: WorkOrderStatus, target: WorkOrderStatus) -> str | None:
    """Why `current` may NOT become `target`, or None if it may.

    The only transition the API allows is approved -> done. (draft -> approved / rejected
    happens through the agent run's decision, never here: orders are stored decided.)
    - target == current  -> f"work order is already {current.value}"
    - any other pair     -> f"a {current.value} work order can't become {target.value}
                             (allowed: approved -> done)"         (ONE line in the real message)

    Why this matters more than it looks: a "done" REJECTED order would claim that work happened
    which a human explicitly refused; a "done" order done twice would hide who really did it.
    """
    if target == current:
        return f"work order is already {current.value}"

    if current == WorkOrderStatus.APPROVED and target == WorkOrderStatus.DONE:
        return None

    return f"a {current.value} work order can't become {target.value} (allowed: approved -> done)"


def _summary_fields(wo: WorkOrder) -> dict[str, Any]:
    draft = wo.draft or {}
    return {
        "id": wo.id,
        "incident_id": wo.incident_id,
        "thread_id": wo.thread_id,
        "status": wo.status,
        "priority": wo.priority,
        "title": draft.get("title"),
        "sop_ref": draft.get("sop_ref"),
        "created_at": wo.created_at,
        "approved_by": wo.approved_by,
        "approved_at": wo.approved_at,
        "rejected_by": wo.rejected_by,
        "review_note": wo.review_note,
        "completed_by": wo.completed_by,
        "completed_at": wo.completed_at,
    }


def _detail(wo: WorkOrder) -> WorkOrderDetail:
    return WorkOrderDetail(**_summary_fields(wo), draft=wo.draft)


async def list_work_orders(
    conn: AsyncConnection,
    status: WorkOrderStatus | None = None,
    incident_id: int | None = None,
    limit: int = 50,
) -> list[WorkOrderSummary]:
    """Newest first. The approval queue (7.5) and the technician's list are filters on this."""
    query = select(WorkOrder).order_by(WorkOrder.id.desc()).limit(limit)
    if status is not None:
        query = query.where(WorkOrder.status == status)
    if incident_id is not None:
        query = query.where(WorkOrder.incident_id == incident_id)
    rows = (await conn.execute(query)).all()
    return [WorkOrderSummary(**_summary_fields(WorkOrder(**row._mapping))) for row in rows]


async def get_work_order(conn: AsyncConnection, work_order_id: int) -> WorkOrderDetail:
    row = (await conn.execute(select(WorkOrder).where(WorkOrder.id == work_order_id))).first()
    if row is None:
        raise NotFoundError(f"work order {work_order_id} does not exist")
    return _detail(WorkOrder(**row._mapping))


async def complete(
    conn: AsyncConnection, work_order_id: int, by: str, at: datetime
) -> WorkOrderDetail:
    """approved -> done, with who and when. Call inside a transaction (engine.begin()).

    SELECT ... FOR UPDATE locks the row until the transaction ends: two "done" clicks at once
    are serialised, and the second sees "already done" (409) instead of overwriting the first
    one's name. The DB rule completion_recorded is the last line of defence."""
    row = (
        await conn.execute(
            select(WorkOrder.status).where(WorkOrder.id == work_order_id).with_for_update()
        )
    ).first()
    if row is None:
        raise NotFoundError(f"work order {work_order_id} does not exist")
    if reason := check_transition(row.status, WorkOrderStatus.DONE):
        raise InvalidTransition(reason)
    await conn.execute(
        update(WorkOrder)
        .where(WorkOrder.id == work_order_id)
        .values(status=WorkOrderStatus.DONE, completed_by=by, completed_at=at)
    )
    return await get_work_order(conn, work_order_id)
