"""Work orders (6.2): check_transition (your TODO), and the endpoints against a real database."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import work_orders
from app.agent.store import SqlAgentRepo
from app.db.deps import get_engine
from app.db.models import WorkOrderStatus
from app.main import app
from app.work_orders import InvalidTransition, check_transition
from tests.test_agent_repo import engine  # noqa: F401  (the fixture: a migrated DB with M-08)
from tests.test_work_order_store import decision

S = WorkOrderStatus


# ------------------------------------------------------------------ check_transition (your TODO)


def test_transition_approved_to_done_is_the_only_allowed_one() -> None:
    assert check_transition(S.APPROVED, S.DONE) is None


@pytest.mark.parametrize("current", [S.DRAFT, S.REJECTED])
def test_transition_to_done_from_anything_but_approved_is_refused(current: WorkOrderStatus) -> None:
    """A "done" REJECTED order would claim work happened that a human explicitly refused."""
    assert check_transition(current, S.DONE) == (
        f"a {current.value} work order can't become done (allowed: approved -> done)"
    )


def test_transition_done_twice_is_refused() -> None:
    """...and would overwrite who really did it."""
    assert check_transition(S.DONE, S.DONE) == "work order is already done"


@pytest.mark.parametrize("target", [S.APPROVED, S.REJECTED, S.DRAFT])
def test_transition_nothing_else_goes_through_this_door(target: WorkOrderStatus) -> None:
    """approved / rejected come from a decision on the agent run, never from here."""
    assert check_transition(S.APPROVED, target) is not None


# ------------------------------------------------------------------ the endpoints (database)


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:  # noqa: F811
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def two_orders(engine: AsyncEngine) -> tuple[int, int]:  # noqa: F811
    """One approved, one rejected, on the same incident."""
    repo = SqlAgentRepo(engine)
    incident_id = await repo.create_incident("M-08", "Overheating?")
    approved = await repo.save(decision(thread_id="t-a", incident_id=incident_id))
    rejected = await repo.save(
        decision(thread_id="t-r", incident_id=incident_id, status="rejected", note="not needed")
    )
    return approved, rejected


@pytest.mark.integration
async def test_list_filter_and_get(engine: AsyncEngine, client: AsyncClient) -> None:  # noqa: F811
    approved, rejected = await two_orders(engine)
    all_ = (await client.get("/work-orders")).json()
    assert [w["id"] for w in all_] == [rejected, approved]  # newest first
    only = (await client.get("/work-orders", params={"status": "approved"})).json()
    assert [w["id"] for w in only] == [approved]
    one = (await client.get(f"/work-orders/{approved}")).json()
    assert one["title"] == "Verify the M-08 discharge_temp RTD" and one["sop_ref"] == "SOP-GEN-004"
    assert one["draft"]["actions"][1]["safety"] is True  # the signed document
    assert (await client.get("/work-orders/999")).status_code == 404


@pytest.mark.integration
async def test_complete_an_approved_order(engine: AsyncEngine, client: AsyncClient) -> None:  # noqa: F811
    approved, _ = await two_orders(engine)
    r = await client.post(f"/work-orders/{approved}/complete", json={"by": "  T. Technician "})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done" and body["completed_by"] == "T. Technician"  # stripped
    assert body["completed_at"] is not None and body["approved_by"] == "M. Engineer"


@pytest.mark.integration
async def test_complete_refuses_rejected_twice_and_nameless(
    engine: AsyncEngine,  # noqa: F811
    client: AsyncClient,
) -> None:
    approved, rejected = await two_orders(engine)
    r = await client.post(f"/work-orders/{rejected}/complete", json={"by": "T"})
    assert r.status_code == 409 and "a rejected work order can't become done" in r.json()["detail"]
    await client.post(f"/work-orders/{approved}/complete", json={"by": "T"})
    again = await client.post(f"/work-orders/{approved}/complete", json={"by": "U"})
    assert again.status_code == 409 and again.json()["detail"] == "work order is already done"
    assert (
        await client.post(f"/work-orders/{approved}/complete", json={"by": " "})
    ).status_code == 422
    assert (await client.post("/work-orders/999/complete", json={"by": "T"})).status_code == 404


@pytest.mark.integration
async def test_a_second_completion_waits_for_the_first_and_cannot_overwrite_it(
    engine: AsyncEngine,  # noqa: F811
) -> None:
    """DETERMINISTIC race (an HTTP gather test ran the requests one after the other and passed
    even WITHOUT the row lock: it proved nothing). A completes but holds its transaction open;
    B must WAIT on the row lock, then see "already done". Without FOR UPDATE, B reads
    "approved" at once and later overwrites A's name: this test fails (mutation-checked)."""
    approved, _ = await two_orders(engine)
    now = datetime.now(UTC)
    async with engine.connect() as a_conn, engine.connect() as b_conn:
        a_tx = await a_conn.begin()
        await work_orders.complete(a_conn, approved, "first", now)  # locked, not committed

        b_tx = await b_conn.begin()
        second = asyncio.create_task(work_orders.complete(b_conn, approved, "second", now))
        await asyncio.sleep(0.5)
        assert not second.done()  # B is BLOCKED on A's row lock

        await a_tx.commit()
        with pytest.raises(InvalidTransition, match="already done"):
            await second
        await b_tx.rollback()
    async with engine.connect() as conn:
        assert (await work_orders.get_work_order(conn, approved)).completed_by == "first"


@pytest.mark.integration
async def test_the_database_refuses_done_without_who_and_when(engine: AsyncEngine) -> None:  # noqa: F811
    approved, _ = await two_orders(engine)
    with pytest.raises(IntegrityError, match="completion_recorded"):
        async with engine.begin() as conn:
            await conn.execute(
                text(f"UPDATE work_orders SET status = 'done' WHERE id = {approved}")
            )
