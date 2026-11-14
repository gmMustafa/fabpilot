"""draft_work_order (5.2c): a work-order DRAFT from the right SOP, checked before anyone sees it.

    pick_sop (code)  ->  get_document (the WHOLE SOP, F11)  ->  LLM drafts from that text
                     ->  check_work_order (code)  ->  one retry with the errors, or no draft

A wrong work order isn't just useless, it's dangerous: an invented part number sends someone to
the stores for nothing; a missing lockout step can hurt someone. Those are checkable, so code
checks them. Not saved here: approval and saving come in 5.3.
"""

from typing import Any

from app.agent.guardrails import cited_doc_ids
from app.agent.nodes import call_as_evidence
from app.agent.state import AgentState, Category, WorkOrderDraft
from app.agent.tools import Tools
from app.gateway.structured import ChatModel, StructuredOutputError, chat_structured
from app.gateway.types import ChatMessage, LLMError

VERIFICATION_SOP = "SOP-GEN-004"  # sensor verification: what a suspected false alarm needs first
HOLD_POINT = "⚠ STOP"
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
    3. the SOP's hold points survive: if sop_text contains N occurrences of HOLD_POINT, the draft
       needs at least N actions with safety=True
         -> f"{sop_id} has {n} hold point(s) (⚠ STOP) but only {k} safety action(s)"
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

    # Rule 3: Preserve SOP hold points
    n = sop_text.count(HOLD_POINT)
    k = sum(a.safety for a in wo.actions)

    if k < n:
        errors.append(f"{sop_id} has {n} hold point(s) (⚠ STOP) but only {k} safety action(s)")

    # Rule 4: Verify sensor faults first
    if category is Category.SENSOR_FAULT and wo.sop_ref != VERIFICATION_SOP:
        errors.append(f"a suspected sensor fault needs {VERIFICATION_SOP} first, not {wo.sop_ref}")

    return errors


async def draft_work_order(state: AgentState, tools: Tools, gateway: ChatModel) -> dict[str, Any]:
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
    requests = state.llm_requests
    for _ in range(1 + MAX_WORK_ORDER_RETRIES):
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
            )
        except (LLMError, StructuredOutputError) as e:
            return {
                "sop_id": sop_id,
                "work_order_errors": [f"the language model failed: {e}"],
                "llm_requests": requests + 1,
            }
        requests += out.calls
        errors = check_work_order(out.value, sop_id, sop_text, h.category)
        if not errors:
            return {
                "sop_id": sop_id,
                "sop_text": sop_text,
                "work_order": out.value,
                "llm_requests": requests,
            }
    return {"sop_id": sop_id, "work_order_errors": errors, "llm_requests": requests}
