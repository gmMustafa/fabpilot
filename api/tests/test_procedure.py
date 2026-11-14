"""SOP structure (5.5): the parser against ALL real SOPs, and check_procedure (your TODO)."""

import pytest

from app.agent.graph import build_graph, run_question
from app.agent.procedure import (
    HOLD_POINT,
    check_procedure,
    parse_procedure,
    safety_notes,
    sections_in,
    steps_in,
)
from app.agent.state import Category, WorkAction
from app.agent.work_order import VERIFICATION_SOP, check_work_order
from app.testing import API_DIR, ScriptedChat
from tests.test_agent_graph import NOW, Q1, TOOLS_WITH_CITATIONS, hypothesis_json
from tests.test_agent_nodes import fake_tools
from tests.test_work_order import draft

SOPS = API_DIR.parent / "data" / "docs" / "sops"
EN_DASH = "\u2013"  # how SOPs and models write ranges, e.g. "steps 7-8" with U+2013


def sop(doc_id: str) -> str:
    return (SOPS / f"{doc_id}.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------ the parser, on the corpus

EXPECTED_HOLD_POINTS = {
    "SOP-CMP-005": ["step 4"],
    "SOP-CNC-002": ["step 3", "step 8"],
    "SOP-CNC-003": ["step 5"],
    "SOP-CNV-008": ["step 1"],
    "SOP-GEN-001": ["§4.3"],  # a whole SECTION: "### 4.3 Verify zero energy — ⚠ STOP"
    "SOP-GEN-004": ["step 10"],
    "SOP-GEN-007": ["step 3"],
    "SOP-PMP-006": ["step 3"],  # on a CONTINUATION line of step 3
    "SOP-PMP-009": ["step 3"],
}


def test_every_sop_in_the_corpus_is_covered_here() -> None:
    """Docs-as-code: a NEW SOP must be added to EXPECTED_HOLD_POINTS (and checked by a human)."""
    assert sorted(p.stem for p in SOPS.glob("*.md")) == sorted(EXPECTED_HOLD_POINTS)


@pytest.mark.parametrize("doc_id", sorted(EXPECTED_HOLD_POINTS))
def test_hold_points_of_each_sop(doc_id: str) -> None:
    text = sop(doc_id)
    procedure = parse_procedure(text)
    assert [h.where for h in procedure.hold_points] == EXPECTED_HOLD_POINTS[doc_id]
    # ...and none is missed: every ⚠ STOP in the file became exactly one hold point.
    assert text.count(HOLD_POINT) == len(procedure.hold_points)
    assert procedure.steps == list(range(1, len(procedure.steps) + 1))  # numbered 1..n
    assert safety_notes(text)  # every SOP has §2 Safety bullets


def test_a_section_hold_point_covers_the_steps_of_its_section() -> None:
    (hp,) = parse_procedure(sop("SOP-GEN-001")).hold_points
    assert hp.section == "4.3" and hp.steps == {8, 9, 10}


def test_safety_notes_are_verbatim_with_continuation_lines_joined() -> None:
    notes = safety_notes(sop("SOP-GEN-004"))
    assert notes[0] == (
        "Readings with the reference instrument are taken from outside guards where possible. "
        "If a guard must be opened, apply LOTO (SOP-GEN-001) and take the reading at standstill."
    )
    assert notes[1].startswith("Hot surfaces: contact thermometers")


@pytest.mark.parametrize(
    ("sop_step", "steps", "sections"),
    [
        ("§4.3 step 7", {7}, {"4.3"}),
        (f"§4 steps 7{EN_DASH}8", {7, 8}, set()),
        ("§4.3 steps 8-10", {8, 9, 10}, {"4.3"}),
        ("§4.3", set(), {"4.3"}),
        ("Step 2", {2}, set()),
    ],
)
def test_references_in_sop_step(sop_step: str, steps: set[int], sections: set[str]) -> None:
    assert steps_in(sop_step) == steps and sections_in(sop_step) == sections


# ------------------------------------------------------------------ check_procedure (your TODO)

GEN_004 = parse_procedure(sop("SOP-GEN-004"))


def act(sop_step: str, safety: bool = False) -> WorkAction:
    return WorkAction(text="x", sop_step=sop_step, safety=safety)


def covering(*safety_steps: int) -> list[WorkAction]:
    """All 10 steps of SOP-GEN-004; the given ones marked safety."""
    return [act(f"§4 step {n}", safety=n in safety_steps) for n in range(1, 11)]


def test_a_complete_draft_with_the_hold_point_passes() -> None:
    assert check_procedure(draft(actions=covering(10)), GEN_004, VERIFICATION_SOP) == ([], [])


def test_safety_on_the_wrong_step_is_an_error() -> None:
    """THE hole of the old count rule: 1 hold point, 1 safety action, but on the harmless step."""
    errors, _ = check_procedure(draft(actions=covering(1)), GEN_004, VERIFICATION_SOP)
    assert errors == [
        "hold point at step 10 (⚠ STOP) is not covered by a safety action; "
        "mark the action for step 10 as safety=true"
    ]


def test_a_non_safety_action_at_the_hold_point_does_not_cover_it() -> None:
    errors, _ = check_procedure(draft(actions=covering()), GEN_004, VERIFICATION_SOP)
    assert len(errors) == 1


def test_missing_steps_are_a_warning_not_an_error() -> None:
    actions = [a for a in covering(10) if steps_in(a.sop_step) not in ({3}, {6})]
    errors, warnings = check_procedure(draft(actions=actions), GEN_004, VERIFICATION_SOP)
    assert errors == []
    assert warnings == ["steps 3, 6 of SOP-GEN-004 are not in the draft; check whether they apply"]


def test_one_missing_step_is_singular() -> None:
    actions = [a for a in covering(10) if steps_in(a.sop_step) != {6}]
    _, warnings = check_procedure(draft(actions=actions), GEN_004, VERIFICATION_SOP)
    assert warnings == ["step 6 of SOP-GEN-004 is not in the draft; check whether it applies"]


def test_a_range_reference_covers_every_step_in_it() -> None:
    actions = [
        act(f"§4.1 steps 1{EN_DASH}5"),
        act("§4.2 steps 6-9"),
        act("§4.3 step 10", safety=True),
    ]
    assert check_procedure(draft(actions=actions), GEN_004, VERIFICATION_SOP) == ([], [])


GEN_001 = parse_procedure(sop("SOP-GEN-001"))


@pytest.mark.parametrize("sop_step", ["§4.3", f"§4.3 steps 8{EN_DASH}10", "§4.3 steps 8-10"])
def test_a_section_hold_point_is_covered_by_the_section_or_all_its_steps(sop_step: str) -> None:
    actions = [act(f"§4 step {n}") for n in range(1, 15)] + [act(sop_step, safety=True)]
    assert check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001") == ([], [])


def test_a_section_hold_point_may_be_split_over_several_safety_actions() -> None:
    actions = [act(f"§4 step {n}", safety=n in (8, 9, 10)) for n in range(1, 15)]
    assert check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001")[0] == []


def test_one_safety_step_of_a_section_hold_point_is_not_enough() -> None:
    """5.5a quiz: "Do not start work until steps 8-10 are confirmed" - all three, not one."""
    actions = [act(f"§4 step {n}") for n in range(1, 15)] + [act("§4.3 step 9", safety=True)]
    errors, _ = check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001")
    assert errors == [
        "hold point at §4.3 (⚠ STOP) is not covered by a safety action; "
        "mark the action for §4.3 as safety=true (no safety action for steps 8, 10)"
    ]


def test_a_bare_section_reference_counts_its_steps_as_in_the_draft() -> None:
    """Rule A: "§4.3" alone covers 8-10, so no misleading "steps 8, 9, 10 missing" warning."""
    actions = [act(f"§4 step {n}") for n in range(1, 15) if n not in (8, 9, 10)]
    actions.append(act("§4.3", safety=True))
    assert check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001") == ([], [])


def test_an_explicit_step_inside_a_section_does_not_count_the_whole_section() -> None:
    """ "§4.3 step 9" is step 9 ONLY: counting all of §4.3 would hide that 8 and 10 are missing."""
    actions = [act(f"§4 step {n}") for n in range(1, 15) if n not in (8, 9, 10)]
    actions.append(act("§4.3 step 9", safety=True))
    _, warnings = check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001")
    assert warnings == ["steps 8, 10 of SOP-GEN-001 are not in the draft; check whether they apply"]


def test_a_section_hold_point_without_a_safety_action_is_an_error() -> None:
    actions = [act(f"§4 step {n}", safety=n == 4) for n in range(1, 15)]
    errors, _ = check_procedure(draft(actions=actions), GEN_001, "SOP-GEN-001")
    assert errors == [
        "hold point at §4.3 (⚠ STOP) is not covered by a safety action; "
        "mark the action for §4.3 as safety=true"
    ]


# ------------------------------------------------------------------ with the REAL SOP-GEN-004

LIVE_Q1_STEPS = [1, 2, 4, 5, 7, 8, 9, 10]  # what the free model drafted in the 5.2-5.4 live runs


def test_check_work_order_rejects_the_count_rules_blind_spot() -> None:
    wrong = draft(actions=[act(f"§4 step {n}", safety=n == 1) for n in LIVE_Q1_STEPS])
    errors = check_work_order(wrong, VERIFICATION_SOP, sop("SOP-GEN-004"), Category.SENSOR_FAULT)
    assert errors == [
        "hold point at step 10 (⚠ STOP) is not covered by a safety action; "
        "mark the action for step 10 as safety=true"
    ]


async def test_the_live_q1_draft_passes_with_notes_warnings_and_the_sop_as_source() -> None:
    live = draft(
        actions=[act(f"§4 step {n}", safety=n == 10) for n in LIVE_Q1_STEPS],
        parts=[],
        safety_notes=["the model tried to write this"],  # code overwrites it
    )
    doc = {"doc_id": VERIFICATION_SOP, "title": "Sensor verification", "text": sop("SOP-GEN-004")}
    tools = fake_tools({**TOOLS_WITH_CITATIONS, ("docs", "get_document"): doc})
    chat = ScriptedChat(hypothesis_json(needs_action=True), live.model_dump_json())
    state = await run_question(build_graph(tools, chat, clock=lambda: NOW), Q1)

    wo = state.work_order
    assert wo is not None and state.llm_requests == 2  # a warning costs no retry
    assert wo.safety_notes == safety_notes(sop("SOP-GEN-004"))
    assert wo.reviewer_warnings == [
        "steps 3, 6 of SOP-GEN-004 are not in the draft; check whether they apply"
    ]
    answer = state.answer or ""
    assert "SAFETY (SOP-GEN-004 §2): Readings with the reference instrument" in answer
    assert "⚠ Note for the reviewer: steps 3, 6 of SOP-GEN-004" in answer
    assert "[SOP-GEN-004]" in answer.split("Sources: ")[1]
