"""Docs-as-code: the knowledge base must agree with the plant and the simulator's alarm logic.

Lives in the simulator package because it checks docs against BOTH app (plant.yaml) and simulator
(alarm rules, fault applicability); api can't import simulator. If someone changes a normal range
in plant.yaml or an alarm rule, this fails until the manuals are updated: the agent must never
read a manual that contradicts the data it is looking at.
"""

import re

import pytest

from app.config import get_settings
from app.plant import PlantConfig, SensorSpec, load_plant
from app.rag.corpus import Document, SourceType, load_corpus
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
