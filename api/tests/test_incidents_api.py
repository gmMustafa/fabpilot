"""The incidents API (6.1b) over HTTP, in process: ASGITransport + an AgentService of fakes.

No server, no DB, no MCP, no LLM: ASGITransport doesn't run the lifespan; get_agent is
overridden. The SSE stream is parsed from the raw lines, as a client receives it.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.agent.service import AgentService
from app.agent.tools import Tools
from app.db.deps import get_agent
from app.main import app
from app.testing import ScriptedChat
from tests.test_service import FakeRepo, q1_chat, service
from tests.test_work_order import tools_with_sop


@asynccontextmanager
async def client_for(agent: AgentService) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_agent] = lambda: agent
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def parse_sse(lines: list[str]) -> list[dict[str, Any]]:
    """Raw SSE lines -> [{"event", "id", "data"}]; ': ping' comments are skipped."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in lines:
        if not line:
            if current:
                events.append(current)
            current = {}
        elif line.startswith(":"):
            continue
        else:
            key, _, value = line.partition(": ")
            current[key] = json.loads(value) if key == "data" else value
    return events + ([current] if current else [])


async def stream(c: AsyncClient, incident_id: int) -> tuple[int, dict[str, str], list[Any]]:
    async with c.stream("POST", f"/incidents/{incident_id}/investigate") as r:
        lines = [line async for line in r.aiter_lines()]
        return r.status_code, dict(r.headers), parse_sse(lines)


# ------------------------------------------------------------------ incidents


async def test_create_incident_is_201_with_location() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        r = await c.post("/incidents", json={"machine": "M-08", "question": "Overheating?"})
    assert r.status_code == 201 and r.headers["location"] == "/incidents/8"
    assert r.json() == {"id": 8, "machine": "M-08", "question": "Overheating?"}


async def test_unknown_machine_is_404_and_a_bad_code_is_422() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        assert (
            await c.post("/incidents", json={"machine": "M-99", "question": "x?x"})
        ).status_code == 404
        assert (
            await c.post("/incidents", json={"machine": "pump", "question": "x?x"})
        ).status_code == 422


# ------------------------------------------------------------------ the SSE stream


async def test_investigate_streams_steps_report_approval_done_with_ids() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        status, headers, events = await stream(c, 7)
    assert status == 200 and headers["content-type"].startswith("text/event-stream")
    names = [e["event"] for e in events]
    assert names == ["step"] * 5 + ["report", "approval_required", "done"]
    assert [e["id"] for e in events] == [str(n) for n in range(1, len(events) + 1)]
    assert events[0]["data"] == {"node": "triage", "machine": "M-08", "intent": "diagnose"}
    assert events[-1]["data"]["status"] == "paused"


async def test_unknown_incident_is_404_before_any_stream() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        status, headers, _ = await stream(c, 99)
    assert status == 404 and headers["content-type"] == "application/json"


async def test_busy_is_429_with_retry_after_before_any_stream() -> None:
    gate = asyncio.Event()

    @asynccontextmanager
    async def slow_tools() -> AsyncIterator[Tools]:
        await gate.wait()
        yield tools_with_sop()

    agent = service(ScriptedChat(), FakeRepo(), tools=slow_tools)
    first, second = await agent.start(7), await agent.start(7)  # both slots taken
    async with client_for(agent) as c:
        r = await c.post("/incidents/7/investigate")
    assert r.status_code == 429 and r.headers["retry-after"] == "30"
    assert "2 investigation(s) running" in r.json()["detail"]
    gate.set()
    for handle in (first, second):
        assert handle.task is not None
        await handle.task


async def test_a_client_that_leaves_early_does_not_stop_the_run() -> None:
    agent = service(q1_chat(), FakeRepo())
    async with client_for(agent) as c:
        async with c.stream("POST", "/incidents/7/investigate") as r:
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    break  # the browser tab is closed after the first event
        (handle,) = agent.runs.values()
        assert handle.task is not None
        await handle.task
        run = await c.get(f"/runs/{handle.thread_id}")  # catch up afterwards
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "paused" and body["answer"].startswith("Most likely a FAULTY SENSOR")
    assert body["pending_approval"]["work_order"]["sop_ref"] == "SOP-GEN-004"


async def test_unknown_run_is_404() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        assert (await c.get("/runs/nope")).status_code == 404


async def test_the_stream_documents_its_errors_in_openapi() -> None:
    async with client_for(service(q1_chat(), FakeRepo())) as c:
        spec = (await c.get("/openapi.json")).json()
    responses = spec["paths"]["/incidents/{incident_id}/investigate"]["post"]["responses"]
    assert {"404", "429"} <= set(responses)
