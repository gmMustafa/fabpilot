"""SqlWorkOrderStore against a throwaway migrated database: rows, transaction, DB constraints."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.agent.approval import Decision
from app.agent.store import SqlWorkOrderStore
from app.db.models import Incident, Machine, MachineType, WorkOrder, WorkOrderStatus
from app.errors import NotFoundError
from tests.test_agent_graph import NOW
from tests.test_work_order import draft

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    async with async_sessionmaker(eng).begin() as s:
        s.add(
            Machine(
                code="M-08",
                name="Screw compressor",
                line="Line 2",
                type=MachineType.COMPRESSOR,
                install_date=date(2020, 1, 1),
            )
        )
    yield eng
    await eng.dispose()


def decision(**kw: object) -> Decision:
    base: dict[str, object] = {
        "machine": "M-08",
        "question": "Is the compressor overheating?",
        "root_cause": "Faulty discharge_temp RTD",
        "draft": draft(),
        "status": "approved",
        "by": "M. Engineer",
        "at": NOW,
        "note": None,
    }
    return Decision(**{**base, **kw})  # type: ignore[arg-type]


async def test_approval_writes_an_incident_and_the_signed_draft(engine: AsyncEngine) -> None:
    wo_id = await SqlWorkOrderStore(engine).save(decision())
    async with async_sessionmaker(engine)() as s:
        wo = await s.get_one(WorkOrder, wo_id)
        incident = await s.get_one(Incident, wo.incident_id)
    assert (wo.status, wo.approved_by, wo.approved_at) == (
        WorkOrderStatus.APPROVED,
        "M. Engineer",
        NOW,
    )
    assert wo.draft == draft().model_dump(mode="json")  # exactly what was signed
    assert wo.actions[1]["step"] == 2 and wo.actions[1]["safety"] is True
    assert incident.root_cause == "Faulty discharge_temp RTD"


async def test_rejection_keeps_who_and_why(engine: AsyncEngine) -> None:
    store = SqlWorkOrderStore(engine)
    wo_id = await store.save(decision(status="rejected", note="RTD replaced yesterday"))
    async with async_sessionmaker(engine)() as s:
        wo = await s.get_one(WorkOrder, wo_id)
    assert (wo.status, wo.rejected_by, wo.review_note) == (
        WorkOrderStatus.REJECTED,
        "M. Engineer",
        "RTD replaced yesterday",
    )
    assert wo.approved_by is None


async def test_unknown_machine_writes_nothing(engine: AsyncEngine) -> None:
    with pytest.raises(NotFoundError):
        await SqlWorkOrderStore(engine).save(decision(machine="M-99"))
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(Incident)) == 0


async def test_the_database_refuses_a_rejection_without_a_reason(engine: AsyncEngine) -> None:
    """The last line of defence, even for a caller that skips check_approval."""
    await SqlWorkOrderStore(engine).save(decision())
    with pytest.raises(IntegrityError, match="rejection_recorded"):
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE work_orders SET status = 'rejected'"))
