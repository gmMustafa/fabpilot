"""Reasoning nodes (5.2b): follow_up, hypothesize, guard, and the two reports. agent-design.md §4.

The LLM judges (follow_up, hypothesize); code checks (guard) and writes the answer (report). The
answer is assembled from the VALIDATED hypothesis plus caveats computed by code, so the most
important caveat ("the data is 26 h old") can never be forgotten, and it costs no request.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.budget import BUDGET_PREFIX, DEFAULT_BUDGET, RunBudget, billed, usage_line
from app.agent.evidence_view import data_age_sentence, render
from app.agent.guardrails import check_hypothesis
from app.agent.nodes import call_as_evidence
from app.agent.state import AgentState, Category, Hypothesis
from app.agent.tools import Server, Tools
from app.gateway.structured import ChatModel, Spend, StructuredOutputError, chat_structured
from app.gateway.types import ChatMessage, LLMError

logger = logging.getLogger(__name__)

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
- Use only the evidence list. Refer to it by id (F1, A2, D3, I1 ...) in "evidence".
- "citations" are ONLY the bracketed document references inside the D lines, copied exactly,
  e.g. [MAN-CMP-001 §5.2]. An evidence id like F1 or D4 is never a citation.
- A past incident (I..) is an analogy, never proof: the current data decides.
- A fault (machine_fault or sensor_fault) needs current data among its evidence: an F or A id.
  Documents (D..) and past incidents (I..) explain, they never prove.
- If one sensor changed alone and its physical neighbours stayed quiet, consider a faulty sensor.
- "unknown" is an allowed answer when the evidence doesn't decide it. Never invent data.
- Respect the Data line: if the data is old, say nothing about the CURRENT state. Old data does
  NOT stop a diagnosis: diagnose what the data shows up to its end, and say it is that old."""


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
    state: AgentState,
    tools: Tools,
    gateway: ChatModel,
    now: Now = _utcnow,
    budget: RunBudget = DEFAULT_BUDGET,
) -> dict[str, Any]:
    """ONE extra tool call the model chooses, from the allowlist, to fill a gap. Bounded by the
    routing (MAX_FOLLOW_UPS); a failing call is recorded as evidence like any other."""
    text, _ = render(state, now())
    gaps = (
        "The alarm log is cut (the onset is missing)"
        if ((state.evidence.alarm_log or {}).get("truncated"))
        else "Findings have no matching documents"
    )
    if refused := budget.check(state):
        # Like a failed follow-up: costs its slot, the run goes on with the evidence it has.
        evidence = state.evidence.model_copy(deep=True)
        evidence.errors.append(f"follow_up: {refused}")
        return {"evidence": evidence, "follow_ups": state.follow_ups + 1}
    spend = Spend()
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
            spend=spend,
        )
    except LLM_FAILURES as e:
        # A failed follow-up costs its slot, not the run: continue with the evidence we have.
        evidence = state.evidence.model_copy(deep=True)
        evidence.errors.append(f"follow_up: {e}")
        return {"evidence": evidence, "follow_ups": state.follow_ups + 1, **billed(state, spend)}
    choice, evidence = out.value, state.evidence.model_copy(deep=True)
    result = await call_as_evidence(
        tools, evidence.errors, FOLLOW_UP_TOOLS[choice.tool], choice.tool, choice.args
    )
    if result is not None:
        if choice.tool in REPLACES:
            setattr(evidence, REPLACES[choice.tool], result)
        else:
            evidence.extra.append({"tool": choice.tool, "args": choice.args, "result": result})
    call = {"tool": choice.tool, "args": choice.args, "ok": result is not None}
    return {
        "evidence": evidence,
        "follow_ups": state.follow_ups + 1,
        "follow_up_calls": [*state.follow_up_calls, call],
        **billed(state, spend),
    }


async def hypothesize(
    state: AgentState, gateway: ChatModel, now: Now = _utcnow, budget: RunBudget = DEFAULT_BUDGET
) -> dict[str, Any]:
    text, _ = render(state, now())
    retry = (
        "\n\nYour previous answer was REJECTED by automatic checks:\n- "
        + "\n- ".join(state.guard_errors)
        # Without this, a model under pressure retreats to "unknown": the answer with the fewest
        # checks becomes the easiest one to pass, not the truest (5.5b live run).
        + "\nFix every point. Keep your diagnosis if the evidence supports it: answer "
        '"unknown" only if the evidence really does not decide it.'
        if state.guard_errors
        else ""
    )
    if refused := budget.check(state):
        return {"hypothesis": None, "llm_error": f"hypothesize: {refused}"}
    spend = Spend()
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
            spend=spend,
        )
    except LLM_FAILURES as e:
        # Free models fail often (rotation, rate limits, truncation). The engineer still gets an
        # answer ("insufficient", with the reason), never a traceback.
        return {"hypothesis": None, "llm_error": f"hypothesize: {e}", **billed(state, spend)}
    return {"hypothesis": out.value, **billed(state, spend)}


def guard(state: AgentState, now: Now = _utcnow) -> dict[str, Any]:
    if state.hypothesis is None:  # the model failed (llm_error): nothing to check, no retry
        return {
            "guard_errors": [state.llm_error or "no hypothesis"],
            "retries": MAX_HYPOTHESIS_RETRIES + 1,
        }
    _, known = render(state, now())
    findings = (state.evidence.anomalies or {}).get("findings", [])
    errors = check_hypothesis(state.hypothesis, known, state.evidence.cited_ids(), findings)
    if errors:
        # Visible even when the retry passes (5.5b live run: a rejected first answer, then an
        # "unknown" retry, and no trace of WHY the first one was rejected).
        logger.warning(
            "hypothesis rejected (%s, %s): %s",
            state.hypothesis.category,
            state.hypothesis.root_cause[:80],
            "; ".join(errors),
        )
    return {"guard_errors": errors, "retries": state.retries + (1 if errors else 0)}


GuardRoute = Literal["hypothesize", "insufficient", "draft_work_order", "report"]


def route_after_guard(state: AgentState) -> GuardRoute:
    """A rejected hypothesis: retry once, then insufficient. A valid one: a work order only if
    someone has to DO something about a fault; "is M-05 OK?" -> healthy -> just report (5.1)."""
    if state.guard_errors:
        return "hypothesize" if state.retries <= MAX_HYPOTHESIS_RETRIES else "insufficient"
    h = state.hypothesis
    assert h is not None
    fault = h.category in (Category.MACHINE_FAULT, Category.SENSOR_FAULT)
    return "draft_work_order" if fault and h.needs_action else "report"


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
    wo = state.work_order
    if wo is not None:
        lines += [
            "",
            f"DRAFT work order (not saved; needs approval): {wo.title}",
            f"  priority {wo.priority.value} | {wo.duration} | {wo.staff} | follows {wo.sop_ref}",
            # From the SOP's §2 by code (5.5): before the steps, where a technician reads first.
            *(f"  SAFETY ({wo.sop_ref} §2): {note}" for note in wo.safety_notes),
            *(
                f"  {i}. {'[SAFETY] ' if a.safety else ''}{a.text} ({a.sop_step})"
                for i, a in enumerate(wo.actions, start=1)
            ),
        ]
        if wo.parts:
            lines.append("  parts: " + ", ".join(f"{p.quantity}x {p.part_no}" for p in wo.parts))
        lines.append(f"  done when: {wo.verification}")
        lines += [f"  ⚠ Note for the reviewer: {w}" for w in wo.reviewer_warnings]
    elif state.work_order_errors:
        lines += ["", "No work order drafted: " + "; ".join(state.work_order_errors)]
    sources = list(h.citations)
    if wo is not None and not any(wo.sop_ref in c for c in sources):
        sources.append(f"[{wo.sop_ref}]")  # the order follows it: it IS a source (5.5)
    if sources:
        lines += ["", "Sources: " + ", ".join(sources)]
    lines += ["", usage_line(state)]
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
    elif state.llm_error and BUDGET_PREFIX in state.llm_error:
        # Not "the model failed": WE stopped, on purpose, before spending more of the quota.
        why = f"this question's LLM budget is used up ({state.llm_error})"
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
        f"{usage_line(state)}"
    )
    return {"answer": answer}
