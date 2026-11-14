"""draft_work_order (5.2c): a work-order DRAFT from the right SOP, checked before anyone sees it.

    pick_sop (code)  ->  get_document (the WHOLE SOP, F11)  ->  LLM drafts from that text
                     ->  check_work_order (code)  ->  one retry with the errors, or no draft

A wrong work order isn't just useless, it's dangerous: an invented part number sends someone to
the stores for nothing; a missing lockout step can hurt someone. Those are checkable, so code
checks them. Not saved here: approval and saving come in 5.3.
"""

from typing import Any

from app.agent.budget import DEFAULT_BUDGET, RunBudget, billed
from app.agent.guardrails import cited_doc_ids
from app.agent.nodes import call_as_evidence
from app.agent.procedure import check_procedure, parse_procedure, safety_notes
from app.agent.state import AgentState, Category, WorkOrderDraft
from app.agent.tools import Tools
from app.gateway.structured import ChatModel, Spend, StructuredOutputError, chat_structured
from app.gateway.types import ChatMessage, LLMError

VERIFICATION_SOP = "SOP-GEN-004"  # sensor verification: what a suspected false alarm needs first
WORK_ORDER_TOKENS = 3000
MAX_WORK_ORDER_RETRIES = 1

SYSTEM = """\
You draft a maintenance work order from ONE standard operating procedure (SOP) that is given to
you in full. Rules:
- Take the steps from the SOP text, in its order; give each action its SOP location (sop_step).
- Mark lockout/tagout steps and every hold point (marked ⚠ STOP in the SOP) as safety=true.
- Parts only from the SOP's parts table, with the part number EXACTLY as written there.
- Duration and staff from the SOP's scope; verification from its acceptance criteria.
- Do not add steps the SOP doesn't contain."""


async def pick_sop(state: AgentState, tools: Tools) -> str | None:
    """Which SOP the order follows. Code, not the model: a SENSOR fault gets the verification SOP
    first (fix the measurement before touching the machine: protocol Q1's trap), otherwise an SOP
    the hypothesis cited, otherwise the best SOP hit for the root cause on this machine type."""
    h = state.hypothesis
    assert h is not None
    if h.category is Category.SENSOR_FAULT:
        return VERIFICATION_SOP
    retrieved = state.evidence.cited_ids()
    for citation in h.citations:
        for doc_id in sorted(cited_doc_ids(citation)):
            if doc_id.startswith("SOP-") and doc_id in retrieved:
                return doc_id
    machine_type = (state.evidence.machine or {}).get("type")
    hits = await call_as_evidence(
        tools,
        [],
        "docs",
        "search_docs",
        {"query": h.root_cause, "machine_type": machine_type, "source_types": ["sop"], "k": 3},
    )
    results = (hits or {}).get("result", [])
    return results[0]["doc_id"] if results else None


def check_work_order(
    wo: WorkOrderDraft, sop_id: str, sop_text: str, category: Category
) -> list[str]:
    """Every safety-relevant violation as one sentence the model can act on; [] if the draft passes.

    Rules (report ALL violations, like check_hypothesis):
    1. wo.sop_ref is the SOP that was read (sop_id)
         -> f"sop_ref {wo.sop_ref!r} is not the SOP you were given ({sop_id})"
    2. every part number appears verbatim in sop_text
         -> f"part {p.part_no!r} is not in {sop_id}'s parts table; use only listed part numbers"
    3. every hold point (⚠ STOP) is covered by a safety action AT ITS LOCATION: the errors of
       check_procedure (5.5). Until 5.5 this was a COUNT (N hold points -> N safety actions),
       which passed a draft that marked a harmless step as safety and dropped the lockout.
    4. a SENSOR_FAULT order follows VERIFICATION_SOP (verify the sensor before touching the machine)
         -> f"a suspected sensor fault needs {VERIFICATION_SOP} first, not {wo.sop_ref}"
    """
    errors: list[str] = []

    # Rule 1: Verify SOP reference
    if wo.sop_ref != sop_id:
        errors.append(f"sop_ref {wo.sop_ref!r} is not the SOP you were given ({sop_id})")

    # Rule 2: Verify part numbers
    for p in wo.parts:
        if p.part_no not in sop_text:
            errors.append(
                f"part {p.part_no!r} is not in {sop_id}'s parts table; use only listed part numbers"
            )

    # Rule 3: every hold point covered by a safety action at its location (5.5; was a count)
    errors += check_procedure(wo, parse_procedure(sop_text), sop_id)[0]

    # Rule 4: Verify sensor faults first
    if category is Category.SENSOR_FAULT and wo.sop_ref != VERIFICATION_SOP:
        errors.append(f"a suspected sensor fault needs {VERIFICATION_SOP} first, not {wo.sop_ref}")

    return errors


def with_code_fields(wo: WorkOrderDraft, sop_id: str, sop_text: str) -> WorkOrderDraft:
    """The parts of the order that are POLICY, set by code over whatever the model sent: the
    SOP's §2 safety notes verbatim, and the reviewer warnings (steps the draft left out)."""
    warnings = check_procedure(wo, parse_procedure(sop_text), sop_id)[1]
    return wo.model_copy(
        update={"safety_notes": safety_notes(sop_text), "reviewer_warnings": warnings}
    )


async def draft_work_order(
    state: AgentState, tools: Tools, gateway: ChatModel, budget: RunBudget = DEFAULT_BUDGET
) -> dict[str, Any]:
    h = state.hypothesis
    assert h is not None
    sop_id = await pick_sop(state, tools)
    if sop_id is None:
        return {"work_order_errors": ["no SOP found for this root cause on this machine type"]}
    doc = await call_as_evidence(tools, [], "docs", "get_document", {"doc_id": sop_id})
    if doc is None:
        return {"sop_id": sop_id, "work_order_errors": [f"could not read {sop_id}"]}
    sop_text = doc["text"]

    errors: list[str] = []
    spend = Spend()  # ONE meter across the draft and its retry
    for _ in range(1 + MAX_WORK_ORDER_RETRIES):
        if refused := budget.check(state, spend):
            # The diagnosis stands; there is just no draft (and the report says why).
            return {"sop_id": sop_id, "work_order_errors": [refused], **billed(state, spend)}
        retry = (
            "\n\nYour previous draft was REJECTED:\n- " + "\n- ".join(errors) + "\nFix every point."
            if errors
            else ""
        )
        try:
            out = await chat_structured(
                gateway,
                [
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(
                        role="user",
                        content=f"Machine {state.machine}. Diagnosis: {h.root_cause}\n\n"
                        f"SOP {sop_id} ({doc['title']}):\n{sop_text}{retry}",
                    ),
                ],
                WorkOrderDraft,
                purpose="agent.work_order",
                max_tokens=WORK_ORDER_TOKENS,
                spend=spend,
            )
        except (LLMError, StructuredOutputError) as e:
            return {
                "sop_id": sop_id,
                "work_order_errors": [f"the language model failed: {e}"],
                **billed(state, spend),
            }
        errors = check_work_order(out.value, sop_id, sop_text, h.category)
        if not errors:
            return {
                "sop_id": sop_id,
                "sop_text": sop_text,
                "work_order": with_code_fields(out.value, sop_id, sop_text),
                **billed(state, spend),
            }
    return {"sop_id": sop_id, "work_order_errors": errors, **billed(state, spend)}
