"""The generated scenarios (8.2) and what the whole suite covers.

No LLM, no database. The suite is the product here: these tests say what it must contain.
"""

from pathlib import Path

import pytest

from agent_evals.coverage import coverage_table, missing_cells
from agent_evals.generate import FAULT_KNOWLEDGE, PREFIX, render, write
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.config import get_settings
from app.db.models import FaultType, MachineType
from app.plant import PlantConfig, load_plant
from app.rag.corpus import load_corpus
from simulator.faults import APPLICABLE


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


@pytest.fixture(scope="module")
def suite(plant: PlantConfig) -> list[Scenario]:
    doc_ids = [d.meta.doc_id for d in load_corpus(get_settings().docs_dir)]
    return load_scenarios(SCENARIOS, plant, doc_ids)


# ---------------------------------------------------------------- the generator


def test_committed_files_are_what_the_generator_produces(plant: PlantConfig) -> None:
    expected = render(plant)
    on_disk = {p.name: p.read_text(encoding="utf-8") for p in SCENARIOS.glob(f"{PREFIX}*.yaml")}
    assert sorted(on_disk) == sorted(expected), "run: uv run python -m agent_evals.generate"
    changed = [name for name, text in expected.items() if on_disk[name] != text]
    assert changed == [], f"out of date, regenerate: {changed}"


def test_render_is_deterministic(plant: PlantConfig) -> None:
    assert render(plant) == render(plant)


def test_write_deletes_stale_generated_files_only(tmp_path: Path) -> None:
    (tmp_path / f"{PREFIX}old.yaml").write_text("x", encoding="utf-8")
    (tmp_path / "hand-written.yaml").write_text("x", encoding="utf-8")
    written, deleted = write(tmp_path, {f"{PREFIX}new.yaml": "y"})
    assert (written, deleted) == (1, 1)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{PREFIX}new.yaml", "hand-written.yaml"]


def test_every_real_fault_has_knowledge_for_every_machine_type_it_hits() -> None:
    for fault, types in APPLICABLE.items():
        if fault is FaultType.SENSOR_DRIFT:
            continue  # its own table (DRIFT_SENSORS)
        assert set(FAULT_KNOWLEDGE[fault].cite) == types, fault


@pytest.mark.parametrize("fault", sorted(FAULT_KNOWLEDGE))
def test_question_templates_are_varied(fault: FaultType) -> None:
    """The generator's blind spot is one phrasing repeated: >= 3 distinct templates per fault."""
    questions = FAULT_KNOWLEDGE[fault].questions
    assert len(questions) >= 3, f"{fault}: write at least 3 question templates"
    assert len(set(questions)) == len(questions), f"{fault}: duplicate templates"
    assert all("{machine}" in q for q in questions), f"{fault}: every template needs {{machine}}"


# ---------------------------------------------------------------- the whole suite


def test_suite_size(suite: list[Scenario]) -> None:
    assert 40 <= len(suite) <= 60


def test_every_fault_and_machine_type_is_covered(suite: list[Scenario], plant: PlantConfig) -> None:
    assert missing_cells(suite, plant) == []


def test_every_machine_type_has_a_control(suite: list[Scenario], plant: PlantConfig) -> None:
    types = {m.code: m.type for m in plant.machines}
    controlled = {types[s.setup.machine] for s in suite if s.setup.fault is None}
    assert controlled == set(MachineType)


def test_smoke_set_covers_every_fault_and_a_control(suite: list[Scenario]) -> None:
    smoke = [s for s in suite if "smoke" in s.tags]
    assert {s.setup.fault for s in smoke} == {*FaultType, None}
    # it runs on every PR against a ~50 requests/day free quota (~4 requests per scenario)
    assert len(smoke) <= 8


def test_coverage_table_lists_every_scenario(suite: list[Scenario], plant: PlantConfig) -> None:
    table = coverage_table(suite, plant)
    assert f"{len(suite)} scenarios" in table
    assert "| bearing_wear | pump |" in table
