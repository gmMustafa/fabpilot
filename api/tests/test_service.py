"""AgentService (6.1): runs as tasks, events per step, slots, failures. No HTTP, no MCP, no LLM."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import checkpoint_serde
from app.agent.service import AgentService, Busy, IncidentRef, RunEvent, RunHandle
from app.agent.state import AgentState
from app.agent.tools import Tools
from app.db.models import RunStatus
from app.errors import NotFoundError
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, hypothesis_json
from tests.test_approval import FakeStore
from tests.test_work_order import draft, tools_with_sop

# The question names NO machine: the incident does (and triage must not ask the LLM for it).
QUESTION = "Discharge temperature alarm. Is the compressor overheating?"


class FakeRepo(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.incidents = {7: IncidentRef(machine="M-08", question=QUESTION)}
        self.finished: dict[str, tuple[RunStatus, AgentState | None, str | None]] = {}
        self.started: list[str] = []

    async def incident(self, incident_id: int) -> IncidentRef | None:
        return self.incidents.get(incident_id)

    async def run_started(self, thread_id: str, incident_id: int) -> None:
        self.started.append(thread_id)

    async def run_finished(
        self, thread_id: str, status: RunStatus, state: AgentState | None, error: str | None
    ) -> None:
        self.finished[thread_id] = (status, state, error)


def fake_tools(tools: Tools | None = None) -> Callable[[], AbstractAsyncContextManager[Tools]]:
    @asynccontextmanager
    async def open_tools() -> AsyncIterator[Tools]:
        yield tools or tools_with_sop()

    return open_tools


def service(chat: ScriptedChat, repo: FakeRepo, **kw: Any) -> AgentService:
    saver = InMemorySaver(serde=checkpoint_serde())
    return AgentService(repo, chat, saver, kw.pop("tools", fake_tools()), clock=lambda: NOW, **kw)


def q1_chat() -> ScriptedChat:
    return ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())


async def drain(handle: RunHandle) -> list[RunEvent]:
    events = []
    while (event := await handle.events.get()) is not None:
        events.append(event)
    return events


async def test_a_run_streams_its_steps_then_report_approval_and_done() -> None:
    repo = FakeRepo()
    handle = await service(q1_chat(), repo).start(7)
    events = await drain(handle)

    steps = [e.data["node"] for e in events if e.event == "step"]
    assert steps == ["triage", "investigate", "hypothesize", "guard", "draft_work_order"]
    assert [e.event for e in events if e.event != "step"] == ["report", "approval_required", "done"]
    report = next(e for e in events if e.event == "report")
    assert report.data["answer"].startswith("Most likely a FAULTY SENSOR")
    assert report.data["usage"] == {"llm_calls": 2, "provider_requests": 2, "tokens": 0}
    approval = next(e for e in events if e.event == "approval_required")
    assert approval.data["work_order"]["sop_ref"] == "SOP-GEN-004"

    status, state, error = repo.finished[handle.thread_id]
    assert status is RunStatus.PAUSED and error is None
    assert state is not None and state.incident_id == 7 and state.machine == "M-08"


async def test_the_machine_comes_from_the_incident_not_an_llm_guess() -> None:
    """QUESTION names no machine; ScriptedChat has no triage answer, so a guess would fail."""
    handle = await service(q1_chat(), FakeRepo()).start(7)
    triage = next(e for e in await drain(handle) if e.data.get("node") == "triage")
    assert triage.data["machine"] == "M-08"  # from the incident; no triage request was made


async def test_nobody_reading_the_stream_does_not_stop_the_run() -> None:
    """The stream is a view: a closed browser tab must not leave a half-done run."""
    repo = FakeRepo()
    handle = await service(q1_chat(), repo).start(7)
    assert handle.task is not None
    await handle.task  # nobody ever read handle.events
    assert repo.finished[handle.thread_id][0] is RunStatus.PAUSED


async def test_the_result_is_readable_later_from_the_checkpointer() -> None:
    svc = service(q1_chat(), FakeRepo())
    handle = await svc.start(7)
    await drain(handle)
    found = await svc.state(handle.thread_id)
    assert found is not None
    state, pending = found
    assert state.answer is not None and pending is not None
    assert await svc.state("no-such-thread") is None


async def test_a_third_concurrent_run_is_refused_and_a_slot_frees_up() -> None:
    gate = asyncio.Event()

    @asynccontextmanager
    async def slow_tools() -> AsyncIterator[Tools]:
        await gate.wait()  # keeps both runs "running"
        yield tools_with_sop()

    svc = service(ScriptedChat(*([hypothesis_json()] * 2)), FakeRepo(), tools=slow_tools)
    first, second = await svc.start(7), await svc.start(7)
    with pytest.raises(Busy, match="2 investigation"):
        await svc.start(7)
    gate.set()
    await asyncio.gather(drain(first), drain(second))
    assert svc._active == 0  # both slots are free again


async def test_an_unknown_incident_uses_no_slot() -> None:
    svc = service(q1_chat(), FakeRepo())
    with pytest.raises(NotFoundError, match="incident 99"):
        await svc.start(99)
    assert svc._active == 0


async def test_a_crash_is_an_error_event_a_failed_run_and_a_free_slot() -> None:
    @asynccontextmanager
    async def broken_tools() -> AsyncIterator[Tools]:
        raise RuntimeError("docs-mcp did not start")
        yield tools_with_sop()  # pragma: no cover

    repo = FakeRepo()
    svc = service(q1_chat(), repo, tools=broken_tools)
    handle = await svc.start(7)
    events = await drain(handle)
    assert [e.event for e in events] == ["error", "done"]
    assert events[0].data["error"] == "RuntimeError: docs-mcp did not start"
    assert repo.finished[handle.thread_id][0] is RunStatus.FAILED and svc._active == 0
