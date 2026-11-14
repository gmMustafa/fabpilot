"""Agent nodes (5.2a: triage, investigate, and the routing after it). See docs/agent-design.md §4.

A node is `async (state, deps...) -> dict`: it reads the state and returns ONLY the fields it
owns. Dependencies (tools, gateway, clock) are passed in when the graph is built (5.2b), so the
nodes stay plain functions that tests call directly.
"""

import asyncio
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.state import AgentState, Evidence, Intent
from app.agent.tools import ToolCallError, Tools
from app.gateway.structured import ChatModel, StructuredOutputError, chat_structured
from app.gateway.types import ChatMessage, LLMError

INVESTIGATION_WINDOW = timedelta(days=3)
ALARM_LIMIT = 200  # the tool's maximum: a truncated log would hide the onset (F12)
DOC_HITS = 6
MAX_FOLLOW_UPS = 2

_MACHINE = re.compile(r"\bM-\d{2}\b")
# Output budgets. Reasoning models spend most tokens "thinking" before they answer: 1500 was
# not enough for the hypothesis in the first live run (5.2b, OutputTruncatedError).
TRIAGE_TOKENS = 800

# Deterministic intent rules, checked in this order. A wrong intent only changes which documents
# are searched first; the hypothesis still sees all the evidence.
_INTENT_RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.PROCEDURE, re.compile(r"\b(procedure|how (do|to)|replace|steps?|change the)\b", re.I)),
    (Intent.HISTORY, re.compile(r"\b(before|history|previous|past|how many|ever|again)\b", re.I)),
    (Intent.STATUS, re.compile(r"\b(ok|okay|healthy|status|fine)\b", re.I)),
]


def rule_intent(question: str) -> Intent:
    for intent, pattern in _INTENT_RULES:
        if pattern.search(question):
            return intent
    return Intent.DIAGNOSE


class TriageOut(BaseModel):
    machine: str | None = Field(description="Machine code like 'M-07', or null if none is meant")
    intent: Intent


# ------------------------------------------------------------------ triage


async def triage(state: AgentState, tools: Tools, gateway: ChatModel) -> dict[str, Any]:
    """Which machine, which kind of question. Regex and keyword rules first: "M-08" is a pattern,
    not a judgement, and every request we don't make is one of ~50 a day saved. The LLM is asked
    only when the question names no machine code ("the central coolant pump")."""
    found = _MACHINE.search(state.question)
    if found:
        return {"machine": found.group(0), "intent": rule_intent(state.question)}

    machines = (await tools.call("sensors", "list_machines", {})).get("result", [])
    plant = "\n".join(f"{m['code']}: {m['name']} ({m['type']}, {m['line']})" for m in machines)
    try:
        out = await chat_structured(
            gateway,
            [
                ChatMessage(
                    role="system",
                    content="You map a maintenance question to ONE machine of this plant, or none.",
                ),
                ChatMessage(
                    role="user", content=f"Machines:\n{plant}\n\nQuestion: {state.question}"
                ),
            ],
            TriageOut,
            purpose="agent.triage",
            max_tokens=TRIAGE_TOKENS,
        )
    except (LLMError, StructuredOutputError) as e:
        return {"machine": None, "llm_error": f"triage: {e}"}
    known = {m["code"] for m in machines}
    machine = (
        out.value.machine if out.value.machine in known else None
    )  # never trust a made-up code
    return {
        "machine": machine,
        "intent": out.value.intent,
        "llm_requests": state.llm_requests + out.calls,
    }


# ------------------------------------------------------------------ investigate


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def doc_query(question: str, evidence: Evidence) -> str:
    """Search terms from what the DATA says, not only from the question: alarm codes and the
    sensors with findings are exact terms full-text search finds (3.3)."""
    terms: list[str] = []
    for alarm in (evidence.alarm_log or {}).get("alarms", []):
        if alarm["code"] not in terms:
            terms.append(alarm["code"])
        if len(terms) >= 3:
            break
    findings = (evidence.anomalies or {}).get("findings", [])
    for f in findings[:3]:
        terms.append(f"{f['sensor']} {f['kind'].replace('_', ' ')}")
    if findings and all(f.get("isolated") for f in findings):
        terms.append("only one sensor changed, other sensors normal")
    return " ".join([question, *terms])


async def call_as_evidence(
    tools: Tools,
    errors: list[str],
    server: Literal["sensors", "docs"],
    tool: str,
    args: Mapping[str, Any],
) -> dict[str, Any] | None:
    """A tool call whose failure is EVIDENCE (e.g. "no readings in this window"), not a crash."""
    try:
        return await tools.call(server, tool, args)
    except ToolCallError as e:
        errors.append(str(e))
        return None


async def investigate(
    state: AgentState, tools: Tools, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> dict[str, Any]:
    """The fixed investigation: the same calls every time, made by code (agent-design.md §1).

    The window ends at the machine's newest reading, not at "now" (F2: default windows past the
    data end were the biggest Phase 4 failure)."""
    if state.machine is None:
        return {}
    errors: list[str] = []
    detail = await call_as_evidence(
        tools, errors, "sensors", "get_machine", {"machine": state.machine}
    )
    if detail is None:
        return {"evidence": Evidence(errors=errors)}

    data_ends_at = _ts(detail.get("last_reading_at"))
    end = min(clock(), data_ends_at + timedelta(minutes=1)) if data_ends_at else clock()
    start = end - INVESTIGATION_WINDOW
    window = {"machine": state.machine, "start": start.isoformat(), "end": end.isoformat()}
    alarm_log, anomalies, incidents = await asyncio.gather(
        call_as_evidence(
            tools, errors, "sensors", "get_alarm_log", {**window, "limit": ALARM_LIMIT}
        ),
        call_as_evidence(tools, errors, "sensors", "detect_anomalies", window),
        call_as_evidence(
            tools, errors, "docs", "list_incident_reports", {"machine": state.machine}
        ),
    )
    evidence = Evidence(
        machine=detail, alarm_log=alarm_log, anomalies=anomalies, incidents=incidents, errors=errors
    )
    search: dict[str, Any] = {
        "query": doc_query(state.question, evidence),
        "machine_type": detail["type"],
        "k": DOC_HITS,
    }
    if state.intent is Intent.PROCEDURE:
        search = {**search, "query": state.question, "source_types": ["sop"]}
    hits = await call_as_evidence(tools, errors, "docs", "search_docs", search)
    evidence.docs = (hits or {}).get("result", [])
    return {
        "window_start": start,
        "window_end": end,
        "data_ends_at": data_ends_at,
        "evidence": evidence,
    }


# ------------------------------------------------------------------ assess (routing)


def has_gap(evidence: Evidence) -> bool:
    """Something a follow-up tool call could fill: a cut alarm log (the onset is missing, F12), or
    findings without a single matching document."""
    truncated = bool((evidence.alarm_log or {}).get("truncated"))
    findings = (evidence.anomalies or {}).get("findings", [])
    return truncated or (bool(findings) and not evidence.docs)


Route = Literal["follow_up", "hypothesize", "insufficient"]


def route_after_investigate(state: AgentState) -> Route:
    """The `assess` diamond of the design: where to go after investigate. Pure code, no LLM.

    Rules, checked in this order:
    1. no machine (triage found none)                         -> "insufficient"
    2. get_machine failed (evidence.machine is None)          -> "insufficient"
    3. no readings in the window (evidence.anomalies is None) -> "insufficient"
       (asking a model to diagnose without data invites a made-up story: decide it here)
    4. a gap (has_gap) AND follow_ups < MAX_FOLLOW_UPS        -> "follow_up"
    5. otherwise                                              -> "hypothesize"
    """
    if state.machine is None:
        return "insufficient"

    evidence = state.evidence

    if evidence.machine is None:
        return "insufficient"

    if evidence.anomalies is None:
        return "insufficient"

    if has_gap(evidence) and state.follow_ups < MAX_FOLLOW_UPS:
        return "follow_up"

    return "hypothesize"
