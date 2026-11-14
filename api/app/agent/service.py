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
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.approval import WorkOrderStore
from app.agent.budget import DEFAULT_BUDGET, RunBudget
from app.agent.events import step_event
from app.agent.graph import Graph, build_graph, thread
from app.agent.state import AgentState
from app.agent.tools import NoTools, Tools
from app.db.models import RunStatus
from app.errors import NotFoundError
from app.gateway.structured import ChatModel

logger = logging.getLogger(__name__)


class Busy(Exception):
    """Every run slot is taken: try again in a minute (HTTP 429)."""


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
    event: str  # "step", "report", "approval_required", "error", "done"
    data: dict[str, Any]


@dataclass
class RunHandle:
    thread_id: str
    incident_id: int
    events: asyncio.Queue[RunEvent | None] = field(default_factory=asyncio.Queue)  # None = end
    task: asyncio.Task[None] | None = None


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
        self.runs: dict[str, RunHandle] = {}

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
        emit = handle.events.put_nowait
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
                            emit(RunEvent("step", {"node": node, **payload}))
                snapshot = await graph.aget_state(config)
            state = AgentState.model_validate(snapshot.values)
            emit(RunEvent("report", {"answer": state.answer, "usage": usage(state)}))
            if snapshot.interrupts:
                emit(RunEvent("approval_required", snapshot.interrupts[0].value))
                status = RunStatus.PAUSED
            else:
                status = RunStatus.DONE
        except Exception as e:
            # Not swallowed: logged with the traceback, recorded on the run, shown to the reader.
            logger.exception("agent run %s failed", handle.thread_id)
            error = f"{type(e).__name__}: {e}"
            emit(RunEvent("error", {"error": error}))
        finally:
            self._active -= 1
            try:
                await self._repo.run_finished(handle.thread_id, status, state, error)
            except Exception:
                logger.exception("could not record the end of run %s", handle.thread_id)
            emit(RunEvent("done", {"thread_id": handle.thread_id, "status": status.value}))
            emit(None)

    async def state(self, thread_id: str) -> tuple[AgentState, dict[str, Any] | None] | None:
        """(state, pending approval or None) of any run in the checkpointer, also one started by
        another process or before a restart; None if the thread is unknown. No tools needed."""
        snapshot = await self._graph(NoTools()).aget_state(thread(thread_id))
        if not snapshot.values:
            return None
        pending = snapshot.interrupts[0].value if snapshot.interrupts else None
        return AgentState.model_validate(snapshot.values), pending
