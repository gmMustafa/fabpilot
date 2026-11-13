"""Fault injection: the six known root causes from the brief, as transformations of healthy data.

Two kinds of effect, applied at different points of the signal pipeline (see signals.py):

    target --[PHYSICAL effects]--> first_order_lag --> + noise --[MEASUREMENT effects]--> reading

- Physical faults change the PROCESS (a failing coolant pump really heats the spindle), so they go
  through the inertia: temperature rises with tau, not instantly.
- Sensor drift changes only the MEASUREMENT: the machine is fine, one sensor lies. Every other
  sensor stays exactly as it would have been, which is the clue the agent must find.

A fault develops from `start` to `full_at` (progress p: 0 -> 1) and then persists at full severity
until cleared. Faults that trip the machine stop it at `full_at`.
"""

import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import numpy.typing as npt

from app.db.models import FaultType, MachineType, SensorKind
from app.plant import MachineSpec, SensorSpec

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]


# Where each fault can physically happen (sensor drift: anywhere).
APPLICABLE: dict[FaultType, set[MachineType]] = {
    FaultType.BEARING_WEAR: {MachineType.CNC_MILL, MachineType.PUMP},
    FaultType.COOLANT_PUMP_FAILURE: {MachineType.CNC_MILL},
    FaultType.CLOGGED_FILTER: {MachineType.COMPRESSOR, MachineType.PUMP},
    FaultType.MOTOR_OVERLOAD: {MachineType.INJECTION_MOLDER, MachineType.CNC_MILL},
    FaultType.SENSOR_DRIFT: set(MachineType),
    FaultType.BELT_SLIPPAGE: {MachineType.CONVEYOR},
}

# How long each fault takes to fully develop: wear is slow, a pump failure is fast.
DEFAULT_DURATION: dict[FaultType, timedelta] = {
    FaultType.BEARING_WEAR: timedelta(days=3),
    FaultType.COOLANT_PUMP_FAILURE: timedelta(minutes=30),
    FaultType.CLOGGED_FILTER: timedelta(days=2),
    FaultType.MOTOR_OVERLOAD: timedelta(hours=2),
    FaultType.SENSOR_DRIFT: timedelta(days=2),
    FaultType.BELT_SLIPPAGE: timedelta(hours=4),
}


@dataclass(frozen=True)
class Fault:
    type: FaultType
    machine_code: str
    start: datetime
    full_at: datetime  # fully developed (and, for tripping faults, the moment the machine stops)
    severity: float = 1.0  # scales the effect; 1.0 = clearly abnormal, alarm-worthy
    sensor: str | None = None  # SENSOR_DRIFT only: which sensor lies
    cleared_at: datetime | None = None  # repaired: no effect from here on; None = still active

    @property
    def trips(self) -> bool:
        """Does this fault stop the machine at full_at? (protection relay / interlock)"""
        if self.type is FaultType.COOLANT_PUMP_FAILURE:
            return True  # spindle overheats -> thermal interlock
        return self.type is FaultType.MOTOR_OVERLOAD and self.severity >= 1.0  # overload relay


def validate(fault: Fault, machine: MachineSpec, sensors: list[SensorSpec]) -> None:
    """Reject physically meaningless faults (coolant failure on a conveyor) with a clear error."""
    if machine.type not in APPLICABLE[fault.type]:
        allowed = ", ".join(sorted(APPLICABLE[fault.type]))
        raise ValueError(f"{fault.type} can't happen on a {machine.type} (allowed: {allowed})")
    if fault.full_at <= fault.start:
        raise ValueError("full_at must be after start")
    if fault.type is FaultType.SENSOR_DRIFT:
        names = {s.name for s in sensors}
        if fault.sensor not in names:
            raise ValueError(f"sensor_drift needs --sensor, one of: {sorted(names)}")


def default_drift_sensor(sensors: list[SensorSpec]) -> str:
    """A temperature sensor if the machine has one (the classic drifting RTD), else the first."""
    temps = [s.name for s in sensors if s.kind is SensorKind.TEMPERATURE]
    return temps[0] if temps else sensors[0].name


# ------------------------------------------------------------------ helpers


def active(fault: Fault, epoch: IntArray) -> BoolArray:
    """True from start until the fault is cleared (repaired); open-ended if never cleared."""
    mask = epoch >= fault.start.timestamp()
    if fault.cleared_at is not None:
        mask &= epoch < fault.cleared_at.timestamp()
    return mask


def progress(fault: Fault, epoch: IntArray) -> FloatArray:
    """0 before start, rising linearly to 1 at full_at, 1 afterwards; 0 again once cleared."""
    start, full = fault.start.timestamp(), fault.full_at.timestamp()
    p: FloatArray = np.clip((epoch - start) / (full - start), 0.0, 1.0) * active(fault, epoch)
    return p


def down_mask(fault: Fault, epoch: IntArray) -> BoolArray:
    """Where a tripping fault keeps the machine stopped: from full_at until repaired."""
    if not fault.trips:
        return np.zeros(len(epoch), dtype=bool)
    return (epoch >= fault.full_at.timestamp()) & active(fault, epoch)


def _fault_rng(fault: Fault, seed: int) -> np.random.Generator:
    key = f"{fault.machine_code}:{fault.type}:{int(fault.start.timestamp())}"
    return np.random.default_rng([seed, zlib.crc32(key.encode())])


def _episodes(fault: Fault, epoch: IntArray, seed: int) -> BoolArray:
    """Intermittent episodes (e.g. overload when a heavy part is molded), getting more frequent
    as the fault develops. Drawn over the fault's whole life on the absolute time grid, so any
    window (backfill or a 10 s live tick) sees the same episodes."""
    rng = _fault_rng(fault, seed)
    start, full = int(fault.start.timestamp()), int(fault.full_at.timestamp())
    mask = np.zeros(len(epoch), dtype=bool)
    t = start
    while t < full:
        p = (t - start) / (full - start)
        gap = rng.uniform(300, 1800) * (1.0 - 0.8 * p)  # quieter early, frequent near the end
        length = rng.uniform(120, 600)
        t += int(gap)
        mask |= (epoch >= t) & (epoch < t + length)
        t += int(length)
    mask |= epoch >= full  # fully developed: permanently overloaded (until it trips)
    return mask


def _band(spec: SensorSpec) -> float:
    return spec.normal_max - spec.normal_min


# ------------------------------------------------------------------ effects


def physical_effect(
    fault: Fault,
    spec: SensorSpec,
    machine_type: MachineType,
    epoch: IntArray,
    target: FloatArray,
    running: BoolArray,
    seed: int,
) -> FloatArray:
    """Change the TARGET (the real process) of one sensor. Returns a new array.

    `machine_type`: the same fault plays out differently per machine (CLOGGED_FILTER).
    """
    p = progress(fault, epoch)
    if not p.any():
        return target
    out = target.copy()
    band, name, kind = _band(spec), spec.name, spec.kind
    match fault.type:
        case FaultType.BEARING_WEAR:
            # Vibration grows first and accelerates (p^2): defects on the race get worse with
            # every rotation. Friction heat only shows in the late stage (p > 0.7).
            if kind is SensorKind.VIBRATION:
                out[running] *= 1.0 + 3.0 * fault.severity * p[running] ** 2
            elif kind is SensorKind.TEMPERATURE and "bearing" in name:
                late = np.clip((p - 0.7) / 0.3, 0.0, 1.0) ** 2
                out[running] += 0.6 * band * fault.severity * late[running]
        case FaultType.COOLANT_PUMP_FAILURE:
            # Pump dies within a minute; without coolant, the spindle heats up (through the lag).
            dead = np.clip((epoch - fault.start.timestamp()) / 60.0, 0.0, 1.0)
            dead *= active(fault, epoch)  # a repaired pump pumps again
            if name == "coolant_pressure":
                out *= 1.0 - dead
            elif kind is SensorKind.TEMPERATURE and "spindle" in name:
                out[running] += 1.2 * band * fault.severity * dead[running]
        case FaultType.CLOGGED_FILTER:
            # Differential pressure across the filter rises steadily on both machine types.
            # What follows depends on the machine (and the manuals say the same):
            # - screw compressor (positive displacement): higher compression ratio, it works
            #   harder -> current and discharge temperature up (MAN-CMP-001 §5.1).
            # - centrifugal pump: starved suction -> less flow -> discharge pressure down and
            #   current slightly DOWN, since its power ~ flow (MAN-PMP-001 §5.4).
            ramp = band * fault.severity * p[running]
            if name.endswith("_dp"):
                out[running] += 1.5 * ramp
            elif machine_type is MachineType.COMPRESSOR:
                if kind is SensorKind.CURRENT:
                    out[running] += 0.15 * ramp
                elif kind is SensorKind.TEMPERATURE and "discharge" in name:
                    out[running] += 0.3 * ramp
            elif machine_type is MachineType.PUMP:
                if kind is SensorKind.CURRENT:
                    out[running] -= 0.15 * ramp
                elif name == "discharge_pressure":
                    out[running] -= 0.5 * ramp
        case FaultType.MOTOR_OVERLOAD:
            # During episodes: current jumps WHILE speed sags (the motor can't hold speed under
            # the load). This breaks the healthy current-tracks-speed correlation.
            ep = _episodes(fault, epoch, seed) & running & (p > 0)
            if kind is SensorKind.CURRENT:
                out[ep] += 0.45 * band * fault.severity
            elif kind is SensorKind.RPM:
                out[ep] *= 1.0 - 0.25 * fault.severity
            elif kind is SensorKind.TEMPERATURE:
                out[ep] += 0.3 * band * fault.severity
        case FaultType.BELT_SLIPPAGE:
            # Slipping belt: roller speed oscillates and dips; less torque gets through, so motor
            # current fluctuates with it. Period ~2 min (well above the 10 s sampling: no aliasing).
            wave = 0.5 * (1.0 + np.sin(2 * np.pi * epoch / 120.0))
            dip = fault.severity * p * wave
            if kind is SensorKind.RPM:
                out[running] *= 1.0 - 0.35 * dip[running]
            elif kind is SensorKind.CURRENT:
                out[running] *= 1.0 - 0.25 * dip[running]
        case FaultType.SENSOR_DRIFT:
            pass  # not physical: see measurement_effect
    return out


def measurement_effect(
    fault: Fault, spec: SensorSpec, epoch: IntArray, values: FloatArray
) -> FloatArray:
    """Change what one sensor REPORTS, not what the process does. Returns a new array."""
    if fault.type is not FaultType.SENSOR_DRIFT or spec.name != fault.sensor:
        return values
    # Slow, steady offset (aging RTD / loose connection), regardless of machine state: a broken
    # sensor lies even while the machine is off.
    return values + 0.8 * _band(spec) * fault.severity * progress(fault, epoch)
