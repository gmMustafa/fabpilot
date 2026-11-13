"""Docs-as-code: the knowledge base must agree with the plant and the simulator's alarm logic.

Lives in the simulator package because it checks docs against BOTH app (plant.yaml) and simulator
(alarm rules, fault applicability); api can't import simulator. If someone changes a normal range
in plant.yaml or an alarm rule, this fails until the manuals are updated: the agent must never
read a manual that contradicts the data it is looking at.
"""

import re
from collections import Counter
from datetime import date

import pytest

from app.config import get_settings
from app.db.models import FaultType, SourceType
from app.plant import PlantConfig, SensorSpec, load_plant
from app.rag.corpus import Document, load_corpus
from simulator.alarms import Direction, rules_for
from simulator.faults import APPLICABLE

ALARM_CODE = re.compile(r"\b(?:TEMP|VIB|PRESS|CURR|RPM)-[A-Z]+\b|\bTRIP\b")
SIGNAL_ROW = re.compile(
    r"^\| `(?P<name>[a-z0-9_]+)` \| (?P<unit>[^|]+?) \| (?P<min>[\d.]+) \| (?P<max>[\d.]+) "
    r"\| (?P<crit>[\d.]+) \| (?P<low>[\d.]+|—) \|$",
    re.MULTILINE,
)
NO_LOW_ALARM = "—"  # the em dash in the table's "Low alarm below" column


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


@pytest.fixture(scope="module")
def docs() -> list[Document]:
    return load_corpus(get_settings().docs_dir)


def _sensors(plant: PlantConfig, doc: Document) -> list[SensorSpec]:
    return [s for t in doc.meta.machine_types for s in plant.sensor_templates[t]]


def _section(body: str, number: int) -> str:
    match = re.search(rf"^## {number}\. .*?(?=^## \d+\. |\Z)", body, re.MULTILINE | re.DOTALL)
    assert match, f"section {number} missing"
    return match.group(0)


def _manuals(docs: list[Document]) -> list[Document]:
    return [d for d in docs if d.meta.source_type is SourceType.MANUAL]


def test_manual_signal_tables_match_plant_and_alarm_rules(
    plant: PlantConfig, docs: list[Document]
) -> None:
    for doc in _manuals(docs):
        rows = {m["name"]: m for m in SIGNAL_ROW.finditer(_section(doc.body, 3))}
        sensors = _sensors(plant, doc)
        assert set(rows) == {s.name for s in sensors}, doc.meta.doc_id  # every sensor, no extras
        for s in sensors:
            row, rules = rows[s.name], {r.code.split("-")[1]: r for r in rules_for(s)}
            where = f"{doc.meta.doc_id} {s.name}"
            assert row["unit"] == s.unit, where
            assert float(row["min"]) == s.normal_min, where
            assert float(row["max"]) == s.normal_max, where
            assert float(row["crit"]) == pytest.approx(rules["CRIT"].raise_at), where
            if "LOW" in rules:
                assert rules["LOW"].direction is Direction.LOW
                assert float(row["low"]) == rules["LOW"].raise_at, where
            else:
                assert row["low"] == NO_LOW_ALARM, where


def test_alarm_codes_in_docs_exist_for_their_machine_types(
    plant: PlantConfig, docs: list[Document]
) -> None:
    for doc in docs:
        possible = {r.code for s in _sensors(plant, doc) for r in rules_for(s)} | {"TRIP"}
        mentioned = set(ALARM_CODE.findall(doc.body))
        assert mentioned <= possible, f"{doc.meta.doc_id}: {sorted(mentioned - possible)}"


def test_manuals_only_name_their_own_sensors(plant: PlantConfig, docs: list[Document]) -> None:
    all_names = {s.name for specs in plant.sensor_templates.values() for s in specs}
    for doc in _manuals(docs):
        own = {s.name for s in _sensors(plant, doc)}
        named = set(re.findall(r"`([a-z0-9_]+)`", doc.body)) & all_names
        assert named <= own, f"{doc.meta.doc_id} names foreign sensors: {sorted(named - own)}"


SOP_SECTIONS = ["Scope", "Safety", "Tools and parts", "Procedure", "Acceptance criteria", "Record"]


def test_sops_have_the_required_sections(docs: list[Document]) -> None:
    """Work orders (5.2) are built from these: parts, steps and acceptance criteria must exist."""
    for doc in docs:
        if doc.meta.source_type is SourceType.SOP:
            titles = re.findall(r"^## \d+\. (.+)$", doc.body, re.MULTILINE)
            assert titles == SOP_SECTIONS, doc.meta.doc_id


# Any document ID written in a body: MAN-PMP-001, SOP-GEN-004, INC-2025-014.
DOC_REF = re.compile(r"\b(?:MAN-[A-Z]{3}|SOP-[A-Z]{3}|INC-\d{4})-\d{3}\b")


def test_all_doc_references_resolve(docs: list[Document]) -> None:
    """A document may only cite documents that exist: an agent following a reference to a missing
    SOP would cite (or invent) something nobody can check. Same idea as citation validity (8.3).
    """
    # Collect all existing document IDs
    known = {doc.meta.doc_id for doc in docs}

    # Collect all broken references
    broken = {}

    for doc in docs:
        # Find all referenced document IDs
        referenced = set(DOC_REF.findall(doc.body))

        # Find references that do not exist
        missing = referenced - known

        if missing:
            broken[doc.meta.doc_id] = sorted(missing)

    # Report all broken references together
    assert not broken, f"references to missing documents: {broken}"


INCIDENT_SECTIONS = [
    "Summary",
    "Timeline",
    "Evidence",
    "Root cause",
    "Corrective actions",
    "Lessons learned",
]
# Simulated data (backfill, live, eval scenarios) starts after this date. Past incidents must end
# before it, so an eval question can never find "its own" incident report (no leakage).
SIMULATION_STARTS = date(2026, 10, 1)


def _incidents(docs: list[Document]) -> list[Document]:
    return [d for d in docs if d.meta.source_type is SourceType.INCIDENT_REPORT]


def test_incidents_have_the_required_sections(docs: list[Document]) -> None:
    for doc in _incidents(docs):
        titles = re.findall(r"^## \d+\. (.+)$", doc.body, re.MULTILINE)
        assert titles == INCIDENT_SECTIONS, doc.meta.doc_id


def test_incidents_predate_the_simulated_period(docs: list[Document]) -> None:
    late = [d.meta.doc_id for d in _incidents(docs) if d.meta.effective_date >= SIMULATION_STARTS]
    assert not late, f"incidents inside the simulated period (eval leakage risk): {late}"


def test_incident_ids_match_their_year(docs: list[Document]) -> None:
    for doc in _incidents(docs):
        assert doc.meta.doc_id[4:8] == str(doc.meta.effective_date.year), doc.meta.doc_id


def test_incident_labels_are_consistent_and_balanced(docs: list[Document]) -> None:
    """Labels are ground truth for retrieval evals (3.4) and few-shot analogies: they must agree
    with each other, and every case the agent must handle needs at least two examples."""
    incidents = [d.meta for d in _incidents(docs)]
    for m in incidents:
        if m.fault is FaultType.SENSOR_DRIFT:
            assert m.false_alarm, f"{m.doc_id}: sensor drift is by definition a false alarm"
        if m.false_alarm:
            assert m.fault in (FaultType.SENSOR_DRIFT, None), f"{m.doc_id}: false alarm + {m.fault}"

    counts = Counter(m.fault for m in incidents)
    assert all(counts[f] >= 2 for f in FaultType), counts
    assert counts[None] >= 4, "need causes the simulator can't produce (retrieval distractors)"
    assert sum(1 for m in incidents if m.false_alarm) >= 4


def test_incidents_match_the_plant(plant: PlantConfig, docs: list[Document]) -> None:
    machines = {m.code: m for m in plant.machines}
    for doc in docs:
        meta = doc.meta
        if meta.source_type is not SourceType.INCIDENT_REPORT:
            continue
        assert meta.machine in machines, meta.doc_id
        mtype = machines[meta.machine].type
        assert meta.machine_types == [mtype], meta.doc_id
        if meta.fault is not None:
            assert mtype in APPLICABLE[meta.fault], f"{meta.doc_id}: {meta.fault} on {mtype}"
