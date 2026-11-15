"""The eval database (8.3a): a separate Postgres database, so evals never touch the dev data.

    cd evals
    uv run python -m agent_evals.evaldb        # create + migrate + seed + backfill + ingest

Why separate: every scenario creates an incident, an agent run and often a draft work order,
and resets (= rewrites) a machine's history. On the dev database that would fill the approval
queue with ~50 eval drafts and change the data you click through. Same Postgres container,
another database: still $0.

How the switch works: use_eval_db() sets DATABASE_URL in this process's environment (environment
variables beat .env in pydantic-settings) and clears the settings cache. Everything started
afterwards reads it: the API code in this process AND the MCP servers it spawns as subprocesses
(they inherit the environment). The URL holds the DB password: it is never printed.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

import app
from app.config import Settings, get_settings
from app.db.session import create_engine
from app.plant import PlantConfig, ShiftPattern, load_plant
from app.rag.corpus import load_corpus
from app.rag.embeddings import build_embedder
from app.rag.ingest import ingest
from app.runtime import run, use_system_certificates
from app.seed import seed
from simulator.cli import DEFAULT_SEED
from simulator.db import connect
from simulator.runner import backfill

EVAL_DB = "fabpilot_eval"
# History per machine; reset_machine regenerates the same span (the longest default fault
# duration is 3 days, so every scenario's fault and its lead-up fit with room to spare).
BACKFILL_DAYS = 7
SEED = DEFAULT_SEED
# The eval CLOCK: the eval world ends here, a Tuesday mid-shift (13:00 Berlin). Not the real
# 'now': the plant runs Mon-Fri 06-22, and faults like belt slippage only act while a machine
# RUNS, so a Sunday eval would find nothing (the 8.3a dry run did). Fixed, so the same seed
# gives byte-identical scenario data on every run, any day, any machine: result differences
# come from the model or the prompt, never from the calendar. The agent gets it as its clock.
EVAL_NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
# Pinned with the clock: the demo's plant.yaml may say `continuous`, the eval world must not
# change with it (the 8.3a dry run was checked against two shifts).
EVAL_SHIFTS = ShiftPattern.TWO_SHIFT_WEEKDAYS
API_DIR = Path(app.__file__).resolve().parents[1]  # alembic.ini + migrations/ live there


class NotEvalDatabase(RuntimeError):
    """Raised before anything destructive runs against a database that isn't the eval one."""


def eval_plant(settings: Settings) -> PlantConfig:
    """plant.yaml with the eval world's shift pattern, whatever the demo uses."""
    return load_plant(settings.plant_file).model_copy(update={"shifts": EVAL_SHIFTS})


def database_name(settings: Settings) -> str:
    return str(make_url(str(settings.database_url)).database)


def require_eval_db(settings: Settings) -> None:
    """The guard in front of every destructive step (reset_machine erases ground truth)."""
    name = database_name(settings)
    if name != EVAL_DB:
        raise NotEvalDatabase(f"refusing to run against database {name!r}, expected {EVAL_DB!r}")


def eval_url(settings: Settings) -> str:
    """The configured URL with the database name replaced (same host, user, driver)."""
    url = make_url(str(settings.database_url)).set(database=EVAL_DB)
    return url.render_as_string(hide_password=False)


def use_eval_db() -> Settings:
    """Point this process (and its subprocesses) at the eval database. Returns its settings."""
    if database_name(get_settings()) != EVAL_DB:
        os.environ["DATABASE_URL"] = eval_url(get_settings())
        get_settings.cache_clear()
    settings = get_settings()
    require_eval_db(settings)
    return settings


def create_database(dev: Settings) -> bool:
    """CREATE DATABASE fabpilot_eval if it doesn't exist. True if it was created."""
    admin = make_url(str(dev.database_url)).set(drivername="postgresql")
    # CREATE DATABASE can't run inside a transaction block -> autocommit
    with psycopg.connect(
        admin.render_as_string(hide_password=False), autocommit=True, connect_timeout=5
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (EVAL_DB,))
        if exists.fetchone():
            return False
        conn.execute(f'CREATE DATABASE "{EVAL_DB}"')  # a constant, not user input
    return True


def migrate(settings: Settings) -> None:
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = str(settings.database_url)  # env.py prefers this
    command.upgrade(cfg, "head")


async def _seed_and_ingest(settings: Settings) -> tuple[int, int]:
    plant = eval_plant(settings)
    docs = load_corpus(settings.docs_dir)
    engine = create_engine(settings)
    try:
        seeded = await seed(engine, plant)
        embedder = build_embedder(settings.embedding_model, settings.embedding_cache_dir)
        report = await ingest(engine, docs, embedder, settings.chunk_max_tokens)
    finally:
        await engine.dispose()
    return seeded.machines, len(report.added) + len(report.updated) + len(report.skipped)


def truncate_after(conn: psycopg.Connection, end: datetime) -> int:
    """Delete readings and alarms after the eval clock (e.g. from a setup that used the real
    time): the agent must not find data from "the future". Returns rows deleted."""
    with conn.transaction():
        readings = conn.execute("DELETE FROM sensor_readings WHERE time > %s", (end,)).rowcount
        alarms = conn.execute("DELETE FROM alarms WHERE time > %s", (end,)).rowcount
    return readings + alarms


def setup() -> None:
    """Idempotent: safe to run again (existing database, migrations, seed, unchanged docs)."""
    created = create_database(get_settings())
    settings = use_eval_db()
    print(f"database {EVAL_DB} {'created' if created else 'exists'}", flush=True)
    migrate(settings)
    use_system_certificates()  # the embedding model may need a download (3.2b)
    machines, docs = run(_seed_and_ingest(settings))
    print(f"seeded {machines} machines, {docs} documents indexed", flush=True)
    plant = eval_plant(settings)
    with connect(settings) as c:
        rows = backfill(c, plant, days=BACKFILL_DAYS, seed=SEED, now=EVAL_NOW)
        late = truncate_after(c, EVAL_NOW)
    print(f"backfilled {BACKFILL_DAYS} days up to {EVAL_NOW:%Y-%m-%d %H:%M} UTC: {rows} readings")
    if late:
        print(f"removed {late} readings + alarms after the eval clock")


if __name__ == "__main__":
    # print, not logging: Alembic's fileConfig (alembic.ini) sets the root logger to WARN
    setup()
