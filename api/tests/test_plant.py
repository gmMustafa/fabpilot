"""Unit tests for data/plant.yaml: no database needed, these run everywhere."""

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.db.models import MachineType, SensorKind
from app.plant import PlantConfig, SensorSpec, load_plant

# Which sensor kinds each fault from the brief needs to be observable, and on which machine types.
# The simulator (1.4) will own this mapping; until then this test guards the plant layout.
FAULT_REQUIREMENTS: dict[str, tuple[set[MachineType], set[SensorKind]]] = {
    "bearing_wear": (
        {MachineType.CNC_MILL, MachineType.PUMP},
        {SensorKind.VIBRATION, SensorKind.TEMPERATURE},
    ),
    "coolant_pump_failure": (
        {MachineType.CNC_MILL},
        {SensorKind.TEMPERATURE, SensorKind.PRESSURE},
    ),
    "clogged_filter": ({MachineType.COMPRESSOR, MachineType.PUMP}, {SensorKind.PRESSURE}),
    "motor_overload": (
        {MachineType.INJECTION_MOLDER, MachineType.CNC_MILL},
        {SensorKind.CURRENT, SensorKind.RPM},
    ),
    # Sensor drift is a false alarm: needs other sensors on the same machine that stay normal.
    "sensor_drift": (set(MachineType), set()),
    "belt_slippage": ({MachineType.CONVEYOR}, {SensorKind.RPM, SensorKind.CURRENT}),
}


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


def test_plant_file_loads(plant: PlantConfig) -> None:
    assert len(plant.machines) == 10
    assert {m.line for m in plant.machines} == {"Line 1", "Line 2", "Line 3"}


@pytest.mark.parametrize("fault", FAULT_REQUIREMENTS)
def test_every_fault_is_observable(plant: PlantConfig, fault: str) -> None:
    machine_types, needed_kinds = FAULT_REQUIREMENTS[fault]
    candidates = [m for m in plant.machines if m.type in machine_types]
    assert candidates, f"{fault}: no machine of types {machine_types}"
    for m in candidates:
        kinds = {s.kind for s in plant.sensors_for(m)}
        assert needed_kinds <= kinds, f"{fault} on {m.code}: missing {needed_kinds - kinds}"
        assert len(kinds) >= 2, f"{m.code}: too few sensor kinds to tell faults apart"


def test_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="normal_min must be < normal_max"):
        SensorSpec(name="x", kind=SensorKind.RPM, unit="rpm", normal_min=10, normal_max=5)


def test_rejects_typo_in_key() -> None:
    with pytest.raises(ValidationError, match="normal_mx"):
        SensorSpec.model_validate(
            {"name": "x", "kind": "rpm", "unit": "rpm", "normal_min": 1, "normal_mx": 5}
        )


def test_rejects_duplicate_machine_codes(plant: PlantConfig) -> None:
    data = plant.model_dump()
    data["machines"].append(data["machines"][0])
    with pytest.raises(ValidationError, match="duplicate machine codes"):
        PlantConfig.model_validate(data)
