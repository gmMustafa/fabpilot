"""Agent runs (6.1/6.2): read a run's state, and hand a paused run the human's decision.

    GET  /runs/{thread_id}           -> state: running / paused / done / failed, answer, draft
    GET  /runs/{thread_id}/events    -> the run's events as SSE: replay, then live (7.4)
    POST /runs/{thread_id}/decision  {action, by, edited?, reason?}
         200  saved: {status: approved | rejected, work_order_id}
         404  no such run
         409  the run isn't waiting (still running, or already decided): the REQUEST is fine,
              the resource's STATE doesn't allow it
         422  not acceptable: a malformed body (pydantic), or check_approval sent it back (no
              name, no reason, an unsafe edit); then the run STAYS PAUSED, the reasons are in
              the detail, and the same endpoint takes the corrected decision

Answers at once, no stream: after a decision only await_approval and save_work_order run.
Known gap: `by` comes from the request body (no login yet), so anyone could type any name.
Real authentication is its own step; until then the name is recorded, not verified.
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent.service import AgentService, NotWaiting, RunEvent, SentBack, usage
from app.agent.state import Approval
from app.db.deps import get_agent
from app.errors import NotFoundError
from app.schemas import DecisionOut, InvalidRequestOut, RunOut, SentBackOut, error

router = APIRouter(prefix="/runs", tags=["runs"])

Agent = Annotated[AgentService, Depends(get_agent)]


@router.post(
    "/{thread_id}/decision",
    response_model=DecisionOut,
    responses={
        404: error("No such run"),
        409: error("The run is not waiting for a decision (already decided, or still running)"),
        422: error(
            "Sent back by the checks (the run stays paused), or a malformed body",
            SentBackOut | InvalidRequestOut,
        ),
    },
)
async def decide(thread_id: str, approval: Approval, agent: Agent) -> DecisionOut:
    """Approve, edit or reject the draft a paused run is waiting on. Nothing is saved before
    this, and an edited draft goes through the same safety checks as the model's draft."""
    try:
        state = await agent.decide(thread_id, approval)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except NotWaiting as e:
        raise HTTPException(409, str(e)) from e
    except SentBack as e:
        raise HTTPException(
            422, {"message": "decision sent back; the run is still waiting", "errors": e.errors}
        ) from e
    assert state.work_order_id is not None  # decide() raises SentBack otherwise
    status: Literal["approved", "rejected"] = (
        "rejected" if approval.action == "reject" else "approved"
    )
    return DecisionOut(thread_id=thread_id, status=status, work_order_id=state.work_order_id)


@router.get("/{thread_id}", response_model=RunOut, responses={404: error("No such run")})
async def get_run(thread_id: str, agent: Agent) -> RunOut:
    """A run's state: live from this process, or from the checkpointer (any process, a restart)."""
    found = await agent.state(thread_id)
    if found is None:
        raise HTTPException(404, f"run {thread_id!r} does not exist")
    state, pending = found
    if agent.is_running(thread_id):
        status = "running"
    elif pending is not None:
        status = "paused"
    else:
        status = "done" if state.answer is not None else "failed"
    return RunOut(
        thread_id=thread_id,
        status=status,
        answer=state.answer,
        machine=state.machine,
        data_ends_at=state.data_ends_at,
        hypothesis=state.hypothesis,
        pending_approval=pending,
        usage=usage(state),
    )


async def open_events(
    thread_id: str,
    agent: Agent,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> AsyncIterator[tuple[int, RunEvent]]:
    """Resolve the stream BEFORE it starts (an unknown run is still a 404 here, not an event).
    The browser sends Last-Event-ID by itself when it reconnects after a dropout."""
    try:
        return await agent.events(thread_id, last_event_id)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e


@router.get(
    "/{thread_id}/events",
    response_class=EventSourceResponse,
    responses={404: error("No such run")},
)
async def run_events(
    events: Annotated[AsyncIterator[tuple[int, RunEvent]], Depends(open_events)],
) -> AsyncIterator[ServerSentEvent]:
    """A run's events (7.4): step* (one per graph node), report, approval_required?, error?,
    done, each with an id. Any number of readers; each gets everything so far (or what came
    after its Last-Event-ID), then follows live until `done`. A run from before a restart:
    a `notice`, then its result rebuilt from the checkpoint."""
    async for event_id, event in events:
        yield ServerSentEvent(event=event.event, data=event.data, id=str(event_id))
