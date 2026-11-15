"""Scenario format (8.1): every shipped scenario is valid, and every broken kind is caught.

No LLM, no database: the real plant.yaml and the real documents, read from disk.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_evals.scenario import (
    Scenario,
    ScenarioError,
    check_consistency,
    check_references,
    load_scenarios,
    parse_scenario,
)
from app.config import get_settings
from app.plant import PlantConfig, load_plant
from app.rag.corpus import load_corpus

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


@pytest.fixture(scope="module")
def doc_ids() -> frozenset[str]:
    return frozenset(d.meta.doc_id for d in load_corpus(get_settings().docs_dir))


def scenario(**over: Any) -> Scenario:
    """A valid machine-fault scenario; `setup` / `expect` entries override single keys."""
    raw: dict[str, Any] = {
        "id": "bearing-wear-m01",
        "description": "Spindle bearing wear on a CNC mill.",
        "setup": {"machine": "M-01", "fault": "bearing_wear"},
        "question": "What is wrong with M-01?",
        "expect": {
            "category": "machine_fault",
            "root_cause_keywords": [["bearing"]],
            "work_order": "required",
        },
    }
    raw["setup"] |= over.pop("setup", {})
    raw["expect"] |= over.pop("expect", {})
    return Scenario.model_validate(raw | over)


# ---------------------------------------------------------------- the shipped scenarios


def test_shipped_scenarios_are_valid(plant: PlantConfig, doc_ids: frozenset[str]) -> None:
    scenarios = load_scenarios(SCENARIOS, plant, doc_ids)
    assert len(scenarios) >= 3
    # the three kinds the suite must always have: a machine fault, a false alarm, a control
    kinds = {s.setup.fault is None or s.setup.fault == "sensor_drift" for s in scenarios}
    assert kinds == {True, False}
    assert any(s.setup.fault is None for s in scenarios)


# ---------------------------------------------------------------- layer 1: shape


def test_parses_durations_and_single_categories() -> None:
    s = scenario(setup={"duration": "1h30m"})
    assert s.setup.duration is not None and s.setup.duration.total_seconds() == 5400
    assert s.expect.category == ["machine_fault"]


def test_keywords_are_lowercased() -> None:
    s = scenario(expect={"root_cause_keywords": [[" Bearing "]]})
    assert s.expect.root_cause_keywords == [["bearing"]]


@pytest.mark.parametrize(
    "over",
    [
        {"expect": {"must_cites": ["MAN-CNC-001"]}},  # typo: unknown key, not ignored
        {"setup": {"duration": "3 days"}},
        {"setup": {"severity": 0}},
        {"expect": {"category": "broken_machine"}},
        {"expect": {"root_cause_keywords": [[]]}},  # an empty group would match anything
        {"expect": {"must_cite": ["MAN-CNC-001 §5.1"]}},  # document ids, not sections
        {"question": "x" * 201},
    ],
)
def test_rejects_bad_shapes(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        scenario(**over)


def test_file_name_must_match_id(tmp_path: Path) -> None:
    src = (SCENARIOS / "control-m10.yaml").read_text(encoding="utf-8")
    path = tmp_path / "renamed.yaml"
    path.write_text(src, encoding="utf-8")
    with pytest.raises(ValueError, match=r"control-m10\.yaml"):
        parse_scenario(path)


# ---------------------------------------------------------------- layer 2: consistency (TODO)


def test_a_consistent_scenario_has_no_errors() -> None:
    assert check_consistency(scenario()) == []


def test_control_may_not_expect_a_fault() -> None:
    s = scenario(setup={"fault": None}, expect={"category": ["no_fault", "machine_fault"]})
    assert check_consistency(s)


def test_control_may_not_require_a_work_order() -> None:
    s = scenario(setup={"fault": None}, expect={"category": "no_fault", "work_order": "required"})
    assert check_consistency(s)


def test_sensor_drift_must_expect_exactly_sensor_fault() -> None:
    drift = {"fault": "sensor_drift", "sensor": "spindle_bearing_temp"}
    assert check_consistency(scenario(setup=drift, expect={"category": "sensor_fault"})) == []
    both = scenario(setup=drift, expect={"category": ["sensor_fault", "machine_fault"]})
    assert check_consistency(both)


def test_machine_fault_must_be_expected_for_real_faults() -> None:
    assert check_consistency(scenario(expect={"category": "unknown"}))


def test_machine_fault_needs_keywords() -> None:
    assert check_consistency(scenario(expect={"root_cause_keywords": []}))


def test_sensor_only_for_sensor_drift() -> None:
    assert check_consistency(scenario(setup={"sensor": "spindle_bearing_temp"}))


# ---------------------------------------------------------------- layer 3: references


def test_fault_must_be_possible_on_the_machine(plant: PlantConfig, doc_ids: frozenset[str]) -> None:
    # M-07 is a conveyor: bearing wear is modelled on CNC mills and pumps only. (The first
    # draft of this format used exactly this example.)
    errors = check_references(scenario(setup={"machine": "M-07"}), plant, doc_ids)
    assert any("can't happen on M-07" in e for e in errors)


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"setup": {"machine": "M-99"}}, "unknown machine"),
        ({"setup": {"fault": "sensor_drift", "sensor": "nope"}}, "has no sensor nope"),
        ({"expect": {"must_cite": ["MAN-CNC-999"]}}, "don't exist"),
        ({"expect": {"tools": ["read_sim_schema"]}}, "unknown tools"),
    ],
)
def test_rejects_unknown_references(
    plant: PlantConfig, doc_ids: frozenset[str], over: dict[str, Any], message: str
) -> None:
    errors = check_references(scenario(**over), plant, doc_ids)
    assert any(message in e for e in errors), errors


def test_loader_reports_every_problem_at_once(
    tmp_path: Path, plant: PlantConfig, doc_ids: frozenset[str]
) -> None:
    (tmp_path / "a.yaml").write_text("id: a\n", encoding="utf-8")  # shape error
    (tmp_path / "b.yaml").write_text("{ not yaml", encoding="utf-8")  # syntax error
    with pytest.raises(ScenarioError) as e:
        load_scenarios(tmp_path, plant, doc_ids)
    assert "a.yaml" in str(e.value) and "b.yaml" in str(e.value)


def test_an_empty_directory_is_an_error(
    tmp_path: Path, plant: PlantConfig, doc_ids: frozenset[str]
) -> None:
    with pytest.raises(ScenarioError, match="no scenarios"):
        load_scenarios(tmp_path, plant, doc_ids)
