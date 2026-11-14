"""SqlAgentRepo against a throwaway migrated database (6.1): incidents, run log, attach on save."""

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.agent.service import IncidentRef
from app.agent.state import AgentState
from app.agent.store import SqlAgentRepo
from app.db.models import AgentRun, Incident, Machine, MachineType, RunStatus, WorkOrder
from app.errors import NotFoundError
from tests.test_work_order_store import decision

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


async def test_an_incident_round_trips(engine: AsyncEngine) -> None:
    repo = SqlAgentRepo(engine)
    incident_id = await repo.create_incident("M-08", "Is it overheating?")
    assert await repo.incident(incident_id) == IncidentRef("M-08", "Is it overheating?")
    assert await repo.incident(999) is None
    with pytest.raises(NotFoundError, match="M-99"):
        await repo.create_incident("M-99", "x")


async def test_the_run_log_records_status_and_usage(engine: AsyncEngine) -> None:
    repo = SqlAgentRepo(engine)
    incident_id = await repo.create_incident("M-08", "q")
    await repo.run_started("t-1", incident_id)
    state = AgentState(question="q", llm_requests=2, llm_attempts=3, llm_tokens=5_700)
    await repo.run_finished("t-1", RunStatus.PAUSED, state, None)
    async with async_sessionmaker(engine)() as s:
        run = await s.get_one(AgentRun, "t-1")
    assert (run.status, run.llm_attempts, run.llm_tokens) == (RunStatus.PAUSED, 3, 5_700)
    assert run.finished_at is not None and run.error is None


async def test_a_decision_attaches_to_the_runs_incident_instead_of_a_new_one(
    engine: AsyncEngine,
) -> None:
    repo = SqlAgentRepo(engine)
    incident_id = await repo.create_incident("M-08", "q")
    wo_id = await repo.save(decision(incident_id=incident_id))
    async with async_sessionmaker(engine)() as s:
        wo = await s.get_one(WorkOrder, wo_id)
        incident = await s.get_one(Incident, incident_id)
        incidents = await s.scalar(select(func.count()).select_from(Incident))
    assert wo.incident_id == incident_id and incidents == 1  # no second incident
    assert incident.root_cause == "Faulty discharge_temp RTD"  # the decided diagnosis
