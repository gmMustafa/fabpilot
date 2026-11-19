"""Run the eval suite (8.3): set up each scenario, let the real agent answer, save what it said.

    cd evals
    uv run python -m agent_evals.evaldb                       # once: the eval database
    uv run python -m agent_evals.run --dry-run                # set up every scenario, 0 requests
    uv run python -m agent_evals.run --limit 1                # the agent on one scenario (~4 req)
    uv run python -m agent_evals.run --tags smoke             # the PR subset (~28 requests)
    uv run python -m agent_evals.run --out results/x.jsonl    # resume: skips finished scenarios

Per scenario: reset the machine (erase earlier scenarios' faults and their history), inject the
scenario's fault, then (unless --dry-run) ask the scenario's question through the same
AgentService the API uses, with the EVAL clock, and append one Result line. Grading is 8.3c.

The prompt cache is OFF unless --cache: a cached answer measures nothing new. With --cache a
re-run of the same scenario costs 0 requests (the eval data is identical on every run), which is
how to test the runner and the grader for free.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import psycopg

from agent_evals import manifest, results
from agent_evals.evaldb import BACKFILL_DAYS, EVAL_NOW, SEED, eval_plant, use_eval_db
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.agent.budget import budget_from
from app.agent.checkpoint import pooled_checkpointer
from app.agent.service import AgentService
from app.agent.store import SqlAgentRepo
from app.agent.tools import McpTools
from app.config import Settings, get_settings
from app.db.session import create_engine
from app.gateway.factory import build_gateway
from app.gateway.structured import STRUCTURED_TEMPERATURE
from app.plant import PlantConfig
from app.rag.corpus import load_corpus
from app.runtime import run
from simulator.db import connect
from simulator.runner import inject, reset_machine

CONTROL_LOOKBACK = timedelta(days=1)  # a control has no fault start: count the last day's alarms
RESULTS = Path(__file__).resolve().parents[2] / "results"


@dataclass(frozen=True)
class Prepared:
    """What one scenario's setup left in the data (before the agent looks at it)."""

    scenario: Scenario
    since: datetime  # alarms are counted from here: the fault's start, or a day back
    alarms: Counter[str] = field(default_factory=Counter)
    tripped: bool = False
    status: str = ""

    def warning(self) -> str | None:
        """A scenario whose data may not support its expectation. Quiet where the scenario
        already accepts 'unknown' for that reason (the weak mild variants)."""
        accepts_unknown = "unknown" in self.scenario.expect.category
        if self.scenario.setup.fault is not None and not self.alarms and not accepts_unknown:
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


# ---------------------------------------------------------------- the agent


@asynccontextmanager
async def agent_service(settings: Settings) -> AsyncIterator[AgentService]:
    """The API's AgentService (main.py lifespan), with two differences: the EVAL clock, and one
    run at a time (scenarios share the database: two at once would reset each other's data)."""
    async with AsyncExitStack() as stack:
        engine = create_engine(settings)
        stack.push_async_callback(engine.dispose)
        checkpointer = await stack.enter_async_context(
            pooled_checkpointer(str(settings.database_url), settings.db_connect_timeout)
        )
        llm = build_gateway(settings, engine=engine)
        stack.push_async_callback(llm.aclose)
        yield AgentService(
            SqlAgentRepo(engine),
            llm,
            checkpointer,
            # explicit, not inherited by accident: the MCP servers read the EVAL database
            partial(McpTools, {"DATABASE_URL": str(settings.database_url)}),
            budget=budget_from(settings),
            max_concurrent=1,
            clock=lambda: EVAL_NOW,
        )


async def run_scenario(
    service: AgentService,
    conn: psycopg.Connection,
    plant: PlantConfig,
    s: Scenario,
    settings: Settings,
    run_manifest: manifest.Manifest,
) -> results.Result:
    """Set up, ask, wait for the run to pause (a draft) or end, and record what it produced."""
    # Sync DB work inside the loop: fine for a CLI that runs one scenario at a time.
    prepare(conn, plant, s, EVAL_NOW)
    started_at, t0 = datetime.now(UTC), time.perf_counter()
    incident_id = await service.create_incident(s.setup.machine, s.question)
    handle = await service.start(incident_id)
    if handle.task is not None:
        await handle.task  # the run task never raises: failures end as a "failed" run
    done = next(e for e in reversed(handle.events) if e.event == "done")
    error = next((e.data["error"] for e in handle.events if e.event == "error"), None)
    found = await service.state(handle.thread_id)
    # Runs are sequential, so the gateway's log rows since this run started are this run's.
    calls = conn.execute(
        "SELECT purpose, provider, model, status, error_type, http_status FROM llm_calls"
        " WHERE time >= %s ORDER BY time, attempt_no",
        (started_at,),
    ).fetchall()
    return results.from_state(
        found[0] if found else None,
        scenario_id=s.id,
        status=done.data["status"],
        thread_id=handle.thread_id,
        machine=s.setup.machine,
        question=s.question,
        error=error,
        llm_calls=[
            {
                "purpose": p,
                "provider": pr,
                "model": m,
                "status": st,
                "error_type": et,
                "http_status": hs,
            }
            for p, pr, m, st, et, hs in calls
        ],
        setup=s.setup.model_dump(mode="json"),
        wall_seconds=round(time.perf_counter() - t0, 1),
        cache=settings.llm_cache_enabled,
        models=settings.fallbacks,
        eval_now=EVAL_NOW,
        started_at=started_at,
        manifest=run_manifest,
    )


def summary_line(r: results.Result, spent: int) -> str:
    category = (r.hypothesis or {}).get("category", "-")
    trouble = r.error or r.llm_error
    note = f"  ! {trouble[:80]}" if trouble else ""
    return (
        f"{r.scenario_id:<48} {r.status:<7} {category:<14} "
        f"{r.usage.provider_requests:>2} req  {r.wall_seconds:>5.1f} s  "
        f"(total {spent} req){note}"
    )


def quota_exhausted(r: results.Result) -> bool:
    """Did this run fail because the (single) model's DAILY quota is gone? Then nothing will
    answer before the reset, and every further scenario would only fail the same way.

    Not the same as a 429 per MINUTE: the gateway waits those out (retry-after), and a run
    that still failed on one is an ordinary errored run.
    """
    # Completed runs should never stop further evaluations.
    if r.completed:
        return False

    for c in r.llm_calls:
        # Only failed agent calls count.
        if not str(c.get("purpose", "")).startswith("agent."):
            continue

        if c.get("status") != "error":
            continue

        error_type = c.get("error_type")
        http_status = c.get("http_status")

        # OpenRouter daily quota exhausted.
        if error_type == "QuotaExhaustedError":
            return True

        # Groq model-specific daily quota exhausted.
        if error_type == "ModelUnavailableError" and http_status == 429:
            return True

    return False


@dataclass(frozen=True)
class Outcome:
    done: int  # results saved
    spent: int  # provider requests
    paused: results.Result | None = None  # the run that hit the daily quota (NOT saved)


async def run_loop(
    todo: Sequence[Scenario],
    run_one: Callable[[Scenario], Awaitable[results.Result]],
    save: Callable[[results.Result], None],
) -> Outcome:
    """Run each scenario and save its result before the next starts; stop at a daily quota.
    The quota-hit run isn't saved: it measured the quota, not the agent, and resume (which
    skips only COMPLETED scenarios) runs it again after the reset."""
    done = spent = 0
    for s in todo:
        r = await run_one(s)
        spent += r.usage.provider_requests
        if quota_exhausted(r):
            return Outcome(done, spent, paused=r)
        save(r)
        done += 1
        print(summary_line(r, spent), flush=True)
    return Outcome(done, spent)


async def run_all(
    settings: Settings,
    plant: PlantConfig,
    todo: list[Scenario],
    out: Path,
    run_manifest: manifest.Manifest,
) -> Outcome:
    async with agent_service(settings) as service:
        with connect(settings) as conn:
            return await run_loop(
                todo,
                lambda s: run_scenario(service, conn, plant, s, settings, run_manifest),
                partial(results.append, out),
            )


# ---------------------------------------------------------------- CLI


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_evals.run", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="set up the scenarios, don't run the agent"
    )
    parser.add_argument("--tags", default="", help="comma-separated; scenarios with ALL of them")
    parser.add_argument("--limit", type=int, help="at most this many runs (after resume)")
    parser.add_argument(
        "--scenario", action="append", default=[], help="only this scenario id (repeatable)"
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="completed runs wanted per scenario: free models vary, one run can be luck",
    )
    parser.add_argument("--out", type=Path, help="results file; an existing one is resumed")
    parser.add_argument(
        "--cache", action="store_true", help="allow cached LLM answers (0 requests on a re-run)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # BEFORE the settings are read: the cache switch, then the database guard
    os.environ["LLM_CACHE_ENABLED"] = "true" if args.cache else "false"
    get_settings.cache_clear()
    settings = use_eval_db()
    plant = eval_plant(settings)
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    tags = {t.strip() for t in args.tags.split(",") if t.strip()}
    chosen = select(load_scenarios(SCENARIOS, plant, doc_ids), tags, None)
    if args.scenario:
        unknown = sorted(set(args.scenario) - {s.id for s in chosen})
        if unknown:
            print(f"unknown scenario id(s): {unknown}", file=sys.stderr)
            return 2
        chosen = [s for s in chosen if s.id in args.scenario]

    if args.dry_run:
        warnings: list[str] = []
        with connect(settings) as conn:
            for s in select(chosen, set(), args.limit):
                p = prepare(conn, plant, s, EVAL_NOW)  # the eval clock, not the real time
                print(format_row(p), flush=True)
                if w := p.warning():
                    warnings.append(f"{s.id}: {w}")
        print(f"\n{min(len(chosen), args.limit or len(chosen))} scenarios set up, 0 LLM requests")
        for w in warnings:
            print(f"  ! {w}")
        return 0

    out = args.out or RESULTS / f"run-{datetime.now(UTC):%Y%m%d-%H%M}.jsonl"
    have = results.completed_counts(out)
    # each scenario until it has --repeat COMPLETED runs: a crash is retried, but a finished run
    # is never repeated beyond N (that would be re-rolling until the answer looks good)
    todo = [s for s in chosen for _ in range(max(0, args.repeat - have[s.id]))]
    todo = todo[: args.limit] if args.limit is not None else todo
    done = sum(min(have[s.id], args.repeat) for s in chosen)
    print(
        f"{len(todo)} runs to do, {done} already done in {out.name}; "
        f"cache {'on' if settings.llm_cache_enabled else 'off'}; "
        f"eval clock {EVAL_NOW:%Y-%m-%d %H:%M} UTC"
    )
    # Which models CAN answer, printed before anything runs: a pinned single-model eval whose
    # pin didn't take effect (variables set in another terminal) must be visible here, not
    # discovered afterwards in the cost table.
    print(
        f"routes: {', '.join(settings.fallbacks)} | retries per model {settings.llm_max_retries}, "
        f"attempts per call {settings.llm_max_attempts}"
    )
    # Strict: ONE route. The gateway's fallback keeps the dashboard answering; in an eval it would
    # silently measure two models in one run (day 1 of the baseline did: 3 work orders by Qwen).
    if len(settings.fallbacks) != 1:
        print(
            f"evals pin ONE model: LLM_FALLBACKS has {len(settings.fallbacks)} routes "
            f"({', '.join(settings.fallbacks) or 'none'}). Set exactly one, e.g. "
            "$env:LLM_FALLBACKS='groq:openai/gpt-oss-120b@low'",
            file=sys.stderr,
        )
        return 2
    run_manifest = manifest.build(settings.fallbacks, STRUCTURED_TEMPERATURE)
    dirty = " + uncommitted changes" if run_manifest.git_dirty else ""
    print(
        f"manifest: agent {run_manifest.agent_hash}, temperature {run_manifest.temperature}, "
        f"commit {(run_manifest.git_sha or '?')[:8]}{dirty}"
    )
    outcome = run(run_all(settings, plant, todo, out, run_manifest))
    if outcome.paused is not None:
        # 3: neither done (0) nor broken (1): a script or CI can tell "come back after the reset"
        print(
            f"\nPaused: {outcome.done}/{len(todo)} done, {outcome.spent} provider requests. "
            f"Daily quota of {', '.join(settings.fallbacks)} exhausted at "
            f"{outcome.paused.scenario_id}.\nResume after the reset with the same command "
            f"(--out {out}): finished scenarios are skipped."
        )
        return 3
    print(f"\n{len(todo)} runs, {outcome.spent} provider requests -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
