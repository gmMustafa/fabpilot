"""Work-order drafting: check_work_order (your TODO), pick_sop, and the path through the graph."""

import json
from typing import Any

from app.agent.graph import build_graph, run_question
from app.agent.state import (
    AgentState,
    Category,
    Evidence,
    EvidenceRef,
    Hypothesis,
    WorkAction,
    WorkOrderDraft,
    WorkPart,
)
from app.agent.work_order import VERIFICATION_SOP, check_work_order, pick_sop
from app.db.models import WorkOrderPriority
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, TOOLS_WITH_CITATIONS, hypothesis_json
from tests.test_agent_nodes import fake_tools

# A short SOP in the corpus's shape: a parts table and ONE hold point.
SOP_TEXT = """## §3 Tools and parts

| Item | Part no. |
|---|---|
| Calibrated contact thermometer | REF-TC-01 |

## §4 Procedure

1. Plot the suspect signal with its neighbours.
3. Apply LOTO (SOP-GEN-001). ⚠ STOP: zero-energy verification done?
4. Measure with the reference instrument.

## §5 Acceptance criteria

- Reading within ±2 °C of the reference."""


def draft(**kw: Any) -> WorkOrderDraft:
    base: dict[str, Any] = {
        "title": "Verify the M-08 discharge_temp RTD",
        "priority": WorkOrderPriority.MEDIUM,
        "sop_ref": VERIFICATION_SOP,
        "actions": [
            WorkAction(text="Desk check of the neighbours", sop_step="§4 step 1", safety=False),
            WorkAction(text="LOTO, verify zero energy", sop_step="§4 step 3", safety=True),
            WorkAction(text="Reference measurement", sop_step="§4 step 4", safety=False),
        ],
        "parts": [WorkPart(part_no="REF-TC-01", description="contact thermometer")],
        "duration": "30-60 min",
        "staff": "1 technician (instrumentation)",
        "verification": "within ±2 °C of the reference",
    }
    return WorkOrderDraft(**{**base, **kw})


# ------------------------------------------------------------------ check_work_order (your TODO)


def test_a_valid_draft_passes() -> None:
    assert check_work_order(draft(), VERIFICATION_SOP, SOP_TEXT, Category.SENSOR_FAULT) == []


def test_wrong_sop_ref() -> None:
    errors = check_work_order(
        draft(sop_ref="SOP-CMP-005"), VERIFICATION_SOP, SOP_TEXT, Category.MACHINE_FAULT
    )
    assert errors == ["sop_ref 'SOP-CMP-005' is not the SOP you were given (SOP-GEN-004)"]


def test_an_invented_part_number() -> None:
    parts = [WorkPart(part_no="AF-SC55-X", description="made up")]
    errors = check_work_order(draft(parts=parts), VERIFICATION_SOP, SOP_TEXT, Category.SENSOR_FAULT)
    assert errors == [
        "part 'AF-SC55-X' is not in SOP-GEN-004's parts table; use only listed part numbers"
    ]


def test_a_dropped_hold_point() -> None:
    """The lockout step marked as an ordinary step: the most dangerous omission."""
    unsafe = [a.model_copy(update={"safety": False}) for a in draft().actions]
    errors = check_work_order(
        draft(actions=unsafe), VERIFICATION_SOP, SOP_TEXT, Category.SENSOR_FAULT
    )
    assert errors == ["SOP-GEN-004 has 1 hold point(s) (⚠ STOP) but only 0 safety action(s)"]


def test_a_sensor_fault_must_be_verified_first() -> None:
    """Protocol Q1's trap: don't send someone to repair a compressor whose sensor lies."""
    errors = check_work_order(
        draft(sop_ref="SOP-CMP-005"), "SOP-CMP-005", SOP_TEXT, Category.SENSOR_FAULT
    )
    assert errors == ["a suspected sensor fault needs SOP-GEN-004 first, not SOP-CMP-005"]


def test_all_violations_at_once() -> None:
    bad = draft(
        sop_ref="SOP-CMP-005",
        parts=[WorkPart(part_no="AF-SC55-X", description="x")],
        actions=[WorkAction(text="x", sop_step="§4", safety=False)],
    )
    assert len(check_work_order(bad, VERIFICATION_SOP, SOP_TEXT, Category.SENSOR_FAULT)) == 4


# ------------------------------------------------------------------ pick_sop


def hypothesis(**kw: Any) -> Hypothesis:
    base: dict[str, Any] = {
        "category": Category.MACHINE_FAULT,
        "root_cause": "Inlet filter clogged",
        "confidence": "high",
        "evidence": [EvidenceRef(id="F1", statement="dp rising")],
        "citations": ["[SOP-CMP-005 §4]"],
        "needs_action": True,
        "next_check": "change the filter",
    }
    return Hypothesis(**{**base, **kw})


async def test_a_sensor_fault_gets_the_verification_sop_without_searching() -> None:
    tools = fake_tools()
    state = AgentState(question="q", hypothesis=hypothesis(category=Category.SENSOR_FAULT))
    assert await pick_sop(state, tools) == VERIFICATION_SOP
    assert tools.calls == []


async def test_a_cited_and_retrieved_sop_is_used() -> None:
    ev = Evidence(docs=[{"doc_id": "SOP-CMP-005"}])
    state = AgentState(question="q", hypothesis=hypothesis(), evidence=ev)
    assert await pick_sop(state, fake_tools()) == "SOP-CMP-005"


async def test_without_a_cited_sop_the_best_sop_hit_is_used() -> None:
    tools = fake_tools({("docs", "search_docs"): {"result": [{"doc_id": "SOP-CMP-005"}]}})
    ev = Evidence(machine={"type": "compressor"})
    state = AgentState(question="q", hypothesis=hypothesis(citations=[]), evidence=ev)
    assert await pick_sop(state, tools) == "SOP-CMP-005"
    assert tools.calls[0][2]["source_types"] == ["sop"]


# ------------------------------------------------------------------ through the graph


def answer(state: AgentState) -> str:
    """run_question guarantees an answer at run time; this narrows the type for mypy."""
    assert state.answer is not None
    return state.answer


def tools_with_sop() -> Any:
    doc = {"doc_id": VERIFICATION_SOP, "title": "Sensor verification", "text": SOP_TEXT}
    return fake_tools({**TOOLS_WITH_CITATIONS, ("docs", "get_document"): doc})


async def test_q1_drafts_a_verification_order_from_the_whole_sop() -> None:
    chat = ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())
    tools = tools_with_sop()
    state = await run_question(build_graph(tools, chat, clock=lambda: NOW), Q1)
    assert state.llm_requests == 2 and state.work_order is not None
    assert ("docs", "get_document", {"doc_id": VERIFICATION_SOP}) in tools.calls  # F11: read it
    assert "DRAFT work order (not saved; needs approval)" in answer(state)
    assert "[SAFETY] LOTO, verify zero energy" in answer(state)
    assert "⚠ STOP" in chat.conversations[1][1].content  # the model got the full SOP text


async def test_an_invented_part_twice_means_no_draft_but_still_an_answer() -> None:
    invented = draft(parts=[WorkPart(part_no="AF-SC55-X", description="x")]).model_dump_json()
    chat = ScriptedChat(hypothesis_json(needs_action=True), invented, invented)
    state = await run_question(build_graph(tools_with_sop(), chat, clock=lambda: NOW), Q1)
    assert state.work_order is None and state.llm_requests == 3
    assert "No work order drafted" in answer(state) and "AF-SC55-X" in answer(state)
    assert answer(state).startswith("Most likely a FAULTY SENSOR")  # the diagnosis still stands


async def test_nothing_to_do_means_no_work_order_and_one_request() -> None:
    """ "Is M-05 OK?" with a healthy machine: no draft, no extra request (5.1's no-action path)."""
    healthy = hypothesis_json(
        category="no_fault", evidence=[], citations=[], needs_action=False, root_cause="Normal."
    )
    chat = ScriptedChat(healthy)
    state = await run_question(
        build_graph(tools_with_sop(), chat, clock=lambda: NOW), "Is M-05 OK?"
    )
    assert state.llm_requests == 1 and state.work_order is None
    assert answer(state).startswith("Nothing abnormal in the data")


def test_json_roundtrip_of_the_draft() -> None:
    assert WorkOrderDraft.model_validate(json.loads(draft().model_dump_json())) == draft()
