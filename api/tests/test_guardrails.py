"""Guardrails (your TODO): what code can check about a hypothesis before anyone reads it."""

from typing import Any

from app.agent.guardrails import check_hypothesis, cited_doc_ids
from app.agent.state import Category, EvidenceRef, Hypothesis

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
    assert errors == ["evidence id 'F9' does not exist; use only ids from the evidence list"]


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
    assert len(check_hypothesis(bad, KNOWN, RETRIEVED, SHARED)) == 3


def test_no_fault_and_unknown_need_no_evidence() -> None:
    for cat in (Category.NO_FAULT, Category.UNKNOWN):
        assert check_hypothesis(hyp(category=cat, evidence=[]), KNOWN, RETRIEVED, SHARED) == []
