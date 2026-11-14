"""The Postgres WorkOrderStore (5.3): an incident + a work order per decision, in ONE transaction.

Either both rows exist or neither: a work order without its incident (or the reverse) would be
an audit record nobody can explain. The DB constraints (approval_recorded, rejection_recorded)
are the last line of defence: even a buggy caller can't store "approved" without who and when.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.approval import Decision
from app.db.models import Incident, IncidentStatus, Machine, WorkOrder, WorkOrderStatus
from app.errors import NotFoundError


class SqlWorkOrderStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def save(self, decision: Decision) -> int:
        approved = decision.status == "approved"
        async with self._sessions.begin() as session:  # commits on exit, rolls back on error
            machine_id = await session.scalar(
                select(Machine.id).where(Machine.code == decision.machine)
            )
            if machine_id is None:
                raise NotFoundError(f"machine {decision.machine!r} does not exist")
            incident = Incident(
                title=decision.question[:200],
                machine_id=machine_id,
                status=IncidentStatus.OPEN,  # open until the work is DONE, approval doesn't fix it
                root_cause=decision.root_cause,
            )
            d = decision.draft
            work_order = WorkOrder(
                incident=incident,
                draft=d.model_dump(mode="json"),
                actions=[{"step": i, **a.model_dump()} for i, a in enumerate(d.actions, start=1)],
                parts=[p.model_dump() for p in d.parts],
                priority=d.priority,
                status=WorkOrderStatus.APPROVED if approved else WorkOrderStatus.REJECTED,
                approved_by=decision.by if approved else None,
                approved_at=decision.at if approved else None,
                rejected_by=None if approved else decision.by,
                review_note=decision.note,
            )
            session.add(work_order)
            await session.flush()  # assigns the id inside the transaction
            return work_order.id
