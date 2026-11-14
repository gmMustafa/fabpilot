"""Guardrails (5.2b; more in 5.5): what code can check about a hypothesis BEFORE anyone reads it.

A model can sound right and still cite a document it never saw, or refer to evidence that
doesn't exist. Those are checkable facts, so code checks them; the model gets ONE retry with the
error list, then the run says "insufficient" instead of guessing.
"""

import re
from typing import Any

from app.agent.state import Category, Hypothesis

DOC_ID = re.compile(r"(?:MAN|SOP)-[A-Z]{3}-\d{3}|INC-\d{4}-\d{3}")

# Evidence classes that are CURRENT DATA of this machine (evidence_view.py's labels). X (follow-up
# results) is left out on purpose: it can be data (get_readings) or a document (search_docs).
DATA_IDS = ("F", "A")


def cited_doc_ids(citation: str) -> set[str]:
    """Document ids inside a citation string: "[MAN-CMP-001 §5.2]" -> {"MAN-CMP-001"}."""
    return set(DOC_ID.findall(citation))


def check_hypothesis(
    h: Hypothesis,
    known_ids: set[str],
    retrieved_docs: set[str],
    findings: list[dict[str, Any]],
) -> list[str]:
    """Every rule violation as one sentence the model can act on; [] if the hypothesis passes.

    Rules (report ALL violations, not only the first: one retry must be able to fix them all):
    1. every h.evidence[i].id is in known_ids
         -> f"evidence id {id!r} does not exist; use only ids from the evidence list"
    2. every citation contains at least one document id (cited_doc_ids)
         -> f"citation {c!r} names no document id; copy citations exactly as listed"
       (an evidence id used as a citation, "[F1]", gets a message that names THAT mistake)
       and every document id in it is in retrieved_docs
         -> f"citation {c!r} cites {doc_id}, which was not retrieved; cite only listed documents"
    3. a fault claim (MACHINE_FAULT or SENSOR_FAULT) needs at least one evidence id
         -> "a fault claim needs at least one evidence id"
    4. SENSOR_FAULT needs a finding that points at the sensor: one with isolated=True, or of
       kind "moves_when_stopped" (findings are the anomaly report's dicts)
         -> "sensor_fault needs an isolated finding or a moves_when_stopped finding"
    5. (5.5b) a fault claim WITH evidence needs current machine data among it: at least one
       EXISTING id (in known_ids) of a DATA_IDS class: F (anomaly findings) or A (alarms).
       D (documents) and I (past incidents) explain, they don't prove; Q and N are context.
       Only when h.evidence is not empty: an empty list is rule 3's message already.
         -> f"a fault claim needs current machine data (an F or A id); documents and past
              incidents explain, they don't prove"                  (ONE line in the real message)
    """

    errors: list[str] = []

    # Rule 1: Validate evidence IDs
    for evidence in h.evidence:
        if evidence.id not in known_ids:
            errors.append(
                f"evidence id {evidence.id!r} does not exist; use only ids from the evidence list"
            )

    # Rule 2: Validate citations
    for c in h.citations:
        doc_ids = cited_doc_ids(c)

        if not doc_ids and c.strip("[] ") in known_ids:
            # 5.5b live run: the model put "[F1]", "[A1]", "[D4]" ... into citations, twice; the
            # generic message below didn't tell it WHAT it confused, so the retry repeated it.
            errors.append(
                f"citation {c!r} is an evidence id: put it in evidence; citations are the "
                "bracketed document references from the D lines, e.g. [MAN-CMP-001 §5.2]"
            )
        elif not doc_ids:
            errors.append(f"citation {c!r} names no document id; copy citations exactly as listed")

        for doc_id in sorted(doc_ids):
            if doc_id not in retrieved_docs:
                errors.append(
                    f"citation {c!r} cites {doc_id}, which was not retrieved; "
                    "cite only listed documents"
                )

    # Rule 3: Fault claims require evidence
    if h.category in (Category.MACHINE_FAULT, Category.SENSOR_FAULT) and not h.evidence:
        errors.append("a fault claim needs at least one evidence id")

    # Rule 4: Sensor fault requires supporting findings
    if h.category == Category.SENSOR_FAULT:
        has_sensor_evidence = any(
            f.get("isolated") or f.get("kind") == "moves_when_stopped" for f in findings
        )

        if not has_sensor_evidence:
            errors.append("sensor_fault needs an isolated finding or a moves_when_stopped finding")

    # Rule 5: Fault claims require current machine data (F or A)
    if h.category in (Category.MACHINE_FAULT, Category.SENSOR_FAULT) and h.evidence:
        # [:1], not [0]: an empty id ("") must not raise IndexError
        has_current_data = any(e.id in known_ids and e.id[:1] in DATA_IDS for e in h.evidence)

        if not has_current_data:
            errors.append(
                "a fault claim needs current machine data (an F or A id); "
                "documents and past incidents explain, they don't prove"
            )

    return errors
