"""Reasoning nodes (5.2b): follow_up, hypothesize, guard, and the two reports. agent-design.md §4.

The LLM judges (follow_up, hypothesize); code checks (guard) and writes the answer (report). The
answer is assembled from the VALIDATED hypothesis plus caveats computed by code, so the most
important caveat ("the data is 26 h old") can never be forgotten, and it costs no request.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.evidence_view import data_age_sentence, render
from app.agent.guardrails import check_hypothesis
from app.agent.nodes import call_as_evidence
from app.agent.state import AgentState, Category, Hypothesis
from app.agent.tools import Server, Tools
from app.gateway.structured import ChatModel, StructuredOutputError, chat_structured
from app.gateway.types import ChatMessage, LLMError

MAX_HYPOTHESIS_RETRIES = 1
# Output budgets. Reasoning models spend most tokens "thinking" first: 1500 was not enough for
# the hypothesis in the first live run (5.2b, OutputTruncatedError after 1500 reasoning tokens).
FOLLOW_UP_TOKENS = 1500
HYPOTHESIS_TOKENS = 4000
LLM_FAILURES = (LLMError, StructuredOutputError)

# The read-only tools a follow-up may call: the model picks from THIS list, nothing else.
FOLLOW_UP_TOOLS: dict[str, Server] = {
    "get_readings": "sensors",
    "get_alarm_log": "sensors",
    "detect_anomalies": "sensors",
    "search_docs": "docs",
    "get_document": "docs",
    "get_incident_report": "docs",
    "list_incident_reports": "docs",
}
# A follow-up result for one of the investigation's own tools REPLACES that evidence (e.g. an
# alarm log for an earlier window); anything else is appended as an extra (X1, X2, ...).
REPLACES = {"get_alarm_log": "alarm_log", "detect_anomalies": "anomalies"}

SYSTEM = """\
You are a maintenance engineer's assistant for an industrial plant. You judge EVIDENCE that code
collected from the machine data and the plant's documents. Rules:
- Use only the evidence list. Refer to it by id (F1, A2, D3, I1 ...).
- Cite documents exactly as shown in the evidence list, e.g. [MAN-CMP-001 §5.2].
- A past incident (I..) is an analogy, never proof: the current data decides.
- If one sensor changed alone and its physical neighbours stayed quiet, consider a faulty sensor.
- "unknown" is an allowed answer when the evidence doesn't decide it. Never invent data.
- Respect the DATA line: if the data is old, say nothing about the CURRENT state."""


class FollowUpChoice(BaseModel):
    tool: Literal[
        "get_readings",
        "get_alarm_log",
        "detect_anomalies",
        "search_docs",
        "get_document",
        "get_incident_report",
        "list_incident_reports",
    ]
    args: dict[str, Any] = Field(description="Arguments for that tool, as in its description")
    why: str = Field(description="Which gap this call fills")


Now = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def follow_up(
    state: AgentState, tools: Tools, gateway: ChatModel, now: Now = _utcnow
) -> dict[str, Any]:
    """ONE extra tool call the model chooses, from the allowlist, to fill a gap. Bounded by the
    routing (MAX_FOLLOW_UPS); a failing call is recorded as evidence like any other."""
    text, _ = render(state, now())
    gaps = (
        "The alarm log is cut (the onset is missing)"
        if ((state.evidence.alarm_log or {}).get("truncated"))
        else "Findings have no matching documents"
    )
    try:
        out = await chat_structured(
            gateway,
            [
                ChatMessage(role="system", content=SYSTEM),
                ChatMessage(
                    role="user",
                    content=f"Question: {state.question}\n\nEvidence:\n{text}\n\nGap: {gaps}.\n"
                    f"Choose ONE tool call (machine {state.machine}) that fills this gap.",
                ),
            ],
            FollowUpChoice,
            purpose="agent.follow_up",
            max_tokens=FOLLOW_UP_TOKENS,
        )
    except LLM_FAILURES as e:
        # A failed follow-up costs its slot, not the run: continue with the evidence we have.
        evidence = state.evidence.model_copy(deep=True)
        evidence.errors.append(f"follow_up: {e}")
        return {
            "evidence": evidence,
            "follow_ups": state.follow_ups + 1,
            "llm_requests": state.llm_requests + 1,  # at least one request was spent
        }
    choice, evidence = out.value, state.evidence.model_copy(deep=True)
    result = await call_as_evidence(
        tools, evidence.errors, FOLLOW_UP_TOOLS[choice.tool], choice.tool, choice.args
    )
    if result is not None:
        if choice.tool in REPLACES:
            setattr(evidence, REPLACES[choice.tool], result)
        else:
            evidence.extra.append({"tool": choice.tool, "args": choice.args, "result": result})
    return {
        "evidence": evidence,
        "follow_ups": state.follow_ups + 1,
        "llm_requests": state.llm_requests + out.calls,
    }


async def hypothesize(state: AgentState, gateway: ChatModel, now: Now = _utcnow) -> dict[str, Any]:
    text, _ = render(state, now())
    retry = (
        "\n\nYour previous answer was REJECTED by automatic checks:\n- "
        + "\n- ".join(state.guard_errors)
        + "\nFix every point."
        if state.guard_errors
        else ""
    )
    try:
        out = await chat_structured(
            gateway,
            [
                ChatMessage(role="system", content=SYSTEM),
                ChatMessage(
                    role="user", content=f"Question: {state.question}\n\nEvidence:\n{text}{retry}"
                ),
            ],
            Hypothesis,
            purpose="agent.hypothesize",
            max_tokens=HYPOTHESIS_TOKENS,
        )
    except LLM_FAILURES as e:
        # Free models fail often (rotation, rate limits, truncation). The engineer still gets an
        # answer ("insufficient", with the reason), never a traceback.
        return {
            "hypothesis": None,
            "llm_error": f"hypothesize: {e}",
            "llm_requests": state.llm_requests + 1,
        }
    return {"hypothesis": out.value, "llm_requests": state.llm_requests + out.calls}


def guard(state: AgentState, now: Now = _utcnow) -> dict[str, Any]:
    if state.hypothesis is None:  # the model failed (llm_error): nothing to check, no retry
        return {
            "guard_errors": [state.llm_error or "no hypothesis"],
            "retries": MAX_HYPOTHESIS_RETRIES + 1,
        }
    _, known = render(state, now())
    findings = (state.evidence.anomalies or {}).get("findings", [])
    errors = check_hypothesis(state.hypothesis, known, state.evidence.cited_ids(), findings)
    return {"guard_errors": errors, "retries": state.retries + (1 if errors else 0)}


def route_after_guard(state: AgentState) -> Literal["hypothesize", "insufficient", "report"]:
    if not state.guard_errors:
        return "report"
    return "hypothesize" if state.retries <= MAX_HYPOTHESIS_RETRIES else "insufficient"


_HEADLINE = {
    Category.MACHINE_FAULT: "Most likely a fault ON THE MACHINE",
    Category.SENSOR_FAULT: "Most likely a FAULTY SENSOR, not a machine fault",
    Category.NO_FAULT: "Nothing abnormal in the data",
    Category.UNKNOWN: "The evidence does not decide it",
}


def report(state: AgentState, now: Now = _utcnow) -> dict[str, Any]:
    h = state.hypothesis
    assert h is not None
    lines = [
        f"{_HEADLINE[h.category]} ({state.machine}, confidence {h.confidence}): {h.root_cause}",
        "",
        data_age_sentence(state.data_ends_at, now()),  # by code, always
        "",
        "Evidence:",
        *(f"- {e.id}: {e.statement}" for e in h.evidence),
    ]
    if h.alternatives_ruled_out:
        lines += ["", "Ruled out:", *(f"- {r.cause}: {r.why}" for r in h.alternatives_ruled_out)]
    lines += ["", f"Next check: {h.next_check}"]
    if h.citations:
        lines += ["", "Sources: " + ", ".join(h.citations)]
    lines += ["", f"({state.llm_requests} LLM request(s) used)"]
    return {"answer": "\n".join(lines)}


def report_insufficient(state: AgentState, now: Now = _utcnow) -> dict[str, Any]:
    if state.machine is None and state.llm_error:
        why = f"I couldn't identify the machine (the language model failed: {state.llm_error})"
    elif state.machine is None:
        why = "the question names no machine I could identify; name it like 'M-07'"
    elif state.evidence.machine is None:
        why = "the machine could not be read: " + "; ".join(state.evidence.errors or ["unknown"])
    elif state.evidence.anomalies is None:
        why = (
            "there are no sensor readings in the investigation window ("
            + "; ".join(state.evidence.errors or ["no detail"])
            + ")"
        )
    elif state.llm_error:
        why = (
            f"the language model failed ({state.llm_error}); the evidence was collected, "
            "so asking again later may work"
        )
    else:
        why = "no hypothesis passed the automatic checks: " + "; ".join(state.guard_errors)
    answer = (
        f"I can't give a reliable answer: {why}.\n\n"
        f"{data_age_sentence(state.data_ends_at, now())}\n\n"
        f"({state.llm_requests} LLM request(s) used)"
    )
    return {"answer": answer}
