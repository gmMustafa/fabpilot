"""A run's numbers as data (8.5): what the CI gate, the README table and the dashboard read.

The markdown report is for people; this is for code. Both come from the same grades, so a
number can't say one thing in the README and another in the report. 0 requests.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_evals import judge, results
from agent_evals.grade import DIFFICULTIES, Grade, grade
from agent_evals.scenario import Scenario

TAGS = (*DIFFICULTIES, "drift", "control", "trap", "smoke")  # the report's groups, same order


def rate(part: int, whole: int) -> float | None:
    """part / whole, or None when there is nothing to divide: 0/0 is "not measured", not 0 %."""
    return part / whole if whole else None


class Counts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int = 0
    correct: int = 0
    acceptable: int = 0
    wrong: int = 0
    errored: int = 0


class JudgeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    judged: int  # answers with a verdict under the current prompt version
    correct: int
    agree: int  # judge and keyword grade say the same (correct vs not)
    calibrated: bool  # every verdict came from a model calibrated under this prompt version


class Summary(BaseModel):
    """One run, condensed. Rates are 0..1, None when their denominator is 0."""

    model_config = ConfigDict(extra="forbid")

    name: str
    eval_now: datetime | None
    suite_size: int  # scenarios in the suite now
    totals: Counts  # n = scenarios run (newest result each)
    accuracy: float | None  # correct / completed
    completion: float | None  # completed / run
    controls: int
    invented: int  # controls where the agent named a fault
    drifts: int
    blamed: int  # sensor drift blamed on the machine
    citation_recall: float | None
    citations_valid: float | None
    work_order_ok: float | None
    tools_ok: float | None
    by_fault: dict[str, Counts]  # "none" = the controls
    by_tag: dict[str, Counts]  # difficulties, drift, control, trap, smoke (those present)
    diagnosed_by: dict[str, int]  # final-hypothesis model -> scenarios
    requests: int
    tokens: int
    median_seconds: float
    stale: int
    judge: JudgeSummary | None


def _count(grades: list[Grade]) -> Counts:
    v = Counter(g.verdict for g in grades if not g.errored)
    return Counts(
        n=len(grades),
        correct=v["correct"],
        acceptable=v["acceptable"],
        wrong=v["wrong"],
        errored=sum(g.errored for g in grades),
    )


def _judge(done: list[Grade], judged: list[judge.Judged]) -> JudgeSummary | None:
    now = {
        j.scenario_id: j
        for j in judged
        if j.prompt_version == judge.PROMPT_VERSION and j.verdict is not None
    }
    pairs = [(g, now[g.scenario_id]) for g in done if g.scenario_id in now]
    if not pairs:
        return None
    trusted = judge.calibrated_models()
    return JudgeSummary(
        judged=len(pairs),
        correct=sum(j.verdict == "correct" for _, j in pairs),
        agree=sum((g.verdict == "correct") == (j.verdict == "correct") for g, j in pairs),
        calibrated=all(j.judge_model in trusted for _, j in pairs),
    )


def summarize(
    name: str,
    rs: list[results.Result],
    scenarios: dict[str, Scenario],
    judged: list[judge.Judged] | None = None,
) -> Summary:
    """`rs`: the newest result per scenario (as for the report)."""
    known = [r for r in rs if r.scenario_id in scenarios]
    grades = [grade(r, scenarios[r.scenario_id]) for r in known]
    done = [g for g in grades if not g.errored]
    totals = _count(grades)
    controls = [g for g in done if g.fault is None]
    drifts = [g for g in done if g.fault == "sensor_drift"]
    recalls = [g.citation_recall for g in done if g.citation_recall is not None]
    walls = [r.wall_seconds for r in known]
    return Summary(
        name=name,
        eval_now=known[0].eval_now if known else None,
        suite_size=len(scenarios),
        totals=totals,
        accuracy=rate(totals.correct, len(done)),
        completion=rate(len(done), len(grades)),
        controls=len(controls),
        invented=sum(g.category in ("machine_fault", "sensor_fault") for g in controls),
        drifts=len(drifts),
        blamed=sum(g.category == "machine_fault" for g in drifts),
        citation_recall=statistics.mean(recalls) if recalls else None,
        citations_valid=rate(sum(not g.invalid_citations for g in done), len(done)),
        work_order_ok=rate(sum(g.work_order_ok for g in done), len(done)),
        tools_ok=rate(sum(g.tools_ok for g in done), len(done)),
        by_fault={
            f: _count([g for g in grades if (g.fault or "none") == f])
            for f in sorted({g.fault or "none" for g in grades})
        },
        by_tag={t: _count(chosen) for t in TAGS if (chosen := [g for g in grades if t in g.tags])},
        diagnosed_by=dict(Counter(results.diagnosed_by(r) for r in known if r.completed)),
        requests=sum(r.usage.provider_requests for r in known),
        tokens=sum(r.usage.tokens for r in known),
        median_seconds=statistics.median(walls) if walls else 0.0,
        stale=sum(g.stale for g in grades),
        judge=_judge(done, judged or []),
    )


def path_for(results_file: Path) -> Path:
    """results/x.jsonl -> results/x.summary.json"""
    return results_file.with_suffix(".summary.json")


def write(summary: Summary, path: Path) -> None:
    path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")


def read(path: Path) -> Summary:
    return Summary.model_validate_json(path.read_text(encoding="utf-8"))
