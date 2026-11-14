"""POST /runs/{id}/decision (6.2) over HTTP, in process, with the AgentService of fakes."""

import asyncio
from typing import Any

from httpx import AsyncClient

from app.agent.service import AgentService
from tests.test_incidents_api import client_for
from tests.test_service import FakeRepo, drain, q1_chat, service
from tests.test_work_order import draft

ME = "M. Engineer"


async def paused_run() -> tuple[AgentService, FakeRepo, str]:
    repo = FakeRepo()
    agent = service(q1_chat(), repo)
    handle = await agent.start(7)
    await drain(handle)  # the run is now paused at the draft
    return agent, repo, handle.thread_id


async def decide(c: AsyncClient, thread_id: str, **body: Any) -> Any:
    return await c.post(f"/runs/{thread_id}/decision", json=body)


async def test_approve_saves_and_the_run_is_done() -> None:
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        r = await decide(c, tid, action="approve", by=ME)
        run = await c.get(f"/runs/{tid}")
    assert r.status_code == 200
    assert r.json() == {"thread_id": tid, "status": "approved", "work_order_id": 1}
    assert [(d.status, d.by, d.incident_id) for d in repo.saved] == [("approved", ME, 7)]
    assert run.json()["status"] == "done" and repo.finished[tid][0] == "done"


async def test_reject_with_a_reason() -> None:
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        r = await decide(c, tid, action="reject", by=ME, reason="RTD replaced yesterday")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert repo.saved[0].note == "RTD replaced yesterday"


async def test_sent_back_is_422_with_reasons_and_the_run_stays_paused() -> None:
    """No reason for a rejection: check_approval sends it back. Then a corrected one works."""
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        r = await decide(c, tid, action="reject", by=ME)
        assert r.status_code == 422
        assert r.json()["detail"]["errors"] == [
            "a rejection needs a reason (it is kept for the audit trail)"
        ]
        assert (await c.get(f"/runs/{tid}")).json()["status"] == "paused"
        assert repo.saved == []
        fixed = await decide(c, tid, action="reject", by=ME, reason="not needed")
    assert fixed.status_code == 200 and len(repo.saved) == 1


async def test_an_unsafe_edit_is_sent_back() -> None:
    """The API is a way in too: an edit without the hold point is refused like the model's."""
    unsafe = draft(actions=[a.model_copy(update={"safety": False}) for a in draft().actions])
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        r = await decide(c, tid, action="edit", by=ME, edited=unsafe.model_dump(mode="json"))
    assert r.status_code == 422 and repo.saved == []
    assert r.json()["detail"]["errors"][0].startswith("your edit: hold point at step 3")


async def test_a_malformed_body_is_422_from_pydantic_before_the_run_is_touched() -> None:
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        r = await decide(c, tid, action="maybe", by=ME)
    assert r.status_code == 422 and repo.saved == []


async def test_a_second_decision_is_409() -> None:
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        await decide(c, tid, action="approve", by=ME)
        again = await decide(c, tid, action="reject", by="someone else", reason="no")
    assert again.status_code == 409 and "already decided" in again.json()["detail"]
    assert len(repo.saved) == 1


async def test_two_decisions_at_the_same_moment_give_one_200_one_409_and_one_order() -> None:
    """A double click, or two reviewers at once: without the lock, both would resume."""
    agent, repo, tid = await paused_run()
    async with client_for(agent) as c:
        first, second = await asyncio.gather(
            decide(c, tid, action="approve", by=ME),
            decide(c, tid, action="approve", by="another reviewer"),
        )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(repo.saved) == 1


async def test_unknown_run_is_404() -> None:
    agent, _, _ = await paused_run()
    async with client_for(agent) as c:
        assert (await decide(c, "nope", action="approve", by=ME)).status_code == 404
