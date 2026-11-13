"""Backfill and live modes, as plain functions (the CLI is a thin wrapper; tests call these)."""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import psycopg

from app.db.models import MachineStatus
from app.plant import PlantConfig
from simulator.db import SensorBatch, last_readings, sensor_ids, update_status, write_readings
from simulator.signals import DT_SECONDS, OpState, simulate_machine

logger = logging.getLogger(__name__)

SensorKey = tuple[str, str]  # (machine code, sensor name)


def floor_to_grid(t: datetime, dt: int = DT_SECONDS) -> datetime:
    return datetime.fromtimestamp(int(t.timestamp()) // dt * dt, UTC)


@dataclass
class WindowResult:
    rows: int = 0
    last: dict[SensorKey, float] = field(default_factory=dict)  # final value per sensor
    statuses: dict[str, MachineStatus] = field(default_factory=dict)


def write_window(
    conn: psycopg.Connection,
    plant: PlantConfig,
    start: datetime,
    end: datetime,
    seed: int,
    initial: dict[SensorKey, float] | None = None,
) -> WindowResult:
    """Simulate every machine over [start, end), write it, update running/idle status."""
    ids = sensor_ids(conn)
    initial = initial or {}
    result = WindowResult()
    batches: list[SensorBatch] = []
    for machine in plant.machines:
        sensors = plant.sensors_for(machine)
        machine_initial = {
            s.name: initial[(machine.code, s.name)]
            for s in sensors
            if (machine.code, s.name) in initial
        }
        sim = simulate_machine(machine, sensors, start, end, seed, initial=machine_initial)
        if len(sim.times) == 0:
            continue
        for s in sensors:
            batches.append(SensorBatch(ids[(machine.code, s.name)], sim.times, sim.values[s.name]))
            result.last[(machine.code, s.name)] = float(sim.values[s.name][-1])
        running = sim.states[-1] == OpState.RUNNING
        result.statuses[machine.code] = MachineStatus.RUNNING if running else MachineStatus.IDLE
    result.rows = write_readings(conn, batches, start, end)
    update_status(conn, result.statuses)
    return result


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
    result = write_window(conn, plant, state.cursor, end, seed, initial=state.last)
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
