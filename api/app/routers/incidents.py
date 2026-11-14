"""Incidents and their investigations (6.1): create, investigate (SSE), and read a run.

    POST /incidents                     {machine, question} -> 201 {id, ...} + Location
    POST /incidents/{id}/investigate    -> text/event-stream:
                                           step* (one per graph node), report, approval_required?,
                                           error?, done   (every event has an id: 1, 2, 3 ...)
    GET  /runs/{thread_id}              -> the run's state, e.g. after a dropped stream

Why the run is started in a DEPENDENCY: an SSE response commits to "200 text/event-stream" with
its first byte, so "all slots busy" (429) or "no such incident" (404) must be decided BEFORE the
stream starts. FastAPI resolves dependencies first, then starts streaming the generator.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.agent.service import AgentService, Busy, RunHandle, usage
from app.db.deps import get_agent
from app.errors import NotFoundError
from app.schemas import IncidentIn, IncidentOut, RunOut

router = APIRouter(tags=["incidents"])

Agent = Annotated[AgentService, Depends(get_agent)]

RETRY_AFTER_SECONDS = 30  # a typical run takes 10-60 s: by then a slot is likely free
# 429 vs 503 (6.1b quiz): 429 strictly means "YOU sent too many"; our limit is GLOBAL, so 503
# ("can't take more right now") is arguably more precise. Both are common; 429 is kept because
# clients and HTTP libraries treat it as "wait Retry-After, then retry".


@router.post("/incidents", response_model=IncidentOut, status_code=201)
async def create_incident(body: IncidentIn, agent: Agent, response: Response) -> IncidentOut:
    """Open an incident for one machine: the thing an investigation is ABOUT."""
    try:
        incident_id = await agent.create_incident(body.machine, body.question)
    except NotFoundError as e:
        raise HTTPException(404, str(e)) from e
    response.headers["Location"] = f"/incidents/{incident_id}"
    return IncidentOut(id=incident_id, machine=body.machine, question=body.question)


async def start_investigation(incident_id: int, agent: Agent) -> RunHandle:
    """Start the run BEFORE the stream: the last moment an HTTP error status is still possible.

    - every slot busy (Busy)       -> 429 Too Many Requests, header Retry-After: RETRY_AFTER_SECONDS
                                      (not an error of the client: "wait and try again")
    - unknown incident (NotFound)  -> 404, with the error's message as the detail
    - otherwise                    -> the RunHandle (the generator streams its events)
    """
    try:
        return await agent.start(incident_id)
    except Busy as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        ) from e
    except NotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=str(e),
        ) from e


@router.post(
    "/incidents/{incident_id}/investigate",
    response_class=EventSourceResponse,
    responses={404: {"description": "No such incident"}, 429: {"description": "All slots busy"}},
)
async def investigate(
    handle: Annotated[RunHandle, Depends(start_investigation)],
) -> AsyncIterator[ServerSentEvent]:
    """Investigate an incident; streams the agent's steps live (Server-Sent Events). Closing the
    stream does NOT stop the run: catch up with GET /runs/{thread_id} (in the `done` event)."""
    n = 0
    while (event := await handle.events.get()) is not None:
        n += 1
        yield ServerSentEvent(event=event.event, data=event.data, id=str(n))


@router.get("/runs/{thread_id}", response_model=RunOut)
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
        pending_approval=pending,
        usage=usage(state),
    )
