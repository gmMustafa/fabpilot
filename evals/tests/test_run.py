"""The eval database guard and the dry run (8.3a). No LLM.

The integration tests use a throwaway database (app.testing), never fabpilot_eval or the dev DB.
"""

import asyncio
from collections import Counter
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import create_async_engine

from agent_evals import evaldb
from agent_evals.run import Prepared, prepare, select
from agent_evals.scenario import Scenario
from app.config import Settings, get_settings
from app.plant import PlantConfig
from app.seed import seed
from app.testing import (  # noqa: F401  (pytest fixtures, used by name)
    alembic_cfg,
    migrated_db_url,
    sqlalchemy_url,
    temp_db_name,
)
from simulator.db import connect
from simulator.runner import backfill
from simulator.signals import utc

NOW = utc(2026, 11, 17, 12)  # a Tuesday, mid-shift


def scenario(
    id: str, machine: str, fault: str | None = None, tags: list[str] | None = None
) -> Scenario:
    expect = (
        {"category": "machine_fault", "root_cause_keywords": [["bearing"]]}
        if fault
        else {"category": ["no_fault", "unknown"], "work_order": "forbidden"}
    )
    setup = {"machine": machine} | ({"fault": fault} if fault else {})
    return Scenario.model_validate(
        {
            "id": id,
            "description": "a test scenario",
            "setup": setup,
            "question": "What is wrong?",
            "expect": expect,
            "tags": tags or [],
        }
    )


def settings_for(database: str) -> Settings:
    return get_settings().model_copy(update={"database_url": PostgresDsn(sqlalchemy_url(database))})


# ---------------------------------------------------------------- the guard


def test_guard_refuses_any_other_database() -> None:
    with pytest.raises(evaldb.NotEvalDatabase, match="fabpilot_eval"):
        evaldb.require_eval_db(settings_for("fabpilot"))
    evaldb.require_eval_db(settings_for(evaldb.EVAL_DB))  # no error


def test_eval_url_changes_only_the_database_name() -> None:
    dev = settings_for("fabpilot")
    url = evaldb.eval_url(dev)
    assert url.endswith(f"/{evaldb.EVAL_DB}")
    assert url.rsplit("/", 1)[0] == sqlalchemy_url("fabpilot").rsplit("/", 1)[0]


def test_use_eval_db_switches_the_whole_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", sqlalchemy_url("fabpilot"))
    get_settings.cache_clear()
    try:
        settings = evaldb.use_eval_db()
        assert evaldb.database_name(settings) == evaldb.EVAL_DB
        assert evaldb.database_name(get_settings()) == evaldb.EVAL_DB  # what everything else reads
    finally:
        monkeypatch.undo()  # restore DATABASE_URL BEFORE the cache is rebuilt
        get_settings.cache_clear()


# ---------------------------------------------------------------- selecting and warnings


def test_select_by_all_tags_then_limit() -> None:
    a = scenario("a", "M-01", "bearing_wear", tags=["smoke", "typical"])
    b = scenario("b", "M-02", "bearing_wear", tags=["typical"])
    c = scenario("c", "M-03", tags=["smoke", "control"])
    assert select([a, b, c], {"smoke"}, None) == [a, c]
    assert select([a, b, c], {"smoke", "typical"}, None) == [a]
    assert select([a, b, c], set(), 2) == [a, b]


def test_warnings_flag_scenarios_the_data_may_not_support() -> None:
    silent = Prepared(scenario("a", "M-01", "bearing_wear"), since=NOW)
    noisy = Prepared(scenario("c", "M-03"), since=NOW, alarms=Counter({"TEMP-HIGH": 1}))
    assert silent.warning() == "fault raised no alarm (only the readings show it)"
    assert noisy.warning() is not None and "TEMP-HIGH" in noisy.warning()  # type: ignore[operator]
    assert Prepared(scenario("d", "M-03"), since=NOW).warning() is None


# ---------------------------------------------------------------- isolation, end to end


@pytest.fixture
def conn(
    migrated_db_url: str,  # noqa: F811  (the imported fixtures, injected by name)
    temp_db_name: str,  # noqa: F811
) -> Iterator[tuple[psycopg.Connection, PlantConfig]]:
    plant = evaldb.eval_plant(get_settings())

    async def _seed() -> None:
        engine = create_async_engine(migrated_db_url)
        try:
            await seed(engine, plant)
        finally:
            await engine.dispose()

    asyncio.run(_seed(), loop_factory=asyncio.SelectorEventLoop)
    with connect(settings_for(temp_db_name)) as c:
        backfill(c, plant, days=evaldb.BACKFILL_DAYS, seed=evaldb.SEED, now=NOW)
        yield c, plant


@pytest.mark.integration
def test_a_control_after_a_fault_scenario_sees_a_healthy_machine(
    conn: tuple[psycopg.Connection, PlantConfig],
) -> None:
    c, plant = conn
    fault = prepare(c, plant, scenario("f", "M-01", "bearing_wear"), NOW)
    assert fault.alarms["VIB-HIGH"] > 0
    assert fault.since == NOW - timedelta(days=3)  # bearing wear's default duration

    control = prepare(c, plant, scenario("c", "M-01"), NOW + timedelta(minutes=5))
    assert "VIB-HIGH" not in control.alarms  # the previous scenario's fault is gone
    assert c.execute("SELECT count(*) FROM sim.fault_injections").fetchone() == (0,)
