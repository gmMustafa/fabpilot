"""Seed machines + sensors from data/plant.yaml. Idempotent: safe to run any number of times.

uv run python -m app.seed
"""

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.config import get_settings
from app.db.models import Machine, Sensor
from app.db.session import create_engine
from app.plant import PlantConfig, load_plant
from app.runtime import run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeedResult:
    machines: int
    sensors: int


async def upsert_machines(conn: AsyncConnection, plant: PlantConfig) -> dict[str, int]:
    """Insert or update every machine by its code. Returns {code: id} for the sensor upsert."""
    rows = [
        {
            "code": m.code,
            "name": m.name,
            "line": m.line,
            "type": m.type,
            "install_date": m.install_date,
        }
        for m in plant.machines
    ]
    insert_stmt = insert(Machine).values(rows)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        # Which unique constraint decides "this machine already exists": uq_machines_code.
        index_elements=[Machine.code],
        # `excluded` = the row we tried to insert. Copy the config-owned columns from it.
        # NOT `status`: that's live runtime state (a machine that is DOWN must stay DOWN when
        # someone re-runs the seed). Config owns the description, the plant owns the state.
        set_={
            "name": insert_stmt.excluded.name,
            "line": insert_stmt.excluded.line,
            "type": insert_stmt.excluded.type,
            "install_date": insert_stmt.excluded.install_date,
        },
    ).returning(Machine.code, Machine.id)
    result = await conn.execute(upsert_stmt)
    return {code: id_ for code, id_ in result}  # SQLAlchemy 2.1: rows unpack like typed tuples


async def upsert_sensors(
    conn: AsyncConnection, plant: PlantConfig, machine_ids: dict[str, int]
) -> int:
    """Insert or update every sensor by (machine, name). Returns the number of sensors."""
    rows: list[dict[str, Any]] = [
        {
            "machine_id": machine_ids[m.code],
            "name": s.name,
            "kind": s.kind,
            "unit": s.unit,
            "normal_min": s.normal_min,
            "normal_max": s.normal_max,
        }
        for m in plant.machines
        for s in plant.sensors_for(m)
    ]
    insert_stmt = insert(Sensor).values(rows)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        index_elements=[Sensor.machine_id, Sensor.name],
        # Only thresholds are re-seedable. kind/unit describe what the STORED readings mean:
        # changing unit °C -> °F on re-seed would silently mislabel months of history.
        # A different kind/unit = a new sensor (new name), not an update.
        set_={
            "normal_min": insert_stmt.excluded.normal_min,
            "normal_max": insert_stmt.excluded.normal_max,
        },
    )
    await conn.execute(upsert_stmt)
    return len(rows)


async def seed(engine: AsyncEngine, plant: PlantConfig) -> SeedResult:
    """Upsert everything in ONE transaction: a failure halfway leaves the DB untouched."""
    async with engine.begin() as conn:  # begin(): commits on success, rolls back on exception
        machine_ids = await upsert_machines(conn, plant)
        n_sensors = await upsert_sensors(conn, plant, machine_ids)
    return SeedResult(machines=len(machine_ids), sensors=n_sensors)


async def _main() -> None:
    settings = get_settings()
    plant = load_plant(settings.plant_file)  # validate BEFORE connecting: bad YAML never half-seeds
    engine = create_engine(settings)
    try:
        result = await seed(engine, plant)
    finally:
        await engine.dispose()
    logger.info("Seeded %d machines, %d sensors", result.machines, result.sensors)


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level, format="%(levelname)s %(message)s")
    run(_main())
