"""The eval database guard and the dry run (8.3a). No LLM.

The integration tests use a throwaway database (app.testing), never fabpilot_eval or the dev DB.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta

import psycopg
import pytest
from pydantic import PostgresDsn
from sqlalchemy.ext.asyncio import create_async_engine

from agent_evals import evaldb
from agent_evals.manifest import Manifest
from agent_evals.results import Result
from agent_evals.run import Prepared, prepare, quota_exhausted, run_loop, run_scenario, select
from agent_evals.scenario import Scenario
from app.agent.checkpoint import pooled_checkpointer
from app.agent.service import AgentService
from app.agent.store import SqlAgentRepo
from app.agent.tools import FakeTools, ToolCallError, Tools
from app.config import Settings, get_settings
from app.plant import PlantConfig
from app.runtime import run
from app.seed import seed
from app.testing import (  # noqa: F401  (pytest fixtures, used by name)
    ScriptedChat,
    alembic_cfg,
    migrated_db_url,
    sqlalchemy_url,
    temp_db_name,
)
from simulator.db import connect
from simulator.runner import backfill

NOW = evaldb.EVAL_NOW  # the eval clock: a Tuesday, mid-shift

MANIFEST = Manifest(
    routes=("groq:m",), temperature=0.2, agent_hash="abc", git_sha=None, git_dirty=False
)


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


@pytest.mark.integration
def test_run_scenario_records_a_run_without_any_llm_request(
    conn: tuple[psycopg.Connection, PlantConfig],
    temp_db_name: str,  # noqa: F811
) -> None:
    """The runner's whole path through the REAL AgentService (repo, checkpointer, the run task),
    with fake tools: no readings in the window ends 'insufficient' before any LLM call, so the
    empty ScriptedChat fails the test if the agent asks it anything."""
    c, plant = conn
    fake = FakeTools(
        {
            ("sensors", "get_machine"): {
                "code": "M-01",
                "type": "cnc_mill",
                "last_reading_at": NOW.isoformat(),
            },
            ("sensors", "get_alarm_log"): {"alarms": [], "total_count": 0, "truncated": False},
            ("sensors", "detect_anomalies"): ToolCallError("no readings in this window"),
            ("docs", "list_incident_reports"): {"reports": []},
            ("docs", "search_docs"): {"result": []},
        }
    )

    @asynccontextmanager
    async def tools() -> AsyncIterator[Tools]:
        yield fake

    async def go() -> Result:
        url = sqlalchemy_url(temp_db_name)
        engine = create_async_engine(url)
        try:
            async with pooled_checkpointer(url) as checkpointer:
                service = AgentService(
                    SqlAgentRepo(engine), ScriptedChat(), checkpointer, tools, clock=lambda: NOW
                )
                return await run_scenario(
                    service,
                    c,
                    plant,
                    scenario("f", "M-01", "bearing_wear"),
                    settings_for(temp_db_name),
                    MANIFEST,
                )
        finally:
            await engine.dispose()

    r = run(go())
    assert r.manifest == MANIFEST  # every result says what it was measured with
    assert r.status == "done" and r.hypothesis is None
    assert r.usage.provider_requests == 0
    assert r.tools == ["get_machine", "get_alarm_log", "list_incident_reports"]
    assert any("no readings" in e for e in r.evidence_errors)
    # the investigation window comes from the EVAL clock, not the real time
    _, tool, args = fake.calls[1]
    assert tool == "get_alarm_log" and args["end"] <= (NOW + timedelta(minutes=1)).isoformat()


# ---------------------------------------------------------------- stop at a daily quota (8.6-pre)


def attempt(
    error_type: str | None, http_status: int | None, purpose: str = "agent.hypothesize"
) -> dict[str, object]:
    status = "ok" if error_type is None else "error"
    return {
        "purpose": purpose,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "status": status,
        "error_type": error_type,
        "http_status": http_status,
    }


def outcome(sid: str, *calls: dict[str, object], llm_error: str | None = None) -> Result:
    return Result(
        scenario_id=sid,
        status="done",
        thread_id="t",
        machine="M-01",
        question="q",
        wall_seconds=1.0,
        cache=False,
        models=[],
        eval_now=NOW,
        started_at=NOW,
        llm_calls=list(calls),
        llm_error=llm_error,
        usage={"provider_requests": len(calls)},
    )


FAILED = "AllModelsFailedError: all models failed"


def test_quota_openrouter_daily_cap_stops() -> None:
    # the real row (eval DB, 2026-11-15): QuotaExhaustedError, HTTP 429
    r = outcome("a", attempt("QuotaExhaustedError", 429), llm_error=FAILED)
    assert quota_exhausted(r)


def test_quota_groq_daily_cap_stops() -> None:
    # groq.py maps a per-model daily limit to ModelUnavailableError + 429 (not yet seen live)
    r = outcome(
        "a",
        attempt("RateLimitedError", 429),
        attempt("ModelUnavailableError", 429),
        llm_error=FAILED,
    )
    assert quota_exhausted(r)


def test_quota_per_minute_429_is_not_a_stop() -> None:
    # 94 of these in the baseline: waited out by the gateway, never a reason to stop
    r = outcome(
        "a", attempt("RateLimitedError", 429), attempt("RateLimitedError", 429), llm_error=FAILED
    )
    assert not quota_exhausted(r)


def test_quota_same_error_type_with_another_status_is_not_a_stop() -> None:
    too_large = outcome("a", attempt("ModelUnavailableError", 413), llm_error=FAILED)
    withdrawn = outcome("b", attempt("ModelUnavailableError", 400), llm_error=FAILED)
    assert not quota_exhausted(too_large) and not quota_exhausted(withdrawn)


def test_quota_a_completed_run_is_never_a_stop() -> None:
    # the cap hit an attempt, but a retry (or another call) still answered: the run is fine
    r = outcome("a", attempt("QuotaExhaustedError", 429), attempt(None, None))
    assert r.completed and not quota_exhausted(r)


def test_quota_old_rows_without_http_status() -> None:
    old = attempt("ModelUnavailableError", None)
    del old["http_status"]  # recorded before 8.6
    assert not quota_exhausted(outcome("a", old, llm_error=FAILED))


def test_quota_judge_calls_dont_count() -> None:
    r = outcome("a", attempt("QuotaExhaustedError", 429, purpose="eval.judge"), llm_error=FAILED)
    assert not quota_exhausted(r)


def test_run_loop_stops_at_the_quota_and_does_not_save_that_run() -> None:
    answers = {
        "s1": outcome("s1", attempt(None, None)),
        "s2": outcome("s2", attempt("QuotaExhaustedError", 429), llm_error=FAILED),
        "s3": outcome("s3", attempt(None, None)),
    }
    todo = [scenario(sid, "M-01", None) for sid in answers]
    saved: list[str] = []

    async def run_one(s: Scenario) -> Result:
        return answers[s.id]

    got = asyncio.run(run_loop(todo, run_one, lambda r: saved.append(r.scenario_id)))
    assert saved == ["s1"]  # s2 is NOT saved: resume runs it again after the reset; s3 never ran
    assert got.done == 1 and got.paused is not None and got.paused.scenario_id == "s2"
    assert got.spent == 2  # s2's failed attempt still counts against the quota


def test_run_loop_without_a_quota_hit_saves_everything() -> None:
    todo = [scenario(sid, "M-01", None) for sid in ("s1", "s2")]

    async def run_one(s: Scenario) -> Result:
        return outcome(s.id, attempt(None, None))

    saved: list[str] = []
    got = asyncio.run(run_loop(todo, run_one, lambda r: saved.append(r.scenario_id)))
    assert saved == ["s1", "s2"] and got.paused is None and got.done == 2


def test_a_draft_whose_llm_never_answered_is_not_completed() -> None:
    # the real row (2026-11-16): the diagnosis answered, Groq's daily cap hit the draft call
    r = outcome(
        "a",
        attempt(None, None),
        attempt("ModelUnavailableError", 429, purpose="agent.work_order"),
    )
    assert r.llm_error is None and r.draft_llm_failed
    assert not r.completed  # errored, re-run on resume: not "the agent chose not to draft"
    assert quota_exhausted(r)  # and the loop stops there


def test_a_draft_that_answered_once_is_completed() -> None:
    # a retried draft (e.g. after a guardrail rejection) whose second call answered
    r = outcome(
        "a",
        attempt(None, None),
        attempt("RateLimitedError", 429, purpose="agent.work_order"),
        attempt(None, None, purpose="agent.work_order"),
    )
    assert not r.draft_llm_failed and r.completed
