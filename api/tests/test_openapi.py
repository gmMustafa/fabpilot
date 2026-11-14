"""The OpenAPI spec as a contract (6.3): drift (your TODO), the committed file, and what it says."""

import json
import re
from typing import Any

import pytest
from pydantic import TypeAdapter

from app.main import app
from app.openapi import REGENERATE, SPEC_FILE, drift, render
from app.schemas import EVENT_DATA
from tests.test_service import FakeRepo, drain, q1_chat, service


def spec(
    paths: dict[str, Any] | None = None, schemas: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": paths or {},
        "components": {"schemas": schemas or {}},
    }


GET_X = {"/x": {"get": {"operationId": "getX"}}}


# ------------------------------------------------------------------ drift (your TODO)


def test_drift_identical_specs_have_none() -> None:
    assert drift(spec(GET_X, {"A": {}}), spec(GET_X, {"A": {}})) == []


def test_drift_operations_added_removed_changed() -> None:
    old = spec({"/x": {"get": {"operationId": "a"}}, "/gone": {"post": {}}})
    new = spec({"/x": {"get": {"operationId": "b"}}, "/new": {"put": {}}})
    assert drift(old, new) == [
        "operation added: PUT /new",
        "operation changed: GET /x",
        "operation removed: POST /gone",
    ]


def test_drift_a_second_method_on_the_same_path_is_its_own_operation() -> None:
    old = spec(GET_X)
    new = spec({"/x": {"get": {"operationId": "getX"}, "post": {}}})
    assert drift(old, new) == ["operation added: POST /x"]


def test_drift_schemas_added_removed_changed() -> None:
    old = spec(schemas={"Keep": {"type": "string"}, "Old": {}})
    new = spec(schemas={"Keep": {"type": "integer"}, "New": {}})
    assert drift(old, new) == ["schema added: New", "schema changed: Keep", "schema removed: Old"]


def test_drift_other_top_level_keys() -> None:
    new = spec()
    new["info"] = {"title": "t", "version": "2"}
    assert drift(spec(), new) == ["changed: info"]


def test_drift_missing_sections_count_as_empty() -> None:
    assert drift({"openapi": "3.1.0"}, {"openapi": "3.1.0", "paths": {}}) == []
    assert drift({}, {"paths": GET_X}) == ["operation added: GET /x"]


# ------------------------------------------------------------------ the committed contract


def test_the_committed_spec_matches_the_app() -> None:
    """THE contract test. Red means: the API changed but api/openapi.json didn't. Regenerate it
    and commit it WITH the change, so the reviewer sees the API change in the diff."""
    committed = json.loads(SPEC_FILE.read_text(encoding="utf-8"))
    current = json.loads(render(app.openapi()))  # through render: the same JSON round trip
    differences = drift(committed, current)
    assert not differences, (
        "api/openapi.json is out of date:\n  "
        + "\n  ".join(differences)
        + f"\nregenerate it with:  {REGENERATE}"
    )


def operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, ops in app.openapi()["paths"].items()
        for method, op in ops.items()
    ]


def test_operation_ids_are_unique_and_readable() -> None:
    """They become method names in the generated client: getRun, not get_run_runs__..._get."""
    ids = [op["operationId"] for _, _, op in operations()]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][A-Za-z]*", i) for i in ids), ids


@pytest.mark.parametrize(("method", "path", "op"), operations())
def test_every_documented_error_has_a_schema(method: str, path: str, op: dict[str, Any]) -> None:
    """A description alone ("Not found") tells the frontend nothing about the body's shape."""
    for code, response in op["responses"].items():
        if code.startswith("4"):
            assert response["content"]["application/json"]["schema"], (method, path, code)


@pytest.mark.parametrize(
    "path",
    ["/machines/{code}", "/runs/{thread_id}", "/work-orders/{work_order_id}"],
)
def test_reads_by_id_document_their_404(path: str) -> None:
    """These raised 404 before 6.3 but didn't SAY so in the spec."""
    assert "404" in app.openapi()["paths"][path]["get"]["responses"]


# ------------------------------------------------------------------ the SSE events


def test_every_event_a_real_run_emits_fits_its_published_model() -> None:
    """The stream's contract: step_event and the service produce exactly what the spec says."""
    import asyncio

    async def run() -> list[Any]:
        handle = await service(q1_chat(), FakeRepo()).start(7)
        return await drain(handle)

    events = asyncio.run(run())
    assert {e.event for e in events} >= {"step", "report", "approval_required", "done"}
    for event in events:
        TypeAdapter(EVENT_DATA[event.event]).validate_python(event.data)


def test_the_stream_operation_points_at_its_event_schemas() -> None:
    s = app.openapi()
    events = s["paths"]["/incidents/{incident_id}/investigate"]["post"]["x-sse-events"]
    for ref in events.values():
        assert ref["$ref"].split("/")[-1] in s["components"]["schemas"]
