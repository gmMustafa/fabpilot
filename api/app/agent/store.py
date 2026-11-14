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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.approval import Decision
from app.agent.service import IncidentRef
from app.agent.state import AgentState
from app.db.models import (
    AgentRun,
    Incident,
    IncidentStatus,
    Machine,
    RunStatus,
    WorkOrder,
    WorkOrderStatus,
)
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

            incident: Incident | None = None
            if decision.incident_id is not None:  # a run of an existing incident (6.1)
                incident = await session.get(Incident, decision.incident_id)
                if incident is None:
                    raise NotFoundError(f"incident {decision.incident_id} does not exist")
                incident.root_cause = decision.root_cause  # the decided diagnosis, kept on it
            if incident is None:  # the CLI: no incident yet, the decision creates one
                incident = Incident(
                    title=decision.question[:200],
                    machine_id=machine_id,
                    status=IncidentStatus.OPEN,  # open until the work is DONE, not on approval
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


class SqlAgentRepo(SqlWorkOrderStore):
    """The AgentService's database side (6.1): incidents, the agent_runs log, and (inherited)
    the idempotent work-order save."""

    async def create_incident(self, machine: str, question: str) -> int:
        async with self._sessions.begin() as session:
            machine_id = await session.scalar(select(Machine.id).where(Machine.code == machine))
            if machine_id is None:
                raise NotFoundError(f"machine {machine!r} does not exist")
            incident = Incident(title=question, machine_id=machine_id)
            session.add(incident)
            await session.flush()
            return incident.id

    async def incident(self, incident_id: int) -> IncidentRef | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(Machine.code, Incident.title)
                    .join(Machine, Machine.id == Incident.machine_id)
                    .where(Incident.id == incident_id)
                )
            ).first()
        return IncidentRef(machine=row.code, question=row.title) if row else None

    async def run_started(self, thread_id: str, incident_id: int) -> None:
        async with self._sessions.begin() as session:
            session.add(AgentRun(thread_id=thread_id, incident_id=incident_id))

    async def run_finished(
        self, thread_id: str, status: RunStatus, state: AgentState | None, error: str | None
    ) -> None:
        async with self._sessions.begin() as session:
            run = await session.get_one(AgentRun, thread_id)
            run.status, run.error, run.finished_at = status, error, func.now()
            if state is not None:
                run.llm_requests = state.llm_requests
                run.llm_attempts = state.llm_attempts
                run.llm_tokens = state.llm_tokens
