"""The API's OpenAPI spec as a CONTRACT (6.3): exported, committed, checked, typed.

    uv run python -m app.export_openapi   # writes api/openapi.json (commit it with the change)

The frontend's TypeScript types are generated from the committed file (7.1):
    pnpm dlx openapi-typescript api/openapi.json -o web/src/lib/api/schema.d.ts
so renaming a field in Python becomes a COMPILE error in the dashboard, not an `undefined`.
tests/test_openapi.py fails when the app and the committed file disagree, and the diff of
openapi.json in a pull request SHOWS the API change to the reviewer.

OpenAPI (with a P) is a spec format for REST APIs; it has nothing to do with OpenAI.
"""

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

SPEC_FILE = Path(__file__).resolve().parents[1] / "openapi.json"
REGENERATE = "cd api && uv run python -m app.export_openapi"


def operation_id(route: APIRoute) -> str:
    """The route's function name in camelCase: list_work_orders -> listWorkOrders. FastAPI's
    default (list_work_orders_work_orders_get) becomes the method name in generated clients."""
    first, *rest = route.name.split("_")
    return first + "".join(word.capitalize() for word in rest)


def render(spec: dict[str, Any]) -> str:
    """Deterministic text: sorted keys and a fixed layout, so a diff shows only REAL changes."""
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export(app: FastAPI, path: Path = SPEC_FILE) -> Path:
    # newline="\n": the same bytes on Windows and in Linux CI (text mode would write \r\n)
    path.write_text(render(app.openapi()), encoding="utf-8", newline="\n")
    return path


def event_schema_name(event: str) -> str:
    """ "approval_required" -> "ApprovalRequiredEventData" """
    return "".join(part.capitalize() for part in event.split("_")) + "EventData"


def event_components() -> dict[str, Any]:
    """Schema components for the SSE events' `data` (app.schemas.EVENT_DATA): "step" ->
    StepEventData (a union discriminated by `node`), "report" -> ReportEventData, ..."""
    from pydantic import TypeAdapter

    from app.schemas import EVENT_DATA

    components: dict[str, Any] = {}
    for event, model in EVENT_DATA.items():
        name = event_schema_name(event)
        schema = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
        components.update(schema.pop("$defs", {}))
        components[name] = schema
    return components


def _errors_are_json(spec: dict[str, Any]) -> None:
    """Every 4xx body in this API is JSON (FastAPI's exception handlers write it). FastAPI
    documents a route's extra responses with the ROUTE's media type, so the SSE route's 404/429
    claimed "text/event-stream": a generated client would read an error as a stream. Found by
    test_every_documented_error_has_a_schema (6.3)."""
    for ops in spec["paths"].values():
        for op in ops.values():
            for code, response in op["responses"].items():
                content = response.get("content", {})
                if code.startswith("4") and content and "application/json" not in content:
                    response["content"] = {"application/json": next(iter(content.values()))}


def install(app: FastAPI) -> None:
    """Publish the event payloads in the spec: FastAPI only includes models that a request or
    a JSON response uses, and an SSE stream's `data` is neither. The investigate operation
    gets an `x-sse-events` map (event name -> schema): a vendor extension tools ignore, but a
    reader (and our test) can follow it."""
    from app.schemas import EVENT_DATA

    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        spec = original()  # sets app.openapi_schema; we extend that same dict, once
        _errors_are_json(spec)
        events = event_components()
        spec["components"]["schemas"].update(events)
        investigate = spec["paths"]["/incidents/{incident_id}/investigate"]["post"]
        investigate["x-sse-events"] = {
            event: {"$ref": f"#/components/schemas/{event_schema_name(event)}"}
            for event in EVENT_DATA
        }
        return spec

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's documented extension point


def drift(committed: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """What differs between the committed spec and the app's, one line each, SORTED; [] if
    nothing does. The contract test prints these, so a red test says WHAT changed.

    - operations ("METHOD /path", from both specs' "paths"):
        f"operation added: POST /x"  /  "operation removed: ..."  /  "operation changed: ..."
    - schema components (both specs' "components"["schemas"]):
        f"schema added: RunOut"      /  "schema removed: ..."     /  "schema changed: ..."
    - any OTHER top-level key that differs ("info", "openapi", ...):
        f"changed: info"
    A missing "paths" / "components" / "schemas" counts as empty.
    """

    def operations(spec: dict[str, Any]) -> dict[str, Any]:
        return {
            f"{method.upper()} {path}": operation
            for path, methods in spec.get("paths", {}).items()
            for method, operation in methods.items()
            if method.lower()
            in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "head",
                "options",
                "trace",
            }
        }

    def compare(old: dict[str, Any], new: dict[str, Any], kind: str) -> list[str]:
        changes = []

        for name in old.keys() | new.keys():
            if name not in old:
                changes.append(f"{kind} added: {name}")
            elif name not in new:
                changes.append(f"{kind} removed: {name}")
            elif old[name] != new[name]:
                changes.append(f"{kind} changed: {name}")

        return changes

    changes = compare(operations(committed), operations(current), "operation")

    old_schemas = committed.get("components", {}).get("schemas", {})
    new_schemas = current.get("components", {}).get("schemas", {})

    changes += compare(old_schemas, new_schemas, "schema")

    for key in (committed.keys() | current.keys()) - {"paths", "components"}:
        if committed.get(key) != current.get(key):
            changes.append(f"changed: {key}")

    return sorted(changes)
