"""Anomaly detection (4.1b): deterministic statistics, so the agent reasons over FINDINGS, not over
thousands of numbers (LLMs miss trends and see patterns in noise).

Input: 1-minute means of every sensor of ONE machine. Steps:
  1. infer the operating state from the machine's current sensor (we don't store states):
     running = current >= its normal_min (verified against the simulator: 99.7-100 % agreement)
  2. "steady" = running for >= WARM_UP minutes (a cold machine at shift start is not an anomaly)
  3. four detectors per sensor, each built for one fault shape:
       out_of_band        values outside the normal band while steady   (limit violations)
       spike              robust z-score > Z_LIMIT for >= SPIKE_MIN min  (sudden jumps, episodes)
       trend              recent median vs baseline median, % of band   (slow creep: wear, drift)
       moves_when_stopped readings that change while the machine is off (a lying sensor)
  4. facts, not diagnoses: `isolated` says no OTHER sensor of the machine changed. That fits a
     drifting sensor -- and also early bearing wear (vibration first, heat later). The agent
     decides with the manual; the detector only reports what the numbers show.

Pure numpy, no I/O: unit-tested directly against the simulator's healthy and faulty signals.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel

from app.db.models import SensorKind

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

# Minutes of running before LOW deviations and statistics count. Like the PLC: low values right
# after start-up are normal (cold, pressure building up); HIGH values are a problem at once (F17:
# waiting for HIGH too shifted a finding 24 min past the first alarm, read as a false timing fact).
WARM_UP = 60
SETTLED = 120  # minutes after stopping before "stopped" readings count as settled
Z_WINDOW = 61  # minutes, centered window for the rolling median / MAD
Z_LIMIT = 4.0  # robust z beyond this is a spike
# ... AND the jump must be >= 35 % of the normal band (effect size). MEASURED (4.1b): after the
# load-change filter, healthy weeks peak at ~27 %; injected overload episodes are 40-51 %.
SPIKE_SHARE = 0.35
SPIKE_MIN = 3  # ... if held for at least this many consecutive minutes
# Trend threshold, MEASURED (4.1b): healthy 24/7 utilities (compressor, pump) shift by up to
# ~30 % of their band between weekdays and the weekend (lines off -> less demand). 40 % sits above
# that; injected wear/drift/clogging reach 80-300 %. Cost: a fault still below 40 % isn't called a
# trend yet (the out-of-band detector and the PLC alarms still see it once it crosses a limit).
TREND_SHARE = 0.40
SIDE = 30  # minutes before / after a point that define "the level around it" for spikes
# Overload signature thresholds, MEASURED from the simulator (4.1b): an overload episode moves the
# current by about +45 % of its band and the speed by about -18 % (CNC) / -19 % (molder).
OVERLOAD_CURRENT_UP = 0.20
OVERLOAD_SPEED_DOWN = 0.08
LOAD_CHANGE_SHARE = 0.10  # both current and speed moved >= 10 % of band, same way = load change
OUT_OF_BAND_MIN = 10  # minutes outside the band before it is a finding
# LOW deviations only matter where "too low" is a problem (same rule as the PLC alarms).
LOW_MATTERS = {SensorKind.PRESSURE, SensorKind.RPM}


@dataclass(frozen=True)
class Series:
    """One sensor's 1-minute means. `times` are naive UTC datetime64, oldest first."""

    name: str
    kind: SensorKind
    unit: str
    normal_min: float
    normal_max: float
    times: npt.NDArray[np.datetime64]
    values: FloatArray

    @property
    def band(self) -> float:
        return self.normal_max - self.normal_min


class FindingKind(StrEnum):
    OUT_OF_BAND = "out_of_band"
    SPIKE = "spike"
    TREND = "trend"
    MOVES_WHEN_STOPPED = "moves_when_stopped"
    # current up WHILE speed down: the overload signature of MAN-CNC-001 §5.4 / MAN-IMM-001 §5.1
    CURRENT_UP_SPEED_DOWN = "current_up_speed_down"


class Finding(BaseModel):
    sensor: str
    kind: FindingKind
    start: datetime  # first minute of the anomaly (UTC)
    end: datetime  # last minute
    magnitude: float  # in % of the sensor's normal band (sign = direction)
    isolated: bool = False  # set by analyze(): no OTHER sensor of this machine has a finding
    summary: str  # one sentence for the model


def robust_zscore(values: FloatArray, window: int, min_scale: float) -> FloatArray:
    """Rolling robust z-score: (x - rolling median) / max(1.4826 * rolling MAD, min_scale).

    - The window is CENTERED and has odd length `window`; where it doesn't fit completely (the
      first and last window // 2 points), the result is NaN.
    - MAD = median(|x - median|) over the window; 1.4826 * MAD estimates the standard deviation
      for normally distributed data, so z is comparable to a classic z-score.
    - min_scale: a floor for the scale. On perfectly constant data MAD = 0, and x - median = 0,
      so a bare division gives 0/0 = NaN (or x/0 = inf one minute later). The floor keeps z
      finite and meaningful: a change smaller than min_scale never looks huge.
    - Raises ValueError if window is even or < 3, or min_scale <= 0.

    Why median/MAD, not mean/std: a single 30-minute spike inflates the std of its own window so
    much that it hides itself; the median and MAD barely notice it.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    if window < 3 or window % 2 == 0:
        raise ValueError("window must be odd and at least 3")

    if min_scale <= 0:
        raise ValueError("min_scale must be greater than 0")

    n = len(values)
    out = np.full(n, np.nan, dtype=np.float64)

    if n < window:
        return out

    half = window // 2

    # Create rolling windows
    win = sliding_window_view(values, window)

    # Calculate median and median absolute deviation
    med = np.median(win, axis=1)
    mad = np.median(np.abs(win - med[:, None]), axis=1)

    # Convert MAD to a robust estimate of standard deviation
    scale = np.maximum(1.4826 * mad, min_scale)

    # Calculate robust z-scores
    z = (values[half : n - half] - med) / scale

    # Place results at the center of each window
    out[half : half + len(z)] = z

    return out


def _runs(mask: BoolArray, min_len: int) -> list[tuple[int, int]]:
    """[(first, last)] index ranges where mask is True for at least min_len consecutive points."""
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(np.diff(padded.astype(np.int8)))
    return [
        (int(a), int(b) - 1)
        for a, b in zip(edges[::2], edges[1::2], strict=True)
        if b - a >= min_len
    ]


def minutes_since_change(flag: BoolArray) -> npt.NDArray[np.int64]:
    """For each point: how many points the current value of `flag` has lasted (1 = just changed)."""
    out = np.zeros(len(flag), dtype=np.int64)
    run = 0
    for i in range(len(flag)):
        run = run + 1 if i and flag[i] == flag[i - 1] else 1
        out[i] = run
    return out


def _t(times: npt.NDArray[np.datetime64], i: int) -> datetime:
    return datetime.fromisoformat(str(times[i].astype("datetime64[s]")) + "+00:00")


def _pct(delta: float, band: float) -> float:
    return round(100.0 * delta / band, 1)


def out_of_band(s: Series, running: BoolArray, steady: BoolArray) -> list[Finding]:
    """HIGH while running (too hot is too hot, also right after start-up), LOW only when steady."""
    high = running & (s.values > s.normal_max)
    low = steady & (s.values < s.normal_min) if s.kind in LOW_MATTERS else np.zeros_like(steady)
    findings = []
    for mask, word, limit in ((high, "above", s.normal_max), (low, "below", s.normal_min)):
        # CONSECUTIVE minutes: many 1-minute ramps at stops add up over a week, a fault doesn't
        # come and go every minute.
        runs = _runs(mask, OUT_OF_BAND_MIN)
        if not runs:
            continue
        minutes = sum(b - a + 1 for a, b in runs)
        inside = np.zeros_like(mask)
        for a, b in runs:
            inside[a : b + 1] = True
        worst = float(np.max(s.values[inside]) if word == "above" else np.min(s.values[inside]))
        findings.append(
            Finding(
                sensor=s.name,
                kind=FindingKind.OUT_OF_BAND,
                start=_t(s.times, runs[0][0]),
                end=_t(s.times, runs[-1][1]),
                magnitude=_pct(worst - limit, s.band),
                summary=(
                    f"{s.name} was {word} its normal {'max' if word == 'above' else 'min'} "
                    f"{limit:g} {s.unit} for {minutes} running minutes (worst {worst:.4g} {s.unit})"
                ),
            )
        )
    return findings


def _side_medians(values: FloatArray, w: int) -> tuple[FloatArray, FloatArray]:
    """Median of the w points BEFORE and the w points AFTER each point (NaN at the edges)."""
    from numpy.lib.stride_tricks import sliding_window_view

    n = len(values)
    before, after = np.full(n, np.nan), np.full(n, np.nan)
    if n > w:
        med = np.median(sliding_window_view(values, w), axis=1)  # med[k] = median(values[k:k+w])
        before[w:] = med[: n - w]  # window ending just before i
        after[: n - w] = med[1:]  # window starting just after i
    return before, after


def spikes(s: Series, steady: BoolArray) -> list[Finding]:
    """Transient excursions: statistically unusual AND large AND away from the level on BOTH
    sides. A new job's load step moves the level and stays there (matches the level after it);
    an overload episode leaves and comes back (differs from before AND after)."""
    z = robust_zscore(s.values, Z_WINDOW, min_scale=0.02 * s.band)
    before, after = _side_medians(s.values, SIDE)
    up, down = s.values - before, s.values - after
    same_direction = np.sign(up) == np.sign(down)
    effect = np.where(same_direction, np.minimum(np.abs(up), np.abs(down)), 0.0)
    hot = (
        steady
        & (np.abs(np.nan_to_num(z)) > Z_LIMIT)  # statistically unusual
        & (np.nan_to_num(effect) >= SPIKE_SHARE * s.band)  # practically large, both sides
    )
    findings = []
    for a, b in _runs(hot, SPIKE_MIN):
        peak = a + int(np.argmax(np.abs(z[a : b + 1])))
        jump = float(s.values[peak] - before[peak])
        findings.append(
            Finding(
                sensor=s.name,
                kind=FindingKind.SPIKE,
                start=_t(s.times, a),
                end=_t(s.times, b),
                magnitude=_pct(jump, s.band),
                summary=(
                    f"{s.name} jumped {'up' if jump > 0 else 'down'} by {abs(jump):.3g} {s.unit} "
                    f"for {b - a + 1} min and came back (robust z {float(z[peak]):+.1f})"
                ),
            )
        )
    return findings[:5]  # a list of 50 episodes adds tokens, not information


def trend(s: Series, steady: BoolArray) -> list[Finding]:
    idx = np.flatnonzero(steady)
    if len(idx) < 8 * 60:  # less than ~8 steady hours: too little to call a trend
        return []
    base = idx[: len(idx) // 4]  # first quarter of the steady minutes
    recent = idx[-max(len(idx) // 10, 60) :]  # last tenth (at least an hour)
    shift = float(np.median(s.values[recent]) - np.median(s.values[base]))
    if abs(shift) < TREND_SHARE * s.band:
        return []
    return [
        Finding(
            sensor=s.name,
            kind=FindingKind.TREND,
            start=_t(s.times, int(base[0])),
            end=_t(s.times, int(recent[-1])),
            magnitude=_pct(shift, s.band),
            summary=(
                f"{s.name} median moved {'up' if shift > 0 else 'down'} by {abs(shift):.3g} "
                f"{s.unit} ({_pct(shift, s.band):+.0f} % of its normal band) between the start "
                "and the end of the window, comparing steady running only"
            ),
        )
    ]


def moves_when_stopped(s: Series, settled_stop: BoolArray) -> list[Finding]:
    idx = np.flatnonzero(settled_stop)
    if len(idx) < 4 * 60:  # needs hours of standstill (machines running 24/7 have none)
        return []
    early, late = idx[: len(idx) // 4], idx[-(len(idx) // 4) :]
    shift = float(np.median(s.values[late]) - np.median(s.values[early]))
    if abs(shift) < TREND_SHARE * s.band:
        return []
    return [
        Finding(
            sensor=s.name,
            kind=FindingKind.MOVES_WHEN_STOPPED,
            start=_t(s.times, int(early[0])),
            end=_t(s.times, int(late[-1])),
            magnitude=_pct(shift, s.band),
            summary=(
                f"{s.name} reads {abs(shift):.3g} {s.unit} {'higher' if shift > 0 else 'lower'} "
                "during later standstills than during earlier ones, although the machine was "
                "stopped and settled each time"
            ),
        )
    ]


def _deviation(s: Series) -> FloatArray:
    """Each point minus the level of the SIDE minutes before it, in units of the normal band."""
    before, _ = _side_medians(s.values, SIDE)
    return (s.values - before) / s.band


def current_up_speed_down(current: Series, speed: Series, steady: BoolArray) -> list[Finding]:
    """The overload signature: the drive draws more current WHILE it loses speed. A load change
    moves both the same way; only a struggling drive moves them apart."""
    cur, spd = _deviation(current), _deviation(speed)
    hot = steady & (np.nan_to_num(cur) >= OVERLOAD_CURRENT_UP)
    hot &= np.nan_to_num(spd) <= -OVERLOAD_SPEED_DOWN
    runs = _runs(hot, SPIKE_MIN)
    if not runs:
        return []
    minutes = sum(b - a + 1 for a, b in runs)
    worst = max(runs, key=lambda r: float(np.max(cur[r[0] : r[1] + 1])))
    return [
        Finding(
            sensor=current.name,
            kind=FindingKind.CURRENT_UP_SPEED_DOWN,
            start=_t(current.times, runs[0][0]),
            end=_t(current.times, runs[-1][1]),
            magnitude=_pct(
                float(np.max(cur[worst[0] : worst[1] + 1])) * current.band, current.band
            ),
            summary=(
                f"{len(runs)} episode(s), {minutes} min in total, where {current.name} rose while "
                f"{speed.name} fell (normally they move together)"
            ),
        )
    ]


def _drop_load_changes(
    findings: list[Finding], current: Series, speed: Series | None
) -> list[Finding]:
    """Remove spikes that are a normal LOAD change, by the rule in our own manuals
    (MAN-CNC-001 §5.4, MAN-IMM-001 §5.1): current and speed moving TOGETHER in the same direction
    = more (or less) demand. Measured from the DATA in the spike's window, not from other
    findings (a first version paired spike findings and broke whenever the speed's own jump fell
    just below the spike threshold). In a load-change window every spike goes: pressures follow
    the load too. Machines without a speed sensor are left as they are."""
    if speed is None:
        return findings
    cur, spd = _deviation(current), _deviation(speed)
    index = {t: i for i, t in enumerate(current.times.astype("datetime64[s]").tolist())}

    def is_load_change(f: Finding) -> bool:
        a = index[f.start.replace(tzinfo=None)]
        b = index[f.end.replace(tzinfo=None)] + 1
        c, r = float(np.nanmean(cur[a:b])), float(np.nanmean(spd[a:b]))
        both_moved = min(abs(c), abs(r)) >= LOAD_CHANGE_SHARE
        return both_moved and (c > 0) == (r > 0)

    return [f for f in findings if f.kind is not FindingKind.SPIKE or not is_load_change(f)]


class CheckNotPossible(BaseModel):
    check: FindingKind
    reason: str


class Analysis(BaseModel):
    findings: list[Finding]
    minutes_running: int
    minutes_stopped: int
    # A check that COULDN'T run is not a check that found nothing (Run 2/3, F3): a model read the
    # missing moves_when_stopped on a 24/7 compressor as evidence against drift.
    checks_not_possible: list[CheckNotPossible]


TREND_MIN_STEADY = 8 * 60  # minutes of steady running a trend needs (see trend())
STOPPED_MIN_SETTLED = 4 * 60  # minutes of settled standstill moves_when_stopped needs


def analyze(series: Sequence[Series]) -> Analysis:
    """All findings for one machine, sorted by sensor and kind, plus what couldn't be checked.
    Needs the machine's current sensor (kind CURRENT) to infer when it ran."""
    current = next((s for s in series if s.kind is SensorKind.CURRENT), None)
    if current is None:
        raise ValueError("analyze() needs the machine's current sensor to infer its state")
    running = current.values >= current.normal_min
    lasted = minutes_since_change(running)
    steady = running & (lasted >= WARM_UP)
    settled_stop = ~running & (lasted >= SETTLED)

    findings: list[Finding] = []
    for s in series:
        if len(s.values) != len(current.values) or (s.times != current.times).any():
            raise ValueError(f"{s.name}: not on the same minute grid as {current.name}")
        findings += out_of_band(s, running, steady) + spikes(s, steady) + trend(s, steady)
        findings += moves_when_stopped(s, settled_stop)

    speed = next((s for s in series if s.kind is SensorKind.RPM), None)
    findings = _drop_load_changes(findings, current, speed)
    if speed is not None:
        findings += current_up_speed_down(current, speed, steady)

    not_possible = []
    if int(steady.sum()) < TREND_MIN_STEADY:
        not_possible.append(
            CheckNotPossible(
                check=FindingKind.TREND,
                reason=f"only {int(steady.sum())} min of steady running in the window "
                f"(needs {TREND_MIN_STEADY}): no trend can be judged",
            )
        )
    if int(settled_stop.sum()) < STOPPED_MIN_SETTLED:
        not_possible.append(
            CheckNotPossible(
                check=FindingKind.MOVES_WHEN_STOPPED,
                reason=f"only {int(settled_stop.sum())} min of settled standstill in the window "
                f"(needs {STOPPED_MIN_SETTLED}): a lying sensor can't be told from the process "
                "this way; no finding here is NOT evidence against sensor drift",
            )
        )
    if speed is None:
        not_possible.append(
            CheckNotPossible(
                check=FindingKind.CURRENT_UP_SPEED_DOWN,
                reason="this machine type has no speed sensor",
            )
        )

    flagged = {f.sensor for f in findings}
    return Analysis(
        findings=sorted(
            (f.model_copy(update={"isolated": flagged == {f.sensor}}) for f in findings),
            key=lambda f: (f.sensor, f.kind.value, f.start),
        ),
        minutes_running=int(running.sum()),
        minutes_stopped=int((~running).sum()),
        checks_not_possible=not_possible,
    )
