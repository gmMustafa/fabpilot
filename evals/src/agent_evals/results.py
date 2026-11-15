"""Eval results (8.3b): one JSON line per scenario run, as the agent produced it. No grading.

Running and grading are separate on purpose: grading (8.3c) reads these files, so a change to a
grading rule costs 0 LLM requests. A line is written right after its scenario, so a run that
stops (the daily quota, Ctrl+C) loses nothing; running again with the same file resumes.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.agent.state import AgentState

RunEnd = Literal["paused", "done", "failed"]  # paused = a draft waits for approval


class Usage(BaseModel):
    llm_calls: int = 0  # our gateway calls
    provider_requests: int = 0  # what they cost against the free quota (retries included)
    tokens: int = 0


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    status: RunEnd
    thread_id: str | None
    machine: str
    question: str
    answer: str | None = None
    hypothesis: dict[str, Any] | None = None
    work_order: dict[str, Any] | None = None
    work_order_errors: list[str] = []
    guard_errors: list[str] = []  # the LAST rejection (empty: the final hypothesis passed)
    retries: int = 0
    llm_error: str | None = None  # an LLM failure the run survived ("insufficient data")
    error: str | None = None  # the run itself crashed
    tools: list[str] = []  # tools that RETURNED something (failed calls: evidence_errors)
    follow_up_calls: list[dict[str, Any]] = []  # the model's own tool choices, with args
    evidence_errors: list[str] = []
    retrieved_docs: list[str] = []  # the documents a citation may name (guardrails, 5.2b)
    usage: Usage = Usage()
    # every provider attempt of this run (purpose, model, status, error_type): WHICH model
    # answered, which failed first. `models` below is only the configured fallback list.
    llm_calls: list[dict[str, Any]] = []
    # the scenario's setup when it ran: grading flags results whose scenario changed since
    setup: dict[str, Any] | None = None
    wall_seconds: float
    # the conditions of the run: a cached answer measures nothing new; models rotate (free tier)
    cache: bool
    models: list[str]
    eval_now: datetime
    started_at: datetime

    @property
    def completed(self) -> bool:
        """The agent got to answer. Not completed (a crash, an LLM failure) = run it again on
        resume, and grade it as ERRORED, never as wrong: a bad day on OpenRouter is not a bad
        agent."""
        return self.status != "failed" and self.error is None and self.llm_error is None


def tools_called(state: AgentState) -> list[str]:
    """Every tool that returned something, reconstructed from the state: investigate's fixed
    calls (one Evidence field each), the SOP read for the draft, then the model's follow-ups."""
    ev = state.evidence
    fixed = [
        ("get_machine", ev.machine is not None),
        ("get_alarm_log", ev.alarm_log is not None),
        ("detect_anomalies", ev.anomalies is not None),
        ("list_incident_reports", ev.incidents is not None),
        ("search_docs", bool(ev.docs)),
        ("get_document", state.sop_text is not None),
    ]
    follow_ups = [c["tool"] for c in state.follow_up_calls if c.get("ok")]
    return [name for name, called in fixed if called] + follow_ups


def from_state(state: AgentState | None, **run: Any) -> Result:
    """A Result from the run's final state (None: it crashed before the first checkpoint).
    `run`: the fields the state doesn't know (scenario_id, status, timing, conditions...)."""
    if state is None:
        return Result(**run)
    return Result(
        answer=state.answer,
        hypothesis=state.hypothesis.model_dump(mode="json") if state.hypothesis else None,
        work_order=state.work_order.model_dump(mode="json") if state.work_order else None,
        work_order_errors=state.work_order_errors,
        guard_errors=state.guard_errors,
        retries=state.retries,
        llm_error=state.llm_error,
        tools=tools_called(state),
        follow_up_calls=state.follow_up_calls,
        evidence_errors=state.evidence.errors,
        retrieved_docs=sorted(state.evidence.cited_ids()),
        usage=Usage(
            llm_calls=state.llm_requests,
            provider_requests=state.llm_attempts,
            tokens=state.llm_tokens,
        ),
        **run,
    )


def append(path: Path, result: Result) -> None:
    """One line, flushed when the file closes: the scenario is saved before the next starts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(result.model_dump_json() + "\n")


def read(path: Path) -> list[Result]:
    """Every line; a scenario run twice (retried on resume) appears twice, oldest first."""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Result.model_validate_json(line) for line in lines if line.strip()]


def latest(results: list[Result]) -> dict[str, Result]:
    """scenario_id -> its newest result: what grading looks at."""
    return {r.scenario_id: r for r in results}


def done_ids(path: Path) -> set[str]:
    """Scenarios to SKIP on resume: those whose newest result is completed."""
    return {sid for sid, r in latest(read(path)).items() if r.completed}


def completed_counts(path: Path) -> Counter[str]:
    """scenario_id -> how many COMPLETED runs the file holds (for --repeat: run until N)."""
    return Counter(r.scenario_id for r in read(path) if r.completed)
