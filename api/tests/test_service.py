"""AgentService (6.1): runs as tasks, events per step, slots, failures. No HTTP, no MCP, no LLM."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import checkpoint_serde
from app.agent.service import (
    KEEP_FINISHED,
    AgentService,
    Busy,
    IncidentRef,
    RunEvent,
    RunHandle,
    events_after,
)
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

    async def create_incident(self, machine: str, question: str) -> int:
        if machine != "M-08":
            raise NotFoundError(f"machine {machine!r} does not exist")
        incident_id = max(self.incidents) + 1
        self.incidents[incident_id] = IncidentRef(machine, question)
        return incident_id

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
    """Everything one reader gets, from the start until the run is done (a log, not a queue)."""
    return [event async for _, event in handle.follow()]


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


# ------------------------------------------------------------------ events_after (your TODO, 7.4)


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, 0), ("", 0), ("0", 0), ("3", 3), ("8", 8)],
)
def test_events_after_a_valid_last_event_id(header: str | None, expected: int) -> None:
    assert events_after(8, header) == expected


@pytest.mark.parametrize("header", ["abc", "3.5", "-1", " 3", "9", "1000", "²"])
def test_events_after_resends_everything_when_in_doubt(header: str) -> None:
    """Not a number, negative, or beyond this log (an id from ANOTHER buffer: before a server
    restart, or another run): resend all. A duplicate step is harmless, a lost one is not."""
    assert events_after(8, header) == 0


# ------------------------------------------------------------------ the event log (7.4)


async def test_two_readers_both_get_every_event() -> None:
    """A queue would hand each event to ONE of them: the log + Condition wakes both."""
    handle = await service(q1_chat(), FakeRepo()).start(7)
    first, second = await asyncio.gather(drain(handle), drain(handle))
    assert [e.event for e in first] == [e.event for e in second]
    assert first[-1].event == "done"


async def test_a_late_reader_gets_everything_from_the_start() -> None:
    """A reload, or a second tab opened after the run finished."""
    handle = await service(q1_chat(), FakeRepo()).start(7)
    assert handle.task is not None
    await handle.task
    late = [event_id async for event_id, _ in handle.follow()]
    assert late == list(range(1, len(handle.events) + 1))


async def test_a_reconnect_with_last_event_id_gets_only_what_is_newer() -> None:
    svc = service(q1_chat(), FakeRepo())
    handle = await svc.start(7)
    assert handle.task is not None
    await handle.task
    resumed = [i async for i, _ in await svc.events(handle.thread_id, last_event_id="5")]
    assert resumed == list(range(6, len(handle.events) + 1))


async def test_after_a_restart_the_result_is_rebuilt_from_the_checkpoint() -> None:
    """A NEW service (another process / after a restart) sharing the checkpointer: no step log,
    but a notice and the result instead of a 404."""
    saver = InMemorySaver(serde=checkpoint_serde())
    first = AgentService(FakeRepo(), q1_chat(), saver, fake_tools(), clock=lambda: NOW)
    handle = await first.start(7)
    assert handle.task is not None
    await handle.task

    restarted = AgentService(FakeRepo(), ScriptedChat(), saver, fake_tools(), clock=lambda: NOW)
    events = [e async for _, e in await restarted.events(handle.thread_id)]
    assert [e.event for e in events] == ["notice", "report", "approval_required", "done"]
    assert events[-1].data["status"] == "paused"
    assert events[1].data["answer"].startswith("Most likely a FAULTY SENSOR")


async def test_an_unknown_run_has_no_events() -> None:
    with pytest.raises(NotFoundError, match="nope"):
        await service(q1_chat(), FakeRepo()).events("nope")


async def test_only_the_newest_finished_logs_are_kept_in_memory() -> None:
    svc = service(ScriptedChat(*([hypothesis_json()] * (KEEP_FINISHED + 3))), FakeRepo())
    threads = []
    for _ in range(KEEP_FINISHED + 3):
        handle = await svc.start(7)
        assert handle.task is not None
        await handle.task
        threads.append(handle.thread_id)
    assert len(svc.runs) == KEEP_FINISHED
    assert threads[0] not in svc.runs and threads[-1] in svc.runs
