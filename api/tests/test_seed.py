"""Seed script against a throwaway, fully migrated database."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.db.models import Machine, MachineStatus, Sensor
from app.plant import PlantConfig, load_plant
from app.seed import seed

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def plant() -> PlantConfig:
    return load_plant(get_settings().plant_file)


async def _counts(engine: AsyncEngine) -> tuple[int, int]:
    async with engine.connect() as conn:
        machines = await conn.scalar(select(func.count()).select_from(Machine))
        sensors = await conn.scalar(select(func.count()).select_from(Sensor))
    return machines or 0, sensors or 0


async def test_seed_is_idempotent(engine: AsyncEngine, plant: PlantConfig) -> None:
    expected_sensors = sum(len(plant.sensors_for(m)) for m in plant.machines)

    first = await seed(engine, plant)
    second = await seed(engine, plant)

    assert first == second
    assert await _counts(engine) == (len(plant.machines), expected_sensors)


async def test_reseed_updates_config_but_keeps_runtime_status(
    engine: AsyncEngine, plant: PlantConfig
) -> None:
    await seed(engine, plant)
    # Runtime state changes in the plant: M-01 breaks down.
    async with engine.begin() as conn:
        await conn.execute(
            update(Machine).where(Machine.code == "M-01").values(status=MachineStatus.DOWN)
        )

    # Someone tunes a range in plant.yaml and re-seeds.
    data = plant.model_dump()
    data["sensor_templates"]["cnc_mill"][0]["normal_max"] = 58.0  # spindle_bearing_temp
    await seed(engine, PlantConfig.model_validate(data))

    async with engine.connect() as conn:
        status = await conn.scalar(select(Machine.status).where(Machine.code == "M-01"))
        new_max = await conn.scalar(
            text(
                "SELECT s.normal_max FROM sensors s JOIN machines m ON m.id = s.machine_id"
                " WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            )
        )
    assert status == MachineStatus.DOWN  # seed must not "repair" a broken machine
    assert new_max == 58.0  # but config changes do land


async def test_reseed_never_changes_unit_of_existing_sensor(
    engine: AsyncEngine, plant: PlantConfig
) -> None:
    """unit/kind give meaning to stored readings, so a re-seed must not rewrite them."""
    await seed(engine, plant)
    data = plant.model_dump()
    data["sensor_templates"]["cnc_mill"][0]["unit"] = "°F"  # spindle_bearing_temp
    await seed(engine, PlantConfig.model_validate(data))

    async with engine.connect() as conn:
        unit = await conn.scalar(
            text(
                "SELECT s.unit FROM sensors s JOIN machines m ON m.id = s.machine_id"
                " WHERE m.code = 'M-01' AND s.name = 'spindle_bearing_temp'"
            )
        )
    assert unit == "°C"
