"""Unit tests for the healthy-signal model. Pure numpy, no database."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.config import get_settings
from app.db.models import MachineType, SensorKind
from app.plant import MachineSpec, PlantConfig, load_plant
from simulator.signals import (
    DT_SECONDS,
    OpState,
    first_order_lag,
    shift_mask,
    simulate_machine,
    time_grid,
    utc,
)

MONDAY = utc(2026, 11, 16)  # a Monday; Berlin is UTC+1 in November
SEED = 42


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


def _first_of_type(plant: PlantConfig, mtype: MachineType) -> MachineSpec:
    return next(m for m in plant.machines if m.type == mtype)


# ------------------------------------------------------------------ first_order_lag (your TODO)


def test_lag_constant_target_stays_constant() -> None:
    out = first_order_lag(np.full(50, 5.0), initial=5.0, dt=1.0, tau=10.0)
    np.testing.assert_allclose(out, 5.0)


def test_lag_step_response_is_63_percent_after_one_tau() -> None:
    out = first_order_lag(np.ones(200), initial=0.0, dt=1.0, tau=50.0)
    # Continuous system: 1 - 1/e = 0.632. Discrete steps land very close to it.
    assert out[49] == pytest.approx(1 - np.exp(-1), abs=0.01)
    assert np.all(np.diff(out) > 0)  # monotonic rise, never jumps past the target
    assert out[-1] < 1.0


def test_lag_returns_new_array_and_keeps_shape() -> None:
    targets = np.linspace(0, 10, 30)
    original = targets.copy()
    out = first_order_lag(targets, initial=0.0, dt=1.0, tau=5.0)
    assert out.shape == targets.shape
    assert out is not targets
    np.testing.assert_array_equal(targets, original)  # input untouched


def test_lag_never_overshoots_when_dt_exceeds_tau() -> None:
    """Naive Euler with dt/tau > 1 overshoots and oscillates: unphysical (and unstable).

    Tests the PROPERTY (no overshoot, nearly settled) rather than one implementation's exact
    numbers, so both a clamped Euler step and the exact 1 - exp(-dt/tau) step pass.
    """
    targets = np.array([10.0, 10.0, 0.0, 0.0, 10.0])
    out = first_order_lag(targets, initial=0.0, dt=10.0, tau=2.0)
    previous = np.concatenate([[0.0], out[:-1]])
    low, high = np.minimum(previous, targets), np.maximum(previous, targets)
    assert np.all((out >= low) & (out <= high))  # each step lands between where it was and target
    np.testing.assert_allclose(out, targets, atol=0.1)  # 5 time constants per step: ~settled


# ------------------------------------------------------------------ schedule


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (utc(2026, 11, 16, 9), True),  # Mon 10:00 Berlin
        (utc(2026, 11, 16, 4), False),  # Mon 05:00 Berlin, before first shift
        (utc(2026, 11, 16, 21), False),  # Mon 22:00 Berlin, after second shift
        (utc(2026, 11, 15, 12), False),  # Sunday
        (utc(2026, 7, 6, 4), True),  # Mon 06:00 Berlin in summer (UTC+2): DST handled
    ],
)
def test_shift_mask_uses_local_time(when: datetime, expected: bool) -> None:
    epoch = time_grid(when, when + timedelta(seconds=DT_SECONDS))
    assert bool(shift_mask(epoch)[0]) is expected


# ------------------------------------------------------------------ whole-machine behaviour


def _seconds_since(event: np.ndarray) -> np.ndarray:
    """For each sample: seconds since `event` was last True (huge if never)."""
    idx = np.arange(len(event))
    last = np.maximum.accumulate(np.where(event, idx, -(10**9)))
    return (idx - last) * DT_SECONDS


def test_same_seed_gives_identical_data(plant: PlantConfig) -> None:
    m = plant.machines[0]
    a = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(hours=6), SEED)
    b = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(hours=6), SEED)
    c = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(hours=6), SEED + 1)
    for name in a.values:
        np.testing.assert_array_equal(a.values[name], b.values[name])
    assert not np.array_equal(a.values["spindle_speed"], c.values["spindle_speed"])


@pytest.mark.parametrize("mtype", list(MachineType))
def test_steady_running_values_stay_in_normal_range(plant: PlantConfig, mtype: MachineType) -> None:
    """The core promise of 'healthy': while running and warmed up, readings sit in the band."""
    m = _first_of_type(plant, mtype)
    sim = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(days=2), SEED)
    running = sim.states == OpState.RUNNING
    # Exclude warm-up (1 h after the shift starts) and 2 min after any short stop: a real
    # anomaly detector has to know about these too, or every morning starts with false alarms.
    steady = (
        running
        & (_seconds_since(sim.states == OpState.OFF) >= 3600)
        & (_seconds_since(~running) >= 120)
    )
    assert steady.sum() > 1000, "test window should contain plenty of steady running"
    for spec in plant.sensors_for(m):
        v = sim.values[spec.name][steady]
        inside = np.mean((v >= spec.normal_min) & (v <= spec.normal_max))
        assert inside >= 0.99, f"{m.code}.{spec.name}: only {inside:.1%} in normal range"


def test_temperatures_cool_to_ambient_overnight(plant: PlantConfig) -> None:
    m = _first_of_type(plant, MachineType.CNC_MILL)
    sim = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(days=2), SEED)
    night = np.searchsorted(sim.times, np.datetime64("2026-11-17T02:00:00"))  # Tue 03:00 Berlin
    assert sim.states[night] == OpState.OFF
    temp = sim.values["spindle_bearing_temp"][night]
    assert 17.0 < temp < 24.0  # hall air at night, below the running band (25-55 °C)


def test_current_tracks_speed_while_running(plant: PlantConfig) -> None:
    """Healthy machines keep their signals correlated; faults (1.4) will break this."""
    m = _first_of_type(plant, MachineType.CNC_MILL)
    sim = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(days=1), SEED)
    running = sim.states == OpState.RUNNING
    corr = np.corrcoef(
        sim.values["spindle_motor_current"][running], sim.values["spindle_speed"][running]
    )[0, 1]
    assert corr > 0.8


def test_utilities_run_around_the_clock(plant: PlantConfig) -> None:
    m = _first_of_type(plant, MachineType.COMPRESSOR)
    sim = simulate_machine(m, plant.sensors_for(m), MONDAY, MONDAY + timedelta(days=1), SEED)
    assert np.all(sim.states == OpState.RUNNING)
    kinds = {s.kind for s in plant.sensors_for(m)}
    assert SensorKind.PRESSURE in kinds


def test_rejects_naive_datetimes(plant: PlantConfig) -> None:
    m = plant.machines[0]
    with pytest.raises(ValueError, match="timezone-aware"):
        simulate_machine(m, plant.sensors_for(m), MONDAY.replace(tzinfo=None), MONDAY, SEED)


def test_plant_behaviour_does_not_depend_on_the_window(plant: PlantConfig) -> None:
    """A 10-second live tick must see the same jobs/stops as a 7-day backfill of that moment."""
    m = _first_of_type(plant, MachineType.CNC_MILL)
    day_start = MONDAY + timedelta(hours=8)  # inside a shift
    long = simulate_machine(
        m, plant.sensors_for(m), day_start, day_start + timedelta(hours=6), SEED
    )
    tick_start = day_start + timedelta(hours=3, minutes=17, seconds=40)
    tick = simulate_machine(
        m, plant.sensors_for(m), tick_start, tick_start + timedelta(seconds=10), SEED
    )
    i = int(np.searchsorted(long.times, tick.times[0]))
    assert tick.states[0] == long.states[i]
    assert tick.load[0] == long.load[i]


def test_initial_value_continues_a_lagged_signal(plant: PlantConfig) -> None:
    """Live mode passes the last stored reading so temperatures don't jump at the boundary."""
    m = _first_of_type(plant, MachineType.CNC_MILL)
    start = MONDAY + timedelta(hours=10)
    sim = simulate_machine(
        m,
        plant.sensors_for(m),
        start,
        start + timedelta(seconds=10),
        SEED,
        initial={"spindle_bearing_temp": 30.0},
    )
    assert abs(sim.values["spindle_bearing_temp"][0] - 30.0) < 1.0  # one 10 s step of a 900 s lag
