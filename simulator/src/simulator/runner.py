"""Backfill and live modes, as plain functions (the CLI is a thin wrapper; tests call these)."""

import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from app.db.models import FaultType, MachineStatus
from app.plant import PlantConfig
from simulator.alarms import AlarmEvent, detect_alarms
from simulator.db import (
    SensorBatch,
    clear_faults,
    faults_overlapping,
    last_readings,
    record_fault,
    sensor_ids,
    update_status,
    write_alarms,
    write_readings,
)
from simulator.faults import DEFAULT_DURATION, Fault, default_drift_sensor, validate
from simulator.signals import DT_SECONDS, OpState, simulate_machine

logger = logging.getLogger(__name__)

SensorKey = tuple[str, str]  # (machine code, sensor name)


def floor_to_grid(t: datetime, dt: int = DT_SECONDS) -> datetime:
    return datetime.fromtimestamp(int(t.timestamp()) // dt * dt, UTC)


@dataclass
class WindowResult:
    rows: int = 0
    alarms: int = 0
    last: dict[SensorKey, float] = field(default_factory=dict)  # final value per sensor
    statuses: dict[str, MachineStatus] = field(default_factory=dict)


def write_window(
    conn: psycopg.Connection,
    plant: PlantConfig,
    start: datetime,
    end: datetime,
    seed: int,
    initial: dict[SensorKey, float] | None = None,
    machines: Collection[str] | None = None,
    with_alarms: bool = True,
) -> WindowResult:
    """Simulate machines over [start, end) incl. every fault active in that window; write the
    readings (+ alarms) and update status. One transaction: all of it lands, or none of it.

    `machines`: only these codes (inject regenerates just the faulted machine). Default: all.
    `with_alarms`: live ticks pass False (see run_live for why).
    """
    ids = sensor_ids(conn)
    initial = initial or {}
    faults = faults_overlapping(conn, start, end)
    selected = [m for m in plant.machines if machines is None or m.code in machines]
    result = WindowResult()
    batches: list[SensorBatch] = []
    alarms: list[AlarmEvent] = []
    for machine in selected:
        sensors = plant.sensors_for(machine)
        machine_initial = {
            s.name: initial[(machine.code, s.name)]
            for s in sensors
            if (machine.code, s.name) in initial
        }
        sim = simulate_machine(
            machine, sensors, start, end, seed, initial=machine_initial, faults=faults
        )
        if len(sim.times) == 0:
            continue
        for s in sensors:
            batches.append(SensorBatch(ids[(machine.code, s.name)], sim.times, sim.values[s.name]))
            result.last[(machine.code, s.name)] = float(sim.values[s.name][-1])
        result.statuses[machine.code] = STATUS_OF[OpState(int(sim.states[-1]))]
        if with_alarms:
            alarms += detect_alarms(machine.code, sensors, sim)
    with conn.transaction():
        result.rows = write_readings(conn, batches, start, end)
        if with_alarms:
            result.alarms = write_alarms(conn, alarms, [m.code for m in selected], start, end)
        update_status(conn, result.statuses)
    return result


STATUS_OF = {
    OpState.RUNNING: MachineStatus.RUNNING,
    OpState.IDLE: MachineStatus.IDLE,
    OpState.OFF: MachineStatus.IDLE,
    OpState.DOWN: MachineStatus.DOWN,
}


@dataclass(frozen=True)
class InjectResult:
    fault_id: int
    fault: Fault
    rows: int
    alarms: int
    tripped: bool


def inject(
    conn: psycopg.Connection,
    plant: PlantConfig,
    fault_type: FaultType,
    machine_code: str,
    seed: int,
    duration: timedelta | None = None,
    start: datetime | None = None,
    severity: float = 1.0,
    sensor: str | None = None,
    now: datetime | None = None,
) -> InjectResult:
    """Inject a fault and rewrite that machine's data from the fault's start until now.

    Default timing (decision D3): the fault develops over its typical duration and is fully
    developed NOW, so the data to investigate is already there ("why did Line 3 stop?").
    Ground truth + readings + alarms + status in ONE transaction.
    """
    machine = next((m for m in plant.machines if m.code == machine_code), None)
    if machine is None:
        raise ValueError(f"unknown machine {machine_code!r}")
    sensors = plant.sensors_for(machine)
    if fault_type is FaultType.SENSOR_DRIFT and sensor is None:
        sensor = default_drift_sensor(sensors)
    current = floor_to_grid(now or datetime.now(UTC))
    # Include the current grid point: a fault fully developed NOW trips NOW, and an exclusive
    # end would cut off exactly that sample (overheating spindle, but no trip in the data).
    end = current + timedelta(seconds=DT_SECONDS)
    duration = duration or DEFAULT_DURATION[fault_type]
    fault_start = floor_to_grid(start) if start else current - duration
    fault = Fault(fault_type, machine_code, fault_start, fault_start + duration, severity, sensor)
    validate(fault, machine, sensors)  # fail before writing anything
    with conn.transaction():
        fault_id = record_fault(conn, fault, seed)
        window = write_window(conn, plant, fault.start, end, seed, machines=[machine_code])
    return InjectResult(
        fault_id=fault_id,
        fault=fault,
        rows=window.rows,
        alarms=window.alarms,
        tripped=window.statuses.get(machine_code) is MachineStatus.DOWN,
    )


def clear(conn: psycopg.Connection, machine_code: str, now: datetime | None = None) -> int:
    """The maintenance team fixed it: active faults end now, the machine goes back in service."""
    return clear_faults(conn, machine_code, floor_to_grid(now or datetime.now(UTC)))


def backfill(
    conn: psycopg.Connection, plant: PlantConfig, days: int, seed: int, now: datetime | None = None
) -> int:
    """History from midnight UTC `days` ago until now. Idempotent for the same window + seed."""
    end = floor_to_grid(now or datetime.now(UTC))
    start = end.replace(hour=0, minute=0, second=0) - timedelta(days=days)
    return write_window(conn, plant, start, end, seed).rows


@dataclass
class LiveState:
    """Where live mode is: the next timestamp to write, and the last value per sensor."""

    cursor: datetime
    last: dict[SensorKey, float]


def resume_state(conn: psycopg.Connection, now: datetime) -> LiveState:
    """Continue right after the newest stored reading (catching up on any gap), or start now."""
    by_id = last_readings(conn)
    if not by_id:
        return LiveState(cursor=floor_to_grid(now), last={})
    names = {sid: key for key, sid in sensor_ids(conn).items()}
    newest = max(t for t, _ in by_id.values())
    return LiveState(
        cursor=newest + timedelta(seconds=DT_SECONDS),
        last={names[sid]: v for sid, (_, v) in by_id.items()},
    )


def live_tick(
    conn: psycopg.Connection, plant: PlantConfig, state: LiveState, now: datetime, seed: int
) -> int:
    """Write everything from the cursor up to now (one tick, or a catch-up after downtime)."""
    end = floor_to_grid(now) + timedelta(seconds=DT_SECONDS)  # include the current grid point
    if end <= state.cursor:
        return 0
    # with_alarms=False: a 10 s tick is shorter than the alarm debounce, so per-tick detection
    # would never raise anything. Live alarming needs streaming state (or a look-back window);
    # until then, backfill and inject produce the alarm log. Faults ARE applied live.
    result = write_window(
        conn, plant, state.cursor, end, seed, initial=state.last, with_alarms=False
    )
    state.cursor = end
    state.last = result.last  # continue lagged signals from here, no DB round-trip needed
    return result.rows


def run_live(
    conn: psycopg.Connection, plant: PlantConfig, seed: int, interval: float = DT_SECONDS
) -> int:
    """Loop until Ctrl+C: one tick per `interval` seconds. Returns readings written."""
    state = resume_state(conn, datetime.now(UTC))
    if (gap := datetime.now(UTC) - state.cursor) > timedelta(seconds=2 * DT_SECONDS):
        logger.info("Catching up %s of missing data since %s", gap, state.cursor.isoformat())
    total = 0
    try:
        while True:
            if rows := live_tick(conn, plant, state, datetime.now(UTC), seed):
                total += rows
                logger.info("tick: %d readings up to %s", rows, state.cursor.isoformat())
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopped. %d readings written this session.", total)
    return total
