"""Healthy ("normal") sensor signals for one machine over a time window. Pure functions, no DB.

Model, per machine:
    shift schedule  ->  operating state (OFF / IDLE / RUNNING) per timestamp
    job sequence    ->  load in [0, 1] (how hard the machine is working)
    state + load    ->  per-sensor TARGET value
    target          ->  physical inertia (first-order lag) + sensor noise  ->  reading

Everything random comes from one seeded numpy Generator, so the same (seed, machine, window)
always produces exactly the same data. Evals (Phase 8) depend on that.
"""

import math
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from app.db.models import MachineType, SensorKind
from app.plant import MachineSpec, SensorSpec
from simulator.faults import (
    Fault,
    down_mask,
    measurement_effect,
    physical_effect,
    validate,
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

DT_SECONDS = 10  # one reading per sensor every 10 s
PLANT_TZ = ZoneInfo("Europe/Berlin")  # shifts follow local time (incl. DST), data is stored UTC
SHIFT_START_HOUR, SHIFT_END_HOUR = 6, 22  # two shifts, Mon-Fri


class OpState(IntEnum):
    OFF = 0  # outside shift hours: powered down, cooling toward ambient
    IDLE = 1  # short stop inside a shift (tool change, waiting for material): powered, warm
    RUNNING = 2  # producing
    DOWN = 3  # tripped by a fault (interlock / overload relay): stopped until repaired


@dataclass(frozen=True)
class LoadProfile:
    """How a machine type is operated."""

    follows_shifts: bool  # False = utility running 24/7 (compressor, central pump)
    day_load: tuple[float, float]  # job load range while running in shift hours
    night_load: tuple[float, float]  # utilities only: lower demand outside shift hours
    stops_per_hour: float  # short stops inside a shift


PROFILES: dict[MachineType, LoadProfile] = {
    MachineType.CNC_MILL: LoadProfile(True, (0.3, 1.0), (0.0, 0.0), 0.5),  # roughing vs finishing
    MachineType.INJECTION_MOLDER: LoadProfile(True, (0.6, 0.9), (0.0, 0.0), 0.3),
    MachineType.CONVEYOR: LoadProfile(True, (0.45, 0.6), (0.0, 0.0), 0.3),  # steady belt speed
    MachineType.COMPRESSOR: LoadProfile(False, (0.6, 0.9), (0.2, 0.4), 0.0),  # air demand
    MachineType.PUMP: LoadProfile(False, (0.7, 0.9), (0.4, 0.5), 0.0),
}

# Per sensor kind: thermal/hydraulic inertia (tau, seconds) and noise (fraction of the normal band).
TAU_SECONDS: dict[SensorKind, float] = {
    SensorKind.TEMPERATURE: 900.0,  # metal + oil heat up / cool down over tens of minutes
    SensorKind.PRESSURE: 30.0,  # pumps build pressure in seconds
}
NOISE_FRACTION: dict[SensorKind, float] = {
    SensorKind.TEMPERATURE: 0.005,  # RTDs are precise
    SensorKind.VIBRATION: 0.06,  # vibration is inherently noisy
    SensorKind.CURRENT: 0.02,
    SensorKind.RPM: 0.005,  # encoder
    SensorKind.PRESSURE: 0.015,
}


@dataclass(frozen=True)
class MachineSimulation:
    times: npt.NDArray[np.datetime64]  # UTC, second resolution
    states: IntArray  # OpState per timestamp
    load: FloatArray
    values: dict[str, FloatArray]  # sensor name -> readings


# ------------------------------------------------------------------ building blocks


def time_grid(start: datetime, end: datetime, dt: int = DT_SECONDS) -> IntArray:
    """UTC epoch seconds from start (inclusive) to end (exclusive), aligned to dt."""
    first = -(-int(start.timestamp()) // dt) * dt  # ceil to the grid, so windows line up
    return np.arange(first, int(end.timestamp()), dt, dtype=np.int64)


def local_epoch(epoch: IntArray, tz: ZoneInfo = PLANT_TZ) -> IntArray:
    """UTC epoch seconds -> 'local wall-clock' epoch seconds (DST-aware), vectorized.

    Calling zoneinfo per timestamp is slow (60k+ per day). The UTC offset can only change on an
    hour boundary, so look it up once per distinct UTC hour and broadcast back.
    """
    hours, inverse = np.unique(epoch // 3600, return_inverse=True)
    offsets = np.array([_utc_offset_seconds(int(h) * 3600, tz) for h in hours], dtype=np.int64)
    return epoch + offsets[inverse]


def _utc_offset_seconds(ts: int, tz: ZoneInfo) -> int:
    offset = datetime.fromtimestamp(ts, tz).utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def shift_mask(epoch: IntArray, tz: ZoneInfo = PLANT_TZ) -> npt.NDArray[np.bool_]:
    """True where local time is a production shift (Mon-Fri, 06:00-22:00)."""
    local = local_epoch(epoch, tz)
    weekday = (local // 86400 + 3) % 7  # 1970-01-01 was a Thursday; Monday = 0
    hour = local % 86400 // 3600
    return (weekday < 5) & (hour >= SHIFT_START_HOUR) & (hour < SHIFT_END_HOUR)


def ambient_temperature(epoch: IntArray, tz: ZoneInfo = PLANT_TZ) -> FloatArray:
    """Factory hall air: ~21 °C, ±3 °C daily swing peaking mid-afternoon (15:00 local)."""
    local_hours = local_epoch(epoch, tz) % 86400 / 3600
    return 21.0 + 3.0 * np.sin(2 * np.pi * (local_hours - 9.0) / 24.0)


def _segments(n: int, low: float, high: float, rng: np.random.Generator) -> FloatArray:
    """Piecewise-constant load: one 'job' every 10-40 minutes, each at its own load level."""
    out = np.empty(n)
    i = 0
    while i < n:
        length = int(rng.uniform(600, 2400) // DT_SECONDS)
        out[i : i + length] = rng.uniform(low, high)
        i += length
    return out


def operating_states(
    in_shift: npt.NDArray[np.bool_], profile: LoadProfile, rng: np.random.Generator
) -> IntArray:
    if not profile.follows_shifts:
        return np.full(len(in_shift), OpState.RUNNING, dtype=np.int64)
    states = np.where(in_shift, OpState.RUNNING, OpState.OFF).astype(np.int64)
    shift_hours = in_shift.sum() * DT_SECONDS / 3600
    for _ in range(rng.poisson(profile.stops_per_hour * shift_hours)):
        start = int(rng.integers(0, len(states)))
        length = int(rng.uniform(180, 900) // DT_SECONDS)  # 3-15 min
        window = states[start : start + length]
        window[window == OpState.RUNNING] = OpState.IDLE  # a stop can't happen while OFF
    return states


def machine_load(
    in_shift: npt.NDArray[np.bool_], profile: LoadProfile, rng: np.random.Generator
) -> FloatArray:
    day = _segments(len(in_shift), *profile.day_load, rng)
    if profile.follows_shifts:
        return day
    night = _segments(len(in_shift), *profile.night_load, rng)
    return np.where(in_shift, day, night)


def first_order_lag(targets: FloatArray, initial: float, dt: float, tau: float) -> FloatArray:
    """Physical inertia: each step the value moves a fraction alpha of the way to its target.

        y[k] = y[k-1] + alpha * (targets[k] - y[k-1]),   alpha = 1 - exp(-dt / tau)

    Exact discretization of dy/dt = (target - y) / tau (target held constant per step):
    0 < alpha < 1 for any dt > 0, so it can never overshoot. After one tau: 63 % (1 - 1/e).
    """
    if tau <= 0:
        raise ValueError(f"tau must be > 0, got {tau}")
    if dt < 0:
        raise ValueError(f"dt must be >= 0, got {dt}")

    alpha = 1.0 - math.exp(-dt / tau)

    out = np.empty_like(targets, dtype=np.float64)  # new array, input untouched
    y = initial
    for k, target in enumerate(targets):
        y = y + alpha * (target - y)
        out[k] = y
    return out


# ------------------------------------------------------------------ per-sensor physics


def _target(
    spec: SensorSpec,
    states: IntArray,
    load: FloatArray,
    ambient: FloatArray,
) -> FloatArray:
    """The value a sensor settles at, given what the machine is doing. Fractions are of the
    normal band (normal_min..normal_max), chosen so a healthy RUNNING machine sits well inside it.
    """
    lo, band = spec.normal_min, spec.normal_max - spec.normal_min
    running, idle = states == OpState.RUNNING, states == OpState.IDLE
    match spec.kind:
        case SensorKind.TEMPERATURE:
            # Running: warmer under load. Idle (short stop): stays warm. Off: drifts to ambient.
            return np.select(
                [running, idle], [lo + (0.35 + 0.45 * load) * band, lo + 0.3 * band], ambient
            )
        case SensorKind.RPM:
            return np.where(running, lo + (0.2 + 0.7 * load) * band, 0.0)
        case SensorKind.CURRENT:
            # Current follows load (the correlation real faults like motor overload break).
            # Idle: control electronics + unloaded motor; off: nothing.
            return np.select([running, idle], [lo + (0.15 + 0.75 * load) * band, 0.5 * lo], 0.0)
        case SensorKind.PRESSURE:
            # Filter/strainer differential pressure grows with flow squared.
            level = load**2 if spec.name.endswith("_dp") else load
            return np.where(running, lo + (0.2 + 0.6 * level) * band, 0.0)
        case SensorKind.VIBRATION:
            # Floor when stopped: the neighbours' vibration still reaches the sensor.
            return np.where(running, lo + (0.15 + 0.35 * load) * band, 0.3 * lo)


def sensor_signal(
    spec: SensorSpec,
    states: IntArray,
    load: FloatArray,
    ambient: FloatArray,
    rng: np.random.Generator,
    dt: float = DT_SECONDS,
    initial: float | None = None,
    epoch: IntArray | None = None,
    faults: Sequence[Fault] = (),
    seed: int = 0,
    machine_type: MachineType | None = None,
) -> FloatArray:
    """One sensor's readings. `initial` = where a lagged signal starts (e.g. the last stored
    reading, so live mode continues smoothly); default: already settled at its first target.

    Pipeline:  target -> physical fault effects -> lag -> noise -> measurement fault effects
    """
    target = _target(spec, states, load, ambient)
    if faults:
        assert epoch is not None, "faults need the time grid"
        assert machine_type is not None, "faults need the machine type"
        running = states == OpState.RUNNING
        for fault in faults:
            target = physical_effect(fault, spec, machine_type, epoch, target, running, seed)
    if (tau := TAU_SECONDS.get(spec.kind)) is not None:
        start = float(target[0]) if initial is None else initial
        target = first_order_lag(target, initial=start, dt=dt, tau=tau)
    sigma = NOISE_FRACTION[spec.kind] * (spec.normal_max - spec.normal_min)
    noise = rng.normal(0.0, sigma, len(target))
    if spec.kind is SensorKind.RPM:
        noise[states != OpState.RUNNING] = 0.0  # a stopped spindle's encoder reads exactly 0
    values = np.clip(target + noise, 0.0, None)  # no negative pressures / currents / vibration
    for fault in faults:
        assert epoch is not None
        values = measurement_effect(fault, spec, epoch, values)
    return values


# ------------------------------------------------------------------ public entry point


# Jobs and short stops are planned in fixed blocks of ABSOLUTE time, each with its own RNG stream.
# So any window (a 7-day backfill or a 10-second live tick) sees the same plant behaviour for the
# same hour, and live mode doesn't redraw a new random job every tick.
BLOCK_SECONDS = 3600


def machine_rng(seed: int, stream: str, block_start: int) -> np.random.Generator:
    """Independent, stable random stream per (seed, stream, block).

    crc32(machine code) instead of the machine's position in plant.yaml: adding a machine must
    not change every other machine's data.
    """
    return np.random.default_rng([seed, zlib.crc32(stream.encode()), block_start])


def _plan(
    epoch: IntArray, machine: MachineSpec, seed: int
) -> tuple[npt.NDArray[np.bool_], IntArray, FloatArray]:
    """Shift mask, operating states and load for `epoch`, assembled from whole hour blocks."""
    profile = PROFILES[machine.type]
    in_shift = shift_mask(epoch)
    states = np.empty(len(epoch), dtype=np.int64)
    load = np.empty(len(epoch))
    block_of = epoch // BLOCK_SECONDS
    for block in np.unique(block_of):
        block_start = int(block) * BLOCK_SECONDS
        block_epoch = np.arange(block_start, block_start + BLOCK_SECONDS, DT_SECONDS)
        rng = machine_rng(seed, machine.code, block_start)
        block_shift = shift_mask(block_epoch)
        block_states = operating_states(block_shift, profile, rng)
        block_load = machine_load(block_shift, profile, rng)
        selected = block_of == block
        offsets = (epoch[selected] - block_start) // DT_SECONDS
        states[selected] = block_states[offsets]
        load[selected] = block_load[offsets]
    return in_shift, states, load


def simulate_machine(
    machine: MachineSpec,
    sensors: list[SensorSpec],
    start: datetime,
    end: datetime,
    seed: int,
    initial: Mapping[str, float] | None = None,
    faults: Sequence[Fault] = (),
) -> MachineSimulation:
    """Signals for every sensor of one machine over [start, end): healthy, plus `faults`.

    `initial`: sensor name -> value to continue lagged signals from (live mode passes the last
    stored readings). Sensors not in it start settled.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start/end must be timezone-aware (use UTC)")
    faults = [f for f in faults if f.machine_code == machine.code]
    for fault in faults:
        validate(fault, machine, sensors)
    epoch = time_grid(start, end)
    _, states, load = _plan(epoch, machine, seed)
    # A tripped machine stays stopped while the fault is active (nobody repaired it yet).
    for fault in faults:
        states[down_mask(fault, epoch)] = OpState.DOWN
    ambient = ambient_temperature(epoch)
    noise_rng = machine_rng(seed, f"{machine.code}:noise", int(epoch[0]) if len(epoch) else 0)
    initial = initial or {}
    values = {
        s.name: sensor_signal(
            s,
            states,
            load,
            ambient,
            noise_rng,
            initial=initial.get(s.name),
            epoch=epoch,
            faults=faults,
            seed=seed,
            machine_type=machine.type,
        )
        for s in sensors
    }
    return MachineSimulation(
        times=epoch.astype("datetime64[s]"), states=states, load=load, values=values
    )


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Small helper for tests and the CLI."""
    return datetime(year, month, day, hour, tzinfo=UTC)
