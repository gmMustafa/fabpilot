"""Human-in-the-loop (5.3): nothing the model drafted is saved until a named person decided.

    report -> await_approval --(interrupt: the run PAUSES here, state in the checkpointer)--
              ... minutes or days later: Command(resume=Approval) ...
              await_approval -> [route_after_approval]
                                  approval_errors -> await_approval   (ask again, with the errors)
                                  otherwise       -> save_work_order -> END

THE TRAP: on resume LangGraph re-runs the interrupted node FROM ITS START; the second time,
interrupt() returns the human's answer instead of pausing. So await_approval must not do
anything with side effects (no DB write, no LLM call): they would run twice. It only asks and
checks. Saving is its own node, which runs once, after the decision.

The decision is untrusted input from outside the agent: validated, and an edited draft goes
through the same check_work_order as the model's draft (a human can delete a lockout step too).
That loop is bounded by the human, not by an LLM: it costs no requests.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from langgraph.types import interrupt
from pydantic import ValidationError

from app.agent.state import AgentState, Approval, WorkOrderDraft
from app.agent.work_order import check_work_order


@dataclass(frozen=True)
class Decision:
    """Everything save_work_order hands to the store: the draft AS DECIDED, and by whom."""

    machine: str
    question: str
    root_cause: str
    draft: WorkOrderDraft
    status: Literal["approved", "rejected"]
    by: str
    at: datetime
    note: str | None


class WorkOrderStore(Protocol):
    """Where decided work orders go: Postgres in the app (store.py), a list in tests."""

    async def save(self, decision: Decision) -> int:
        """Persist the decision; return the new work order's id."""
        ...


def check_approval(approval: Approval, state: AgentState) -> list[str]:
    """Every reason the decision can't be saved as given, as one sentence; [] if it can.

    Rules (report ALL violations, like check_work_order):
    1. `by` names someone (not empty or only spaces): the DB requires it for approved/rejected
         -> "the decision needs the reviewer's name (by)"
    2. a rejection has a reason (not empty or only spaces): "rejected, no reason" is no audit trail
         -> "a rejection needs a reason (it is kept for the audit trail)"
    3. action "edit" comes with the edited draft
         -> "an edit needs the edited work order"
    4. an edited draft passes check_work_order against the SOP the original was drafted from
       (state.sop_id, state.sop_text, state.hypothesis.category); prefix each error with
         -> f"your edit: {error}"

    Hint: rules 3 and 4 are an if/else on `approval.edited is None` inside `action == "edit"`.
    """
    errors: list[str] = []

    # Rule 1: Reviewer name is required
    if not approval.by.strip():
        errors.append("the decision needs the reviewer's name (by)")

    # Rule 2: Rejection requires a reason
    if approval.action == "reject" and not (approval.reason or "").strip():
        errors.append("a rejection needs a reason (it is kept for the audit trail)")

    # Rules 3 & 4: Validate edited work order
    if approval.action == "edit":
        if approval.edited is None:
            errors.append("an edit needs the edited work order")
        else:
            # Not user input: a paused run always has these (a draft needs all three). If one is
            # missing, OUR code is broken, so crash loudly instead of returning a message.
            assert state.sop_id and state.sop_text and state.hypothesis
            edit_errors = check_work_order(
                approval.edited,
                state.sop_id,
                state.sop_text,
                state.hypothesis.category,
            )
            errors.extend(f"your edit: {error}" for error in edit_errors)

    return errors


def await_approval(state: AgentState) -> dict[str, Any]:
    """ONLY ask and check (see the module docstring: this node runs twice per decision)."""
    assert state.work_order is not None
    raw = interrupt(
        {
            "work_order": state.work_order.model_dump(mode="json"),
            "errors": state.approval_errors,  # why the previous decision was sent back
        }
    )
    try:
        approval = Approval.model_validate(raw)
    except ValidationError as e:
        # A malformed answer (a typo in the CLI, a bad API payload) must not kill a paused run:
        # ask again, and say what was wrong.
        return {"approval": None, "approval_errors": [f"invalid decision: {e}"]}
    return {"approval": approval, "approval_errors": check_approval(approval, state)}


def route_after_approval(state: AgentState) -> str:
    return "await_approval" if state.approval_errors else "save_work_order"


async def save_work_order(
    state: AgentState, store: WorkOrderStore, now: Callable[[], datetime]
) -> dict[str, Any]:
    a, h = state.approval, state.hypothesis
    assert a is not None and h is not None and state.work_order is not None
    assert state.machine is not None
    final = a.edited if a.action == "edit" and a.edited is not None else state.work_order
    note = a.reason or ("edited by the reviewer before approval" if a.action == "edit" else None)
    work_order_id = await store.save(
        Decision(
            machine=state.machine,
            question=state.question,
            root_cause=h.root_cause,
            draft=final,
            status="rejected" if a.action == "reject" else "approved",
            by=a.by.strip(),
            at=now(),
            note=note,
        )
    )
    return {"work_order_id": work_order_id}
