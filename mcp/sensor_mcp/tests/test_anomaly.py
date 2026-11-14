"""Anomaly detectors against the simulator: healthy must be QUIET, each fault must be FOUND.

No database: the simulator produces 10-s signals in memory, we average them to 1-minute means
(what the tool gets from Postgres) and run the detectors. The healthy-week test is the most
important one: a detector that fires on normal operation teaches the agent to ignore it.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from app.config import get_settings
from app.db.models import FaultType
from app.plant import PlantConfig, load_plant
from sensor_mcp.anomaly import Finding, FindingKind, Series, analyze, robust_zscore
from simulator.faults import Fault
from simulator.signals import simulate_machine, utc

SEED = 42
MONDAY = utc(2026, 11, 16)


@pytest.fixture(scope="module")
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


def _series(
    plant: PlantConfig, code: str, start: datetime, days: float, faults: list[Fault] | None = None
) -> list[Series]:
    m = next(m for m in plant.machines if m.code == code)
    sensors = plant.sensors_for(m)
    sim = simulate_machine(
        m, sensors, start, start + timedelta(days=days), SEED, faults=faults or []
    )
    times = sim.times[::6]  # 6 x 10 s = 1 minute
    return [
        Series(
            s.name,
            s.kind,
            s.unit,
            s.normal_min,
            s.normal_max,
            times,
            sim.values[s.name].reshape(-1, 6).mean(axis=1),
        )
        for s in sensors
    ]


def _of(findings: list[Finding], sensor: str, kind: FindingKind | None = None) -> list[Finding]:
    return [f for f in findings if f.sensor == sensor and (kind is None or f.kind is kind)]


# ------------------------------------------------------------------ robust_zscore (your TODO)


def test_zscore_of_constant_data_is_zero_not_nan() -> None:
    z = robust_zscore(np.full(11, 5.0), window=5, min_scale=0.1)
    assert np.all(z[2:-2] == 0.0)


def test_zscore_edges_are_nan_and_length_is_kept() -> None:
    z = robust_zscore(np.arange(10, dtype=float), window=5, min_scale=0.1)
    assert len(z) == 10
    assert np.isnan(z[:2]).all() and np.isnan(z[-2:]).all() and not np.isnan(z[2:-2]).any()


def test_zscore_flags_a_spike_and_ignores_noise() -> None:
    rng = np.random.default_rng(0)
    x = 10 + rng.normal(0, 0.1, 200)
    x[100] = 15.0
    z = robust_zscore(x, window=21, min_scale=0.01)
    assert abs(z[100]) > 20
    assert np.nanmax(np.abs(np.delete(z, 100))) < 6


def test_zscore_min_scale_floors_tiny_changes() -> None:
    x = np.full(11, 5.0)
    x[5] = 5.05  # MAD stays 0 -> scale = min_scale
    assert robust_zscore(x, window=5, min_scale=0.1)[5] == pytest.approx(0.5)


def test_zscore_short_series_is_all_nan() -> None:
    assert np.isnan(robust_zscore(np.ones(3), window=5, min_scale=0.1)).all()


@pytest.mark.parametrize(("window", "scale"), [(4, 0.1), (1, 0.1), (5, 0.0), (5, -1.0)])
def test_zscore_rejects_bad_parameters(window: int, scale: float) -> None:
    with pytest.raises(ValueError):
        robust_zscore(np.ones(20), window=window, min_scale=scale)


# ------------------------------------------------------------------ healthy: quiet


@pytest.mark.parametrize("code", [f"M-{i:02d}" for i in range(1, 11)])
def test_a_healthy_week_produces_no_findings(plant: PlantConfig, code: str) -> None:
    assert analyze(_series(plant, code, MONDAY, days=7)).findings == []


# ------------------------------------------------------------------ faults: found


def test_bearing_wear_shows_as_a_vibration_trend(plant: PlantConfig) -> None:
    f = Fault(FaultType.BEARING_WEAR, "M-01", MONDAY, MONDAY + timedelta(days=2, hours=18))
    findings = analyze(_series(plant, "M-01", MONDAY, days=3, faults=[f])).findings
    vib = _of(findings, "spindle_vibration", FindingKind.TREND)
    assert vib and vib[0].magnitude > 0


def test_drift_on_a_24_7_machine_is_an_isolated_trend(plant: PlantConfig) -> None:
    """M-08 never stops, so only the 'isolated' fact separates a lying sensor from the process."""
    f = Fault(
        FaultType.SENSOR_DRIFT, "M-08", MONDAY, MONDAY + timedelta(days=2), sensor="discharge_temp"
    )
    findings = analyze(_series(plant, "M-08", MONDAY, days=2, faults=[f])).findings
    assert {x.sensor for x in findings} == {"discharge_temp"}  # neighbours stay quiet
    trend = _of(findings, "discharge_temp", FindingKind.TREND)
    assert trend and trend[0].isolated and trend[0].magnitude > 0


def test_drift_on_a_shift_machine_moves_while_stopped(plant: PlantConfig) -> None:
    f = Fault(
        FaultType.SENSOR_DRIFT,
        "M-01",
        MONDAY,
        MONDAY + timedelta(days=3),
        sensor="spindle_bearing_temp",
    )
    findings = analyze(_series(plant, "M-01", MONDAY, days=4, faults=[f])).findings
    assert _of(findings, "spindle_bearing_temp", FindingKind.MOVES_WHEN_STOPPED)
    assert {x.sensor for x in findings} == {"spindle_bearing_temp"}


def test_clogged_filter_moves_several_signals_so_nothing_is_isolated(plant: PlantConfig) -> None:
    f = Fault(FaultType.CLOGGED_FILTER, "M-08", MONDAY, MONDAY + timedelta(days=2))
    findings = analyze(_series(plant, "M-08", MONDAY, days=2, faults=[f])).findings
    assert _of(findings, "inlet_filter_dp", FindingKind.TREND)
    assert len({x.sensor for x in findings}) >= 2
    assert not any(x.isolated for x in findings)


def test_coolant_pump_failure_drops_coolant_pressure_out_of_band(plant: PlantConfig) -> None:
    start = MONDAY + timedelta(hours=9)  # mid-shift, the machine has been running for hours
    f = Fault(FaultType.COOLANT_PUMP_FAILURE, "M-01", start, start + timedelta(minutes=30))
    findings = analyze(_series(plant, "M-01", MONDAY, days=1, faults=[f])).findings
    low = _of(findings, "coolant_pressure", FindingKind.OUT_OF_BAND)
    assert low and low[0].magnitude < 0


@pytest.mark.parametrize(
    ("code", "current"), [("M-05", "pump_motor_current"), ("M-03", "spindle_motor_current")]
)
def test_motor_overload_shows_the_current_up_speed_down_signature(
    plant: PlantConfig, code: str, current: str
) -> None:
    start = MONDAY + timedelta(hours=8)
    f = Fault(FaultType.MOTOR_OVERLOAD, code, start, start + timedelta(hours=2))
    findings = analyze(_series(plant, code, MONDAY, days=1, faults=[f])).findings
    assert _of(findings, current, FindingKind.CURRENT_UP_SPEED_DOWN)


def test_belt_slippage_shows_on_the_roller_speed(plant: PlantConfig) -> None:
    start = MONDAY + timedelta(hours=8)
    f = Fault(FaultType.BELT_SLIPPAGE, "M-04", start, start + timedelta(hours=4))
    findings = analyze(_series(plant, "M-04", MONDAY, days=1, faults=[f])).findings
    assert _of(findings, "drive_roller_speed")
