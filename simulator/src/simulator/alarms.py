"""PLC-style alarm generation from sensor readings.

Real controllers don't alarm the moment a value touches a limit. Two classic techniques keep one
noisy sensor near its limit from producing hundreds of alarms an hour (an "alarm flood", the
problem the ISA-18.2 alarm-management standard exists for):

- DEBOUNCE:   the value must be beyond the limit for `min_samples` consecutive readings.
- HYSTERESIS: once raised, the alarm only clears when the value is back past a *different*,
              safer level (clear level), not the moment it dips under the raise level.

    sample:        1   2   3   4   5   6   7   8
    value:         11  12  11  9   11  9   7   11
    raise > 10     x   x   x   .   x   .   .   x     x = above raise level
    clear < 8      .   .   .   .   .   .   x   .     x = below clear level
    state:         ..  ..  AL  AL  AL  AL  ok  ..
                           ^ raised (3rd in a row)
                                           ^ cleared (9 never cleared it: hysteresis)

And alarms are STATE-AWARE: "pressure low" during warm-up or a planned stop isn't a problem.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from app.db.models import AlarmSeverity, SensorKind
from app.plant import SensorSpec
from simulator.signals import DT_SECONDS, MachineSimulation, OpState

FloatArray = npt.NDArray[np.float64]

KIND_CODE = {
    SensorKind.TEMPERATURE: "TEMP",
    SensorKind.VIBRATION: "VIB",
    SensorKind.PRESSURE: "PRESS",
    SensorKind.CURRENT: "CURR",
    SensorKind.RPM: "RPM",
}
# Low alarms only where "too low" means something is wrong (a cold motor is fine, slow isn't).
LOW_ALARM_KINDS = {SensorKind.PRESSURE, SensorKind.RPM}
HYSTERESIS = 0.05  # clear level sits 5 % of the normal band back inside the limit
CRITICAL_MARGIN = 0.25  # critical alarm: 25 % of the band beyond the normal limit
WARM_UP_SECONDS = 3600  # after the shift starts (see test_signals: warm-up leaves the band)
SETTLE_SECONDS = 120  # after any short stop: pressures need a moment to build up


class Direction(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True)
class AlarmRule:
    code: str  # e.g. "TEMP-HIGH"
    severity: AlarmSeverity
    direction: Direction
    raise_at: float
    clear_at: float
    min_samples: int
    only_when_steady: bool  # evaluate only while running and warmed up


@dataclass(frozen=True)
class AlarmEvent:
    time: datetime
    cleared_at: datetime | None  # None = still active at the end of the data
    machine_code: str
    sensor_name: str | None  # None = machine-level (e.g. a trip)
    code: str
    severity: AlarmSeverity
    message: str


def alarm_episodes(
    values: FloatArray, raise_above: float, clear_below: float, min_samples: int
) -> list[tuple[int, int]]:
    """Debounced, hysteretic HIGH alarm over a series. Returns [(raised_idx, cleared_idx), ...].

    - Raised at index i when values[i - min_samples + 1 .. i] are ALL > raise_above
      (i is the sample where the debounce is satisfied, i.e. when a PLC would sound the alarm).
    - Once raised, it stays active until the first index j with values[j] < clear_below;
      cleared_idx = j. Values between clear_below and raise_above do NOT clear it.
    - Still active at the end -> cleared_idx = len(values).
    - After clearing, raising again needs min_samples fresh consecutive samples.
    - Strict comparisons: exactly == raise_above is not "above"; == clear_below does not clear.
    - Raises ValueError if clear_below > raise_above (hysteresis must point back inside) or
      min_samples < 1.

    For LOW alarms the caller negates: alarm_episodes(-v, -raise_below, -clear_above, n).
    """
    if clear_below > raise_above:
        raise ValueError("clear_below must be <= raise_above")

    if min_samples < 1:
        raise ValueError("min_samples must be >= 1")

    episodes: list[tuple[int, int]] = []
    count = 0
    active = False
    raised_idx = 0  # only read while active, so this initial value never leaks out

    for i, value in enumerate(values):
        if not active:
            if value > raise_above:
                count += 1

                if count >= min_samples:
                    active = True
                    raised_idx = i
            else:
                count = 0

        else:
            if value < clear_below:
                episodes.append((raised_idx, i))
                active = False
                count = 0

    if active:
        episodes.append((raised_idx, len(values)))

    return episodes


# ------------------------------------------------------------------ rules + detection


def rules_for(spec: SensorSpec) -> list[AlarmRule]:
    """Warning at the normal limit, critical further out; low alarms for pressure/speed."""
    band = spec.normal_max - spec.normal_min
    prefix = KIND_CODE[spec.kind]
    rules = [
        AlarmRule(
            f"{prefix}-HIGH",
            AlarmSeverity.WARNING,
            Direction.HIGH,
            raise_at=spec.normal_max,
            clear_at=spec.normal_max - HYSTERESIS * band,
            min_samples=3,  # 30 s
            only_when_steady=False,  # too hot is a problem in any state
        ),
        AlarmRule(
            f"{prefix}-CRIT",
            AlarmSeverity.CRITICAL,
            Direction.HIGH,
            raise_at=spec.normal_max + CRITICAL_MARGIN * band,
            clear_at=spec.normal_max + (CRITICAL_MARGIN - HYSTERESIS) * band,
            min_samples=3,
            only_when_steady=False,
        ),
    ]
    if spec.kind in LOW_ALARM_KINDS:
        rules.append(
            AlarmRule(
                f"{prefix}-LOW",
                AlarmSeverity.WARNING,
                Direction.LOW,
                raise_at=spec.normal_min,
                clear_at=spec.normal_min + HYSTERESIS * band,
                min_samples=6,  # 60 s: low pressure/speed is noisier around transitions
                only_when_steady=True,
            )
        )
    return rules


def steady_mask(states: npt.NDArray[np.int64]) -> npt.NDArray[np.bool_]:
    """Running, at least WARM_UP after the machine was last off, and SETTLE after any stop."""
    idx = np.arange(len(states))
    running = states == OpState.RUNNING

    def seconds_since(event: npt.NDArray[np.bool_]) -> npt.NDArray[np.int64]:
        last = np.maximum.accumulate(np.where(event, idx, -(10**9)))
        return (idx - last) * DT_SECONDS

    stopped = (states == OpState.OFF) | (states == OpState.DOWN)
    # Annotated local: numpy's stubs type `array == x` as Any, so state the real type once.
    steady: npt.NDArray[np.bool_] = (
        running
        & (seconds_since(stopped) >= WARM_UP_SECONDS)
        & (seconds_since(~running) >= SETTLE_SECONDS)
    )
    return steady


def _to_datetime(t: np.datetime64) -> datetime:
    return datetime.fromtimestamp(int(t.astype("datetime64[s]").astype(np.int64)), tz=UTC)


def detect_alarms(
    machine_code: str, sensors: list[SensorSpec], sim: MachineSimulation
) -> list[AlarmEvent]:
    """Every alarm a PLC would have raised over this simulation, sorted by time."""
    n = len(sim.times)
    events: list[AlarmEvent] = []
    steady = steady_mask(sim.states)

    def cleared(j: int) -> datetime | None:
        return _to_datetime(sim.times[j]) if j < n else None

    for spec in sensors:
        values = sim.values[spec.name]
        for rule in rules_for(spec):
            if rule.direction is Direction.HIGH:
                series, raise_at, clear_at = values, rule.raise_at, rule.clear_at
            else:  # LOW == HIGH on the negated series
                series, raise_at, clear_at = -values, -rule.raise_at, -rule.clear_at
            if rule.only_when_steady:
                # Outside steady running the condition can't hold, and an active alarm clears
                # (state-based alarming: a stopped pump's pressure is SUPPOSED to be low).
                series = np.where(steady, series, -np.inf)
            for i, j in alarm_episodes(series, raise_at, clear_at, rule.min_samples):
                word = "above" if rule.direction is Direction.HIGH else "below"
                events.append(
                    AlarmEvent(
                        time=_to_datetime(sim.times[i]),
                        cleared_at=cleared(j),
                        machine_code=machine_code,
                        sensor_name=spec.name,
                        code=rule.code,
                        severity=rule.severity,
                        message=(
                            f"{spec.name} {values[i]:.1f} {spec.unit} {word} limit "
                            f"{rule.raise_at:.1f} {spec.unit}"
                        ),
                    )
                )

    # Machine-level: the moment a fault tripped the machine (interlock / overload relay).
    down = sim.states == OpState.DOWN
    for k in np.flatnonzero(down & ~np.concatenate([[False], down[:-1]])):
        events.append(
            AlarmEvent(
                time=_to_datetime(sim.times[int(k)]),
                cleared_at=None,
                machine_code=machine_code,
                sensor_name=None,
                code="TRIP",
                severity=AlarmSeverity.CRITICAL,
                message="Machine tripped by protection (interlock / overload relay)",
            )
        )
    return sorted(events, key=lambda e: e.time)
