"""Agent runs inside a long-lived process (6.1): the API's way to start and watch investigations.

    start(incident_id) --checks--> a slot? the incident? --> RunHandle(thread_id, events, task)
                                   (Busy / NotFoundError are raised HERE, before anything runs)
    the run TASK:  McpTools (per run) -> graph.astream(...) -> one RunEvent per step -> queue
                   -> "report" (the answer) -> "approval_required" (if paused) -> "done"

THE STREAM IS A VIEW, NOT THE RUN: the task owns the run; a reader (the SSE response, 6.1b) only
drains the queue. A browser that closes the tab stops reading; the run goes on, its state lands
in the checkpointer, and GET /runs/{thread_id} shows the result later.

Why at most `max_concurrent` runs (default 2): the free tier allows ~20 requests/minute; ten
clicks at once would spend that in seconds. A third run is refused with Busy (HTTP 429), not
queued: a queue would hide from the engineer that nothing is happening.

No FastAPI in here: events are plain RunEvents; the router turns them into SSE. So this module is
testable without HTTP, and the CLI could use it too.

Known limit: the registry is IN MEMORY, so a run is only watchable from the process that started
it (fine for one API process; several pods need Postgres LISTEN/NOTIFY or Redis, Phase 10). The
run's STATE is in Postgres either way.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.approval import WorkOrderStore
from app.agent.budget import DEFAULT_BUDGET, RunBudget
from app.agent.events import step_event
from app.agent.graph import Graph, build_graph, resume, thread
from app.agent.state import AgentState, Approval
from app.agent.tools import NoTools, Tools
from app.db.models import RunStatus
from app.errors import NotFoundError
from app.gateway.structured import ChatModel

logger = logging.getLogger(__name__)

KEEP_FINISHED = 50  # finished runs whose event logs stay in memory for replay


class Busy(Exception):
    """Every run slot is taken: try again in a minute (HTTP 429)."""


class NotWaiting(Exception):
    """The run exists but isn't waiting for a decision: still running, or decided (HTTP 409)."""


class SentBack(Exception):
    """check_approval rejected the decision; the run stays paused and asks again (HTTP 422)."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class IncidentRef:
    machine: str  # machine code, "M-08"
    question: str


class AgentRepo(WorkOrderStore, Protocol):
    """What the service needs from the database: Postgres in the app (store.py), fakes in tests."""

    async def create_incident(self, machine: str, question: str) -> int: ...

    async def incident(self, incident_id: int) -> IncidentRef | None: ...

    async def run_started(self, thread_id: str, incident_id: int) -> None: ...

    async def run_finished(
        self, thread_id: str, status: RunStatus, state: AgentState | None, error: str | None
    ) -> None: ...


@dataclass(frozen=True)
class RunEvent:
    event: str  # "step", "report", "approval_required", "error", "notice", "done"
    data: dict[str, Any]


def events_after(count: int, last_event_id: str | None) -> int:
    """How many of a run's `count` events the client already HAS, from its Last-Event-ID
    header (the id of the last event it received; ids are 1, 2, 3, ...). The replay then
    continues after that many.

    Rules (tests/test_service.py::test_events_after_*). When in doubt, resend EVERYTHING: a
    duplicate step on the screen is harmless, a lost one is not.
    - no header (None or "")                       -> 0
    - a whole number n with 0 <= n <= count        -> n
    - anything else: not a number ("abc", "3.5"), negative, or n > count (an id from ANOTHER
      buffer: before a server restart, or another run), or "²" (isdigit, not decimal) -> 0
    """
    # isdecimal, not isdigit: isdigit() is True for "²", and int("²") raises (a 500 from a header
    # any client can send). isdecimal() is True exactly for what int() parses.
    if not last_event_id or not last_event_id.isdecimal():
        return 0

    n = int(last_event_id)
    return n if 0 <= n <= count else 0


@dataclass
class RunHandle:
    """One run's events as a LOG (7.4), not a queue: any number of readers, each can join late
    (replay from the start or from its Last-Event-ID) and then follows live. A queue would hand
    each event to ONE reader and forget it."""

    thread_id: str
    incident_id: int
    events: list[RunEvent] = field(default_factory=list)
    finished: bool = False
    task: asyncio.Task[None] | None = None
    # wakes EVERY waiting reader when an event arrives (a queue would wake one)
    _changed: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    async def emit(self, event: RunEvent) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    async def finish(self) -> None:
        async with self._changed:
            self.finished = True
            self._changed.notify_all()

    def _has_news(self, sent: int) -> bool:
        return len(self.events) > sent or self.finished

    async def follow(self, after: int = 0) -> AsyncIterator[tuple[int, RunEvent]]:
        """(id, event) for every event after the first `after`, then live until the run ends."""
        sent = after
        while True:
            async with self._changed:
                # partial() binds `sent` NOW: a lambda here would be a late-binding closure over a
                # loop variable (ruff B023), a classic bug even where wait_for calls it at once
                await self._changed.wait_for(partial(self._has_news, sent))
                new, done = self.events[sent:], self.finished
            for i, event in enumerate(new, start=sent + 1):
                yield i, event
            sent += len(new)
            if done and sent >= len(self.events):
                return


def report_data(state: AgentState) -> dict[str, Any]:
    """The `report` event's data (7.4b): the answer as text AND the hypothesis as structure, so
    the dashboard's evidence panel reads fields instead of parsing prose. ONE builder for the
    live run and for a run rebuilt from the checkpoint, so the two can't drift apart."""
    h = state.hypothesis
    return {
        "answer": state.answer,
        "machine": state.machine,
        "data_ends_at": state.data_ends_at.isoformat() if state.data_ends_at else None,
        "hypothesis": h.model_dump(mode="json") if h else None,
        "usage": usage(state),
    }


def usage(state: AgentState) -> dict[str, int]:
    return {
        "llm_calls": state.llm_requests,
        "provider_requests": state.llm_attempts,
        "tokens": state.llm_tokens,
    }


class AgentService:
    def __init__(
        self,
        repo: AgentRepo,
        gateway: ChatModel,
        checkpointer: BaseCheckpointSaver[Any],
        tools: Callable[[], AbstractAsyncContextManager[Tools]],
        *,
        budget: RunBudget = DEFAULT_BUDGET,
        max_concurrent: int = 2,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo, self._gateway, self._checkpointer = repo, gateway, checkpointer
        self._tools, self._budget, self._clock = tools, budget, clock
        self._max, self._active = max_concurrent, 0
        self.runs: dict[str, RunHandle] = {}  # live and recently finished runs (their logs)
        # One lock per run (6.2): two decisions at the same moment would BOTH see "paused" and
        # both resume. With the lock the second waits, then sees "not waiting" (409).
        self._decision_locks: dict[str, asyncio.Lock] = {}

    def _graph(self, tools: Tools) -> Graph:
        return build_graph(
            tools,
            self._gateway,
            self._clock,
            store=self._repo,
            checkpointer=self._checkpointer,
            budget=self._budget,
        )

    async def create_incident(self, machine: str, question: str) -> int:
        """Raises NotFoundError for an unknown machine."""
        return await self._repo.create_incident(machine, question)

    def is_running(self, thread_id: str) -> bool:
        handle = self.runs.get(thread_id)
        return handle is not None and handle.task is not None and not handle.task.done()

    async def start(self, incident_id: int) -> RunHandle:
        """Check, then start. Raises Busy or NotFoundError BEFORE anything runs, so the router
        can still answer with an HTTP error (once an SSE stream has started, it can't)."""
        if self._active >= self._max:
            raise Busy(f"{self._active} investigation(s) running (max {self._max}); try later")
        incident = await self._repo.incident(incident_id)
        if incident is None:
            raise NotFoundError(f"incident {incident_id} does not exist")
        handle = RunHandle(str(uuid.uuid4()), incident_id)
        await self._repo.run_started(handle.thread_id, incident_id)
        self._active += 1  # no await between the check and here: no other task can sneak in
        self.runs[handle.thread_id] = handle
        handle.task = asyncio.create_task(self._run(handle, incident))
        return handle

    async def _run(self, handle: RunHandle, incident: IncidentRef) -> None:
        emit = handle.emit
        status, state, error = RunStatus.FAILED, None, None
        try:
            # McpTools opened and closed in THIS task (anyio cancel scopes, 4.1a).
            async with self._tools() as tools:
                graph = self._graph(tools)
                start = AgentState(
                    question=incident.question,
                    machine=incident.machine,
                    incident_id=handle.incident_id,
                )
                config = thread(handle.thread_id)
                async for update in graph.astream(start, config, stream_mode="updates"):
                    for node, node_update in update.items():
                        payload = None if node.startswith("__") else step_event(node, node_update)
                        if payload is not None:
                            await emit(RunEvent("step", {"node": node, **payload}))
                snapshot = await graph.aget_state(config)
            state = AgentState.model_validate(snapshot.values)
            await emit(RunEvent("report", report_data(state)))
            if snapshot.interrupts:
                await emit(RunEvent("approval_required", snapshot.interrupts[0].value))
                status = RunStatus.PAUSED
            else:
                status = RunStatus.DONE
        except Exception as e:
            # Not swallowed: logged with the traceback, recorded on the run, shown to the reader.
            logger.exception("agent run %s failed", handle.thread_id)
            error = f"{type(e).__name__}: {e}"
            await emit(RunEvent("error", {"error": error}))
        finally:
            self._active -= 1
            try:
                await self._repo.run_finished(handle.thread_id, status, state, error)
            except Exception:
                logger.exception("could not record the end of run %s", handle.thread_id)
            await emit(RunEvent("done", {"thread_id": handle.thread_id, "status": status.value}))
            await handle.finish()
            self._evict()

    def _evict(self) -> None:
        """Keep the logs of at most KEEP_FINISHED finished runs (oldest dropped first): every
        log lives in RAM. A dropped run is still readable from the checkpointer (events())."""
        finished = [t for t, h in self.runs.items() if h.finished]  # dicts keep insertion order
        for thread_id in finished[: max(0, len(finished) - KEEP_FINISHED)]:
            del self.runs[thread_id]

    async def events(
        self, thread_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[tuple[int, RunEvent]]:
        """The run's events for one reader: replay after Last-Event-ID, then live. A run not in
        this process (finished long ago, another process, before a restart) has no step log
        any more: its RESULT is rebuilt from the checkpointer, after a notice saying so. Raises
        NotFoundError for an unknown thread (call it BEFORE streaming: that's still a 404)."""
        handle = self.runs.get(thread_id)
        if handle is None:
            found = await self.state(thread_id)
            if found is None:
                raise NotFoundError(f"run {thread_id!r} does not exist")
            handle = self._rebuilt(thread_id, *found)
        return handle.follow(events_after(len(handle.events), last_event_id))

    def _rebuilt(
        self, thread_id: str, state: AgentState, pending: dict[str, Any] | None
    ) -> RunHandle:
        handle = RunHandle(thread_id, state.incident_id or 0, finished=True)
        status = "paused" if pending else ("done" if state.answer else "failed")
        handle.events = [
            RunEvent(
                "notice",
                {
                    "message": "This run's live steps are no longer available; "
                    "its result comes from the saved state."
                },
            ),
            RunEvent("report", report_data(state)),
            *([RunEvent("approval_required", pending)] if pending else []),
            RunEvent("done", {"thread_id": thread_id, "status": status}),
        ]
        return handle

    async def decide(self, thread_id: str, approval: Approval) -> AgentState:
        """Hand a human's decision to a paused run. Needs no tools and no LLM: only
        await_approval and save_work_order run (milliseconds). Raises NotFoundError (no such
        run), NotWaiting (409) or SentBack (422: the run stays paused, with the reasons)."""
        lock = self._decision_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            graph = self._graph(NoTools())
            snapshot = await graph.aget_state(thread(thread_id))
            if not snapshot.values:
                raise NotFoundError(f"run {thread_id!r} does not exist")
            if not snapshot.interrupts:
                what = "still running" if self.is_running(thread_id) else "already decided"
                raise NotWaiting(f"run {thread_id!r} is not waiting for a decision ({what})")
            state = await resume(graph, approval, thread_id)
            if state.work_order_id is None:  # check_approval sent it back: still paused
                raise SentBack(state.approval_errors)
            await self._repo.run_finished(thread_id, RunStatus.DONE, state, None)
            return state

    async def state(self, thread_id: str) -> tuple[AgentState, dict[str, Any] | None] | None:
        """(state, pending approval or None) of any run in the checkpointer, also one started by
        another process or before a restart; None if the thread is unknown. No tools needed."""
        snapshot = await self._graph(NoTools()).aget_state(thread(thread_id))
        if not snapshot.values:
            return None
        pending = snapshot.interrupts[0].value if snapshot.interrupts else None
        return AgentState.model_validate(snapshot.values), pending
