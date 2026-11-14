"""The Postgres WorkOrderStore (5.3): an incident + a work order per decision, in ONE transaction.

Either both rows exist or neither: a work order without its incident (or the reverse) would be
an audit record nobody can explain. The DB constraints (approval_recorded, rejection_recorded)
are the last line of defence: even a buggy caller can't store "approved" without who and when.

Idempotent per agent run (5.4), in two layers:
1. look first: this run already saved -> return that id (the save node ran twice: crash
   between commit and checkpoint, a retry after a timeout);
2. UNIQUE(thread_id): two saves at the SAME moment both look, both find nothing, both insert;
   the database lets one win and the other gets an IntegrityError -> return the winner's id.
Layer 1 alone has a race. Layer 2 alone is correct but turns every normal repeat into a FAILED
insert (rollback, an ERROR in the Postgres log, burned sequence numbers). Layer 1 is the normal
path, layer 2 the rare one.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.approval import Decision
from app.db.models import Incident, IncidentStatus, Machine, WorkOrder, WorkOrderStatus
from app.errors import NotFoundError


class SqlWorkOrderStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def save(self, decision: Decision) -> int:
        try:
            return await self._save_once(decision)
        except IntegrityError:
            # Layer 2: lost the race to a concurrent save of the same run? Then its row is the
            # answer. Any OTHER constraint violation (no such row) is a real error: re-raise.
            existing = await self._existing_id(decision.thread_id)
            if existing is None:
                raise
            return existing

    async def _existing_id(self, thread_id: str) -> int | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(WorkOrder.id).where(WorkOrder.thread_id == thread_id)
            )

    async def _save_once(self, decision: Decision) -> int:
        approved = decision.status == "approved"
        async with self._sessions.begin() as session:  # commits on exit, rolls back on error
            # Layer 1: this run was already saved (the save node ran twice) -> its id, BEFORE
            # anything is inserted: no second incident, no failed INSERT, no burned id.
            existing_id = await session.scalar(
                select(WorkOrder.id).where(WorkOrder.thread_id == decision.thread_id)
            )

            if existing_id is not None:
                return existing_id

            # Only look up the machine for a new work order
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
                thread_id=decision.thread_id,
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
