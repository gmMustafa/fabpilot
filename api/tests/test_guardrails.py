"""Guardrails (your TODO): what code can check about a hypothesis before anyone reads it."""

from typing import Any

from app.agent.graph import build_graph, run_question
from app.agent.guardrails import check_hypothesis, cited_doc_ids
from app.agent.state import Category, EvidenceRef, Hypothesis
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, TOOLS_WITH_CITATIONS, hypothesis_json
from tests.test_agent_nodes import fake_tools

KNOWN = {"F1", "A1", "D1", "D2", "I1"}
RETRIEVED = {"MAN-CMP-001", "SOP-GEN-004", "INC-2024-014"}
ISOLATED = [{"sensor": "discharge_temp", "kind": "trend", "isolated": True}]
SHARED = [{"sensor": "spindle_vibration", "kind": "trend", "isolated": False}]


def hyp(**kw: Any) -> Hypothesis:
    base: dict[str, Any] = {
        "category": Category.SENSOR_FAULT,
        "root_cause": "discharge_temp RTD drifting",
        "confidence": "medium",
        "evidence": [EvidenceRef(id="F1", statement="only discharge_temp moved")],
        "citations": ["[MAN-CMP-001 §5.2]", "[SOP-GEN-004 §4]"],
        "needs_action": True,
        "next_check": "reference thermometer",
    }
    return Hypothesis(**{**base, **kw})


def test_cited_doc_ids() -> None:
    assert cited_doc_ids("[MAN-CMP-001 §5.2]") == {"MAN-CMP-001"}
    assert cited_doc_ids("INC-2024-014 and SOP-GEN-004") == {"INC-2024-014", "SOP-GEN-004"}
    assert cited_doc_ids("the compressor manual") == set()


def test_a_valid_hypothesis_passes() -> None:
    assert check_hypothesis(hyp(), KNOWN, RETRIEVED, ISOLATED) == []


def test_unknown_evidence_id() -> None:
    errors = check_hypothesis(
        hyp(evidence=[EvidenceRef(id="F9", statement="x")]), KNOWN, RETRIEVED, ISOLATED
    )
    assert errors == [
        "evidence id 'F9' does not exist; use only ids from the evidence list",
        NO_DATA,  # 5.5b: the only "data" was the made-up F9, so there is none
    ]


def test_citation_without_a_document_id() -> None:
    errors = check_hypothesis(hyp(citations=["the manual"]), KNOWN, RETRIEVED, ISOLATED)
    assert errors == [
        "citation 'the manual' names no document id; copy citations exactly as listed"
    ]


def test_citation_of_a_document_never_retrieved() -> None:
    """An invented SOP id is the classic hallucinated citation."""
    errors = check_hypothesis(hyp(citations=["[SOP-CMP-099 §4]"]), KNOWN, RETRIEVED, ISOLATED)
    assert errors == [
        "citation '[SOP-CMP-099 §4]' cites SOP-CMP-099, which was not retrieved; "
        "cite only listed documents"
    ]


def test_a_fault_claim_needs_evidence() -> None:
    errors = check_hypothesis(
        hyp(category=Category.MACHINE_FAULT, evidence=[]), KNOWN, RETRIEVED, ISOLATED
    )
    assert errors == ["a fault claim needs at least one evidence id"]


def test_sensor_fault_needs_an_isolated_or_standstill_finding() -> None:
    errors = check_hypothesis(hyp(), KNOWN, RETRIEVED, SHARED)
    assert errors == ["sensor_fault needs an isolated finding or a moves_when_stopped finding"]
    standstill = [{"sensor": "x", "kind": "moves_when_stopped", "isolated": False}]
    assert check_hypothesis(hyp(), KNOWN, RETRIEVED, standstill) == []


def test_all_violations_are_reported_at_once() -> None:
    """One retry must be able to fix everything, so the model needs the full list."""
    bad = hyp(evidence=[EvidenceRef(id="F9", statement="x")], citations=["[SOP-CMP-099 §4]"])
    # unknown id, unretrieved citation, no isolated finding, and (5.5b) F9 doesn't exist, so
    # there is no real data among the evidence either
    assert len(check_hypothesis(bad, KNOWN, RETRIEVED, SHARED)) == 4


def test_no_fault_and_unknown_need_no_evidence() -> None:
    for cat in (Category.NO_FAULT, Category.UNKNOWN):
        assert check_hypothesis(hyp(category=cat, evidence=[]), KNOWN, RETRIEVED, SHARED) == []


# ------------------------------------------------------------------ rule 5 (5.5b, your TODO)

NO_DATA = (
    "a fault claim needs current machine data (an F or A id); "
    "documents and past incidents explain, they don't prove"
)


def test_a_fault_resting_only_on_documents_and_incidents_has_no_data() -> None:
    """ "It was like this last time" is the RAG agent's most dangerous guess."""
    by_analogy = [
        EvidenceRef(id="I1", statement="same last year"),
        EvidenceRef(id="D1", statement="x"),
    ]
    errors = check_hypothesis(
        hyp(category=Category.MACHINE_FAULT, evidence=by_analogy), KNOWN, RETRIEVED, ISOLATED
    )
    assert errors == [NO_DATA]


def test_a_fault_with_an_alarm_as_data_passes_rule_5() -> None:
    alarm = [EvidenceRef(id="A1", statement="TEMP-HIGH fired"), EvidenceRef(id="D1", statement="x")]
    h = hyp(category=Category.MACHINE_FAULT, evidence=alarm)
    assert check_hypothesis(h, KNOWN, RETRIEVED, ISOLATED) == []


def test_a_fault_needs_EXISTING_data_not_a_made_up_f_id() -> None:
    """F9 isn't in the evidence list: it must not count as data (rule 1 reports it too)."""
    made_up = [EvidenceRef(id="F9", statement="x"), EvidenceRef(id="I1", statement="y")]
    errors = check_hypothesis(
        hyp(category=Category.MACHINE_FAULT, evidence=made_up), KNOWN, RETRIEVED, ISOLATED
    )
    assert NO_DATA in errors and len(errors) == 2


def test_rule_5_is_silent_when_rule_3_already_spoke() -> None:
    """No evidence at all: ONE message (rule 3), not two for the same problem."""
    errors = check_hypothesis(
        hyp(category=Category.MACHINE_FAULT, evidence=[]), KNOWN, RETRIEVED, ISOLATED
    )
    assert errors == ["a fault claim needs at least one evidence id"]


def test_unknown_and_no_fault_may_rest_on_documents() -> None:
    """Rule 5 is about FAULT claims: "unknown, see D1" is an honest answer, not a guess."""
    docs_only = [EvidenceRef(id="D1", statement="the manual lists three causes")]
    for cat in (Category.UNKNOWN, Category.NO_FAULT):
        assert (
            check_hypothesis(hyp(category=cat, evidence=docs_only), KNOWN, RETRIEVED, SHARED) == []
        )


# ------------------------------------------------------------------ through the graph


async def test_a_fault_by_analogy_twice_ends_as_insufficient_not_as_a_guess() -> None:
    """Rule 5 rejects, the retry repeats the analogy: "I can't give a reliable answer" with the
    reason, and no work order. The roadmap's "insufficient data instead of guessing"."""
    by_analogy = hypothesis_json(
        category="machine_fault",
        evidence=[{"id": "D1", "statement": "the manual describes overheating like this"}],
        root_cause="Airend overheating, like last year",
    )
    chat = ScriptedChat(by_analogy, by_analogy)  # the first answer and its one retry
    graph = build_graph(fake_tools(TOOLS_WITH_CITATIONS), chat, clock=lambda: NOW)
    state = await run_question(graph, Q1)
    assert state.llm_requests == 2 and state.work_order is None
    answer = state.answer or ""
    assert answer.startswith("I can't give a reliable answer")
    assert "a fault claim needs current machine data" in answer


def test_an_evidence_id_used_as_a_citation_gets_a_message_that_names_the_mistake() -> None:
    """5.5b live run: "[F1]", "[D4]" in citations, twice; the generic message didn't help."""
    errors = check_hypothesis(hyp(citations=["[F1]", "[D1]"]), KNOWN, RETRIEVED, ISOLATED)
    assert errors == [
        f"citation {c!r} is an evidence id: put it in evidence; citations are the bracketed "
        "document references from the D lines, e.g. [MAN-CMP-001 §5.2]"
        for c in ("[F1]", "[D1]")
    ]
