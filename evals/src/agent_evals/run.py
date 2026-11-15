"""Run the eval suite (8.3). 8.3a: --dry-run only, which costs 0 LLM requests.

    cd evals
    uv run python -m agent_evals.evaldb                 # once: the eval database
    uv run python -m agent_evals.run --dry-run          # set up every scenario, no agent
    uv run python -m agent_evals.run --dry-run --tags smoke --limit 3

Per scenario: reset the machine (erase earlier scenarios' faults and their history), inject the
scenario's fault, and report what the DATA now shows: alarms raised, tripped or not. A dry run
checks the scenarios themselves before any request is spent: a real fault that raises nothing,
or a control machine that alarms on its own, is worth knowing about first.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg

from agent_evals.evaldb import BACKFILL_DAYS, EVAL_NOW, SEED, eval_plant, use_eval_db
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.plant import PlantConfig
from app.rag.corpus import load_corpus
from simulator.db import connect
from simulator.runner import inject, reset_machine

CONTROL_LOOKBACK = timedelta(days=1)  # a control has no fault start: count the last day's alarms


@dataclass(frozen=True)
class Prepared:
    """What one scenario's setup left in the data (before the agent looks at it)."""

    scenario: Scenario
    since: datetime  # alarms are counted from here: the fault's start, or a day back
    alarms: Counter[str] = field(default_factory=Counter)
    tripped: bool = False
    status: str = ""

    def warning(self) -> str | None:
        """A scenario whose data may not support its expectation."""
        if self.scenario.setup.fault is not None and not self.alarms:
            return "fault raised no alarm (only the readings show it)"
        if self.scenario.setup.fault is None and self.alarms:
            return f"control machine alarms on its own: {dict(self.alarms)}"
        return None


def select(scenarios: Sequence[Scenario], tags: set[str], limit: int | None) -> list[Scenario]:
    """Scenarios carrying ALL `tags` (none = all), in id order, at most `limit`."""
    chosen = [s for s in scenarios if tags <= set(s.tags)]
    return chosen[:limit] if limit is not None else chosen


def prepare(conn: psycopg.Connection, plant: PlantConfig, s: Scenario, now: datetime) -> Prepared:
    """Reset the machine, inject the scenario's fault, read back what the data shows."""
    machine = s.setup.machine
    reset_machine(conn, plant, machine, seed=SEED, days=BACKFILL_DAYS, now=now)
    since, tripped = now - CONTROL_LOOKBACK, False
    if s.setup.fault is not None:
        result = inject(
            conn,
            plant,
            s.setup.fault,
            machine,
            seed=SEED,
            duration=s.setup.duration,
            severity=s.setup.severity,
            sensor=s.setup.sensor,
            now=now,
        )
        since, tripped = result.fault.start, result.tripped
    rows = conn.execute(
        "SELECT a.code, count(*) FROM alarms a JOIN machines m ON m.id = a.machine_id"
        " WHERE m.code = %s AND a.time >= %s GROUP BY a.code ORDER BY a.code",
        (machine, since),
    ).fetchall()
    status = conn.execute("SELECT status FROM machines WHERE code = %s", (machine,)).fetchone()
    return Prepared(
        scenario=s,
        since=since,
        alarms=Counter({str(code): int(n) for code, n in rows}),
        tripped=tripped,
        status=str(status[0]) if status else "?",
    )


def format_row(p: Prepared) -> str:
    s = p.scenario
    fault = f"{s.setup.fault} x{s.setup.severity}" if s.setup.fault else "-"
    alarms = ", ".join(f"{code} {n}" for code, n in sorted(p.alarms.items())) or "none"
    trip = " TRIPPED" if p.tripped else ""
    return f"{s.id:<48} {s.setup.machine}  {fault:<26} {p.status:<8}{trip:<8} {alarms}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_evals.run", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="set up the scenarios, don't run the agent"
    )
    parser.add_argument("--tags", default="", help="comma-separated; scenarios with ALL of them")
    parser.add_argument("--limit", type=int, help="at most this many scenarios")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.dry_run:
        print("running the agent comes in 8.3b; use --dry-run", file=sys.stderr)
        return 2

    settings = use_eval_db()  # BEFORE anything connects: the guard for the destructive reset
    plant = eval_plant(settings)
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    tags = {t.strip() for t in args.tags.split(",") if t.strip()}
    scenarios = select(load_scenarios(SCENARIOS, plant, doc_ids), tags, args.limit)

    warnings: list[str] = []
    with connect(settings) as conn:
        for s in scenarios:
            p = prepare(conn, plant, s, EVAL_NOW)  # the eval clock, not the real time
            print(format_row(p), flush=True)
            if w := p.warning():
                warnings.append(f"{s.id}: {w}")
    print(f"\n{len(scenarios)} scenarios set up, 0 LLM requests")
    for w in warnings:
        print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
