"""Alarm generation: the debounce/hysteresis core (your TODO), then whole-plant behaviour."""

from datetime import timedelta

import numpy as np
import pytest

from app.config import get_settings
from app.db.models import FaultType
from app.plant import PlantConfig, load_plant
from simulator.alarms import alarm_episodes, detect_alarms
from simulator.faults import Fault
from simulator.signals import simulate_machine, utc

SEED = 42
MONDAY = utc(2026, 11, 16)


def ep(values: list[float], raise_above: float = 10, clear_below: float = 8, n: int = 3):  # type: ignore[no-untyped-def]
    return alarm_episodes(np.array(values, dtype=float), raise_above, clear_below, n)


# ------------------------------------------------------------------ alarm_episodes (your TODO)


def test_episodes_empty_and_quiet_series() -> None:
    assert ep([]) == []
    assert ep([5, 6, 9.9, 7]) == []


def test_episodes_short_spike_is_debounced() -> None:
    assert ep([5, 11, 12, 5, 11, 5]) == []  # never 3 in a row


def test_episodes_raised_on_the_sample_that_completes_the_debounce() -> None:
    #           0  1   2   3   4  5
    assert ep([5, 11, 12, 13, 5, 5]) == [(3, 4)]  # 3rd consecutive high sample = index 3


def test_episodes_hysteresis_keeps_alarm_active_between_levels() -> None:
    # After raising, 9 is below the raise level but above the clear level: still in alarm.
    #           0   1   2  3  4  5    6
    assert ep([11, 11, 11, 9, 9, 7.9, 9]) == [(2, 5)]


def test_episodes_strict_comparisons() -> None:
    assert ep([10, 10, 10, 10]) == []  # == raise level is not "above"
    assert ep([11, 11, 11, 8, 8, 7]) == [(2, 5)]  # == clear level does not clear


def test_episodes_re_raise_needs_a_fresh_debounce() -> None:
    #           0   1   2  3  4   5   6   7
    assert ep([11, 11, 11, 7, 11, 11, 11, 7]) == [(2, 3), (6, 7)]


def test_episodes_still_active_at_end() -> None:
    assert ep([5, 11, 11, 11, 11]) == [(3, 5)]  # cleared_idx == len(values)


def test_episodes_min_samples_one_raises_immediately() -> None:
    assert ep([5, 11, 5], n=1) == [(1, 2)]


@pytest.mark.parametrize(("raise_above", "clear_below", "n"), [(10, 12, 3), (10, 8, 0)])
def test_episodes_reject_invalid_arguments(raise_above: float, clear_below: float, n: int) -> None:
    with pytest.raises(ValueError):
        alarm_episodes(np.array([1.0]), raise_above, clear_below, n)


# ------------------------------------------------------------------ whole plant


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


def test_a_healthy_week_raises_no_alarms(plant: PlantConfig) -> None:
    """Decision D2: healthy data must be silent, or real alarms drown in noise."""
    for m in plant.machines:
        sensors = plant.sensors_for(m)
        sim = simulate_machine(m, sensors, MONDAY, MONDAY + timedelta(days=7), SEED)
        alarms = detect_alarms(m.code, sensors, sim)
        assert alarms == [], f"{m.code}: {[(a.code, a.sensor_name, a.message) for a in alarms[:5]]}"


@pytest.mark.parametrize(
    ("fault", "hours", "expected_codes"),
    [
        (
            Fault(FaultType.BEARING_WEAR, "M-01", MONDAY, MONDAY + timedelta(days=2, hours=18)),
            67,
            {"VIB-HIGH", "TEMP-HIGH"},
        ),
        (
            Fault(
                FaultType.COOLANT_PUMP_FAILURE,
                "M-01",
                MONDAY + timedelta(hours=9),
                MONDAY + timedelta(hours=9, minutes=30),
            ),
            12,
            {"PRESS-LOW", "TEMP-HIGH", "TEMP-CRIT", "TRIP"},
        ),
        (
            Fault(FaultType.CLOGGED_FILTER, "M-08", MONDAY, MONDAY + timedelta(days=2)),
            48,
            {"PRESS-HIGH"},
        ),
        (
            Fault(
                FaultType.MOTOR_OVERLOAD,
                "M-05",
                MONDAY + timedelta(hours=8),
                MONDAY + timedelta(hours=10),
            ),
            11,
            {"CURR-HIGH", "TRIP"},
        ),
        (
            Fault(
                FaultType.BELT_SLIPPAGE,
                "M-04",
                MONDAY + timedelta(hours=8),
                MONDAY + timedelta(hours=12),
            ),
            12,
            {"RPM-LOW"},
        ),
    ],
    ids=lambda v: v.type.value if isinstance(v, Fault) else None,
)
def test_each_fault_raises_its_alarms(
    plant: PlantConfig, fault: Fault, hours: int, expected_codes: set[str]
) -> None:
    m = next(m for m in plant.machines if m.code == fault.machine_code)
    sensors = plant.sensors_for(m)
    sim = simulate_machine(
        m, sensors, MONDAY, MONDAY + timedelta(hours=hours), SEED, faults=[fault]
    )
    codes = {a.code for a in detect_alarms(m.code, sensors, sim)}
    assert expected_codes <= codes, f"missing {expected_codes - codes}, got {codes}"


def test_bearing_wear_alarms_in_the_right_order(plant: PlantConfig) -> None:
    fault = Fault(FaultType.BEARING_WEAR, "M-01", MONDAY, MONDAY + timedelta(days=2, hours=18))
    m = next(m for m in plant.machines if m.code == "M-01")
    sensors = plant.sensors_for(m)
    sim = simulate_machine(m, sensors, MONDAY, MONDAY + timedelta(hours=67), SEED, faults=[fault])
    alarms = detect_alarms(m.code, sensors, sim)
    first = {
        code: min(a.time for a in alarms if a.code == code) for code in ("VIB-HIGH", "TEMP-HIGH")
    }
    assert first["VIB-HIGH"] < first["TEMP-HIGH"]


def test_sensor_drift_alarms_only_on_the_drifting_sensor(plant: PlantConfig) -> None:
    """The false-alarm scenario: alarms exist, but every one points at a single sensor."""
    fault = Fault(
        FaultType.SENSOR_DRIFT, "M-08", MONDAY, MONDAY + timedelta(days=2), sensor="discharge_temp"
    )
    m = next(m for m in plant.machines if m.code == "M-08")
    sensors = plant.sensors_for(m)
    sim = simulate_machine(m, sensors, MONDAY, MONDAY + timedelta(days=2), SEED, faults=[fault])
    alarms = detect_alarms(m.code, sensors, sim)
    assert alarms, "drift should eventually trip the limit"
    assert {a.sensor_name for a in alarms} == {"discharge_temp"}
    assert "TRIP" not in {a.code for a in alarms}  # the machine itself is fine
