"""Each fault must leave its own physical signature, distinguishable from the others.

Method: simulate the same machine/window/seed twice, healthy and faulted, and compare.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.config import get_settings
from app.db.models import FaultType
from app.plant import MachineSpec, PlantConfig, SensorSpec, load_plant
from simulator.faults import Fault
from simulator.signals import MachineSimulation, OpState, simulate_machine, utc

SEED = 42
MONDAY = utc(2026, 11, 16)  # Berlin = UTC+1; shifts run 05:00-21:00 UTC


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


def _machine(plant: PlantConfig, code: str) -> tuple[MachineSpec, list[SensorSpec]]:
    m = next(m for m in plant.machines if m.code == code)
    return m, plant.sensors_for(m)


def _pair(
    plant: PlantConfig, fault: Fault, start: datetime, end: datetime
) -> tuple[MachineSimulation, MachineSimulation, dict[str, SensorSpec]]:
    m, sensors = _machine(plant, fault.machine_code)
    healthy = simulate_machine(m, sensors, start, end, SEED)
    faulty = simulate_machine(m, sensors, start, end, SEED, faults=[fault])
    return healthy, faulty, {s.name: s for s in sensors}


def _first_above(values: np.ndarray, limit: float, mask: np.ndarray) -> int | None:
    idx = np.flatnonzero((values > limit) & mask)
    return int(idx[0]) if len(idx) else None


def test_no_effect_before_the_fault_starts(plant: PlantConfig) -> None:
    fault = Fault(
        FaultType.BEARING_WEAR, "M-01", MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)
    )
    healthy, faulty, _ = _pair(plant, fault, MONDAY, MONDAY + timedelta(days=2))
    before = healthy.times < np.datetime64(fault.start.replace(tzinfo=None))
    for name in healthy.values:
        np.testing.assert_array_equal(healthy.values[name][before], faulty.values[name][before])


def test_bearing_wear_vibration_rises_before_temperature(plant: PlantConfig) -> None:
    start, full = MONDAY, MONDAY + timedelta(days=2, hours=18)  # fully worn Wed 19:00 Berlin
    fault = Fault(FaultType.BEARING_WEAR, "M-01", start, full)
    _, faulty, specs = _pair(plant, fault, start, full + timedelta(hours=1))
    running = faulty.states == OpState.RUNNING
    vib = _first_above(
        faulty.values["spindle_vibration"], specs["spindle_vibration"].normal_max, running
    )
    temp = _first_above(
        faulty.values["spindle_bearing_temp"], specs["spindle_bearing_temp"].normal_max, running
    )
    assert vib is not None and temp is not None, "both should eventually exceed their limits"
    assert vib < temp, "the classic order: vibration first, heat only in the late stage"
    assert faulty.times[temp] - faulty.times[vib] > np.timedelta64(12, "h")


def test_coolant_failure_drops_pressure_heats_spindle_and_trips(plant: PlantConfig) -> None:
    start = MONDAY + timedelta(hours=9)  # 10:00 Berlin, mid-shift
    fault = Fault(FaultType.COOLANT_PUMP_FAILURE, "M-01", start, start + timedelta(minutes=30))
    _, faulty, specs = _pair(plant, fault, start - timedelta(hours=1), start + timedelta(hours=2))

    def at(delta: timedelta) -> int:
        """Index of the sample `delta` after the fault started."""
        when = np.datetime64((start + delta).replace(tzinfo=None))
        return int(np.searchsorted(faulty.times, when))

    assert faulty.values["coolant_pressure"][at(timedelta(minutes=2))] < 1.0  # pump dead
    temp = faulty.values["spindle_bearing_temp"]
    assert temp[at(timedelta(minutes=29))] > specs["spindle_bearing_temp"].normal_max  # overheated
    assert np.all(faulty.states[at(timedelta(minutes=30)) :] == OpState.DOWN)  # interlock tripped
    assert temp[-1] < temp[at(timedelta(minutes=30))]  # stopped machine cools down again


def test_clogged_filter_pressure_rises_gradually(plant: PlantConfig) -> None:
    start, full = MONDAY, MONDAY + timedelta(days=2)
    fault = Fault(FaultType.CLOGGED_FILTER, "M-08", start, full)
    healthy, faulty, specs = _pair(plant, fault, start, full)
    dp, limit = faulty.values["inlet_filter_dp"], specs["inlet_filter_dp"].normal_max
    first = len(dp) // 10  # first 10 % of the clogging: still within limits
    assert dp[:first].max() <= limit
    assert np.mean(dp[-360:]) > limit  # last hour: clearly too high
    # The compressor works harder: more current than the healthy twin.
    assert (
        faulty.values["motor_current"][-360:].mean() > healthy.values["motor_current"][-360:].mean()
    )


def test_motor_overload_breaks_current_speed_correlation(plant: PlantConfig) -> None:
    start = MONDAY + timedelta(hours=8)
    fault = Fault(FaultType.MOTOR_OVERLOAD, "M-05", start, start + timedelta(hours=2))
    healthy, faulty, specs = _pair(plant, fault, start, start + timedelta(hours=2))
    running = faulty.states == OpState.RUNNING
    cur, rpm = "pump_motor_current", "screw_speed"
    assert faulty.values[cur][running].max() > specs[cur].normal_max
    healthy_corr = np.corrcoef(healthy.values[cur][running], healthy.values[rpm][running])[0, 1]
    faulty_corr = np.corrcoef(faulty.values[cur][running], faulty.values[rpm][running])[0, 1]
    assert healthy_corr > 0.8
    assert faulty_corr < 0.3  # current up while speed sags: the relationship is gone


def test_sensor_drift_changes_one_sensor_and_nothing_else(plant: PlantConfig) -> None:
    """The false-alarm case: only the measurement is wrong, the machine is perfectly healthy."""
    start, full = MONDAY, MONDAY + timedelta(days=2)
    fault = Fault(FaultType.SENSOR_DRIFT, "M-08", start, full, sensor="discharge_temp")
    healthy, faulty, specs = _pair(plant, fault, start, full)
    np.testing.assert_array_equal(healthy.states, faulty.states)
    for name in healthy.values:
        if name != "discharge_temp":
            np.testing.assert_array_equal(healthy.values[name], faulty.values[name])
    assert faulty.values["discharge_temp"][-360:].mean() > specs["discharge_temp"].normal_max


def test_belt_slippage_makes_roller_speed_oscillate(plant: PlantConfig) -> None:
    start = MONDAY + timedelta(hours=8)
    fault = Fault(FaultType.BELT_SLIPPAGE, "M-04", start, start + timedelta(hours=4))
    healthy, faulty, specs = _pair(plant, fault, start, start + timedelta(hours=4))
    last_hour = slice(-360, None)
    running = (faulty.states == OpState.RUNNING)[last_hour]
    speed = "drive_roller_speed"
    assert (
        faulty.values[speed][last_hour][running].std()
        > 3 * healthy.values[speed][last_hour][running].std()
    )
    assert faulty.values[speed][last_hour][running].min() < specs[speed].normal_min


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        (
            Fault(FaultType.COOLANT_PUMP_FAILURE, "M-04", MONDAY, MONDAY + timedelta(hours=1)),
            "can't happen",
        ),
        (
            Fault(FaultType.SENSOR_DRIFT, "M-08", MONDAY, MONDAY + timedelta(hours=1)),
            "needs --sensor",
        ),
        (Fault(FaultType.BEARING_WEAR, "M-01", MONDAY, MONDAY), "after start"),
    ],
)
def test_invalid_faults_are_rejected(plant: PlantConfig, fault: Fault, message: str) -> None:
    m, sensors = _machine(plant, fault.machine_code)
    with pytest.raises(ValueError, match=message):
        simulate_machine(m, sensors, MONDAY, MONDAY + timedelta(hours=1), SEED, faults=[fault])


def test_cleared_fault_stops_affecting_the_machine(plant: PlantConfig) -> None:
    """After a repair: effects stop and a tripped machine runs again at the next shift."""
    start = MONDAY + timedelta(hours=9)
    repaired = start + timedelta(hours=2)
    fault = Fault(
        FaultType.COOLANT_PUMP_FAILURE,
        "M-01",
        start,
        start + timedelta(minutes=30),
        cleared_at=repaired,
    )
    healthy, faulty, _ = _pair(plant, fault, start, MONDAY + timedelta(days=1, hours=12))
    after = faulty.times >= np.datetime64(repaired.replace(tzinfo=None))
    np.testing.assert_array_equal(healthy.states[after], faulty.states[after])  # runs again
    # Pressure rebuilds through its 30 s lag (inertia, like real hydraulics): equal once settled.
    settled = faulty.times >= np.datetime64((repaired + timedelta(minutes=10)).replace(tzinfo=None))
    np.testing.assert_allclose(
        healthy.values["coolant_pressure"][settled], faulty.values["coolant_pressure"][settled]
    )
