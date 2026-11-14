"""The agent's state (5.2a): one typed object flowing through the graph (agent-design.md §3).

Each node returns a dict with ONLY the fields it owns; LangGraph merges it into the state. So the
checkpointer (5.4) saves a full, readable record of every step, and a field has exactly one writer.

Tool results are kept as plain dicts (the MCP servers' structured output): the state must be
JSON-serialisable for the checkpointer, and the agent reads them, it doesn't re-validate them.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models import WorkOrderPriority


class Intent(StrEnum):
    DIAGNOSE = "diagnose"  # "what's wrong / why did it alarm?"
    STATUS = "status"  # "is it OK?"
    HISTORY = "history"  # "has this happened before?"
    PROCEDURE = "procedure"  # "how do I replace ...?"


class Category(StrEnum):
    MACHINE_FAULT = "machine_fault"  # the machine itself is degrading or failed
    SENSOR_FAULT = "sensor_fault"  # the machine is fine, a sensor lies (a false alarm)
    NO_FAULT = "no_fault"  # nothing abnormal in the data
    UNKNOWN = "unknown"  # the evidence doesn't decide it: an ALLOWED answer


class EvidenceRef(BaseModel):
    id: str = Field(description="An id from the evidence list, e.g. 'F1', 'A2', 'D3'")
    statement: str = Field(description="What this evidence shows, in one sentence")


class RuledOut(BaseModel):
    cause: str
    why: str = Field(description="Which evidence rules it out")


class Hypothesis(BaseModel):
    """The model's judgement, in a shape code can check (guardrails) before anyone reads it."""

    category: Category
    root_cause: str = Field(description="One sentence; for unknown: what is missing to decide")
    confidence: Literal["low", "medium", "high"]
    evidence: list[EvidenceRef] = Field(description="The evidence ids this conclusion rests on")
    citations: list[str] = Field(
        description="Document citations exactly as shown in the evidence list, e.g. "
        "'[MAN-CMP-001 §5.2]'"
    )
    alternatives_ruled_out: list[RuledOut] = Field(default_factory=list)
    needs_action: bool = Field(description="True if someone should do something on the machine")
    next_check: str = Field(description="The single most useful next step for the engineer")


class WorkAction(BaseModel):
    text: str = Field(description="What to do, in the SOP's words where possible")
    sop_step: str = Field(description="Where in the SOP this comes from, e.g. '§4 step 6'")
    safety: bool = Field(description="True for lockout, hold points (⚠ STOP) and safety checks")


class WorkPart(BaseModel):
    part_no: str = Field(description="Exactly as in the SOP's parts table, e.g. 'AF-SC55-E'")
    description: str
    quantity: int = 1


class WorkOrderDraft(BaseModel):
    """A DRAFT for a human to approve (5.3); never saved by the agent on its own."""

    title: str
    priority: WorkOrderPriority
    sop_ref: str = Field(description="The SOP this order follows, e.g. 'SOP-CMP-005'")
    actions: list[WorkAction]
    parts: list[WorkPart] = Field(default_factory=list)
    duration: str = Field(description="From the SOP's scope, e.g. '30 min'")
    staff: str = Field(description="From the SOP's scope, e.g. '1 technician'")
    verification: str = Field(description="The SOP's acceptance criteria, in one or two lines")


class Evidence(BaseModel):
    """Everything the investigation collected, as the tools returned it."""

    machine: dict[str, Any] | None = None  # get_machine
    alarm_log: dict[str, Any] | None = None  # get_alarm_log (total_count, truncated, alarms ...)
    anomalies: dict[str, Any] | None = None  # detect_anomalies; None = no readings in the window
    docs: list[dict[str, Any]] = Field(default_factory=list)  # search_docs hits
    incidents: dict[str, Any] | None = None  # list_incident_reports(machine=...)
    extra: list[dict[str, Any]] = Field(default_factory=list)  # follow-up calls: tool, args, result
    errors: list[str] = Field(default_factory=list)  # tool errors, kept for the report

    def cited_ids(self) -> set[str]:
        """Every document id the investigation actually retrieved: the only ones a hypothesis may
        cite (guardrails, 5.2b)."""
        ids = {d["doc_id"] for d in self.docs}
        if self.incidents:
            ids |= {r["doc_id"] for r in self.incidents.get("reports", [])}
        return ids


class AgentState(BaseModel):
    question: str
    # triage
    machine: str | None = None
    intent: Intent = Intent.DIAGNOSE
    # investigate
    window_start: datetime | None = None
    window_end: datetime | None = None
    data_ends_at: datetime | None = None
    evidence: Evidence = Field(default_factory=Evidence)
    # loop bookkeeping
    follow_ups: int = 0  # extra tool calls the model asked for (max MAX_FOLLOW_UPS)
    llm_requests: int = 0  # every LLM request of this run (the budget guard, 5.4)
    # hypothesize / guardrails
    hypothesis: Hypothesis | None = None
    guard_errors: list[str] = Field(default_factory=list)
    retries: int = 0  # hypotheses rejected by the guardrails so far
    # An LLM call that failed (all models down, output truncated, no valid JSON). A STATE, not
    # an exception: free models fail often, and the engineer must still get an answer.
    llm_error: str | None = None
    # draft_work_order
    sop_id: str | None = None  # the SOP read in full for the draft
    work_order: WorkOrderDraft | None = None
    work_order_errors: list[str] = Field(default_factory=list)  # why no draft was kept
    # report
    answer: str | None = None
