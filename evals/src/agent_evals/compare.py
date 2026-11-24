"""Compare eval runs of different models (8.6d). 0 requests: it only reads result files.

    uv run python -m agent_evals.compare results/run-groq-full.jsonl \
        results/run-groq-gpt-oss-20b.jsonl results/run-groq-qwen3.8-27b.jsonl

The first file is the reference; every other run is compared with it.

A difference between two accuracies is only worth something if (1) everything except the model
was the same, and (2) it is bigger than chance. So:

1. Comparability: the agent code, the data and the temperature must agree across the runs (the
   routes differ on purpose: they are the variable). Grading is the report's own (grade.grade),
   so a comparison can never disagree with the per-run reports.
2. Paired, not pooled: both models answered the SAME scenarios. Scenarios both get right (or
   both get wrong) say nothing about the difference; only the discordant ones do. McNemar's
   exact test asks: if the models were equally good, how likely is a split of the discordant
   pairs at least this lopsided? With few discordant pairs no difference can be significant,
   and saying so is the honest result.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from math import comb
from pathlib import Path

from agent_evals import judge, results, summary
from agent_evals.evaldb import eval_plant
from agent_evals.grade import Grade, grade
from agent_evals.manifest import Manifest
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.config import get_settings
from app.rag.corpus import load_corpus

# Until 8.6b the manifest recorded the gateway's DEFAULT_TEMPERATURE (0.2), which no agent call
# uses: every agent call goes through chat_structured at 0.0 (git: temperature=0.0 in
# structured.py at the baseline's commits 6ffea189 and 708e4cd9). Rows recorded before 8.6a
# (no data_hash yet) with 0.2 are relabelled 0.0 HERE, visibly (the report lists it), never in
# the result files: those stay as recorded.
MISLABELLED_TEMPERATURE = 0.2
TRUE_STRUCTURED_TEMPERATURE = 0.0


@dataclass(frozen=True)
class Run:
    name: str
    model: str  # what answered (results.answered_by), "?" if mixed or none
    grades: dict[str, Grade]  # scenario_id -> grade of the newest result
    manifests: list[Manifest | None]
    summary: summary.Summary


@dataclass(frozen=True)
class Paired:
    """Reference vs other, on the scenarios BOTH completed."""

    n: int  # scenarios both completed
    both_right: int
    ref_only: int  # b: reference correct, other not
    other_only: int  # c: other correct, reference not
    both_wrong: int
    ref_only_ids: list[str]
    other_only_ids: list[str]


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for b vs c discordant pairs.

    Under "the models are equally good", each discordant pair is a fair coin flip: the number
    that went the reference's way is Binomial(n = b + c, 0.5). The p-value is the probability of
    a split at least as lopsided as the observed one, in either direction.
    """
    n = b + c
    k = min(b, c)

    if n == 0:
        return 1.0

    one_tail = sum(comb(n, i) * 0.5**n for i in range(k + 1))

    return min(1.0, 2 * one_tail)


def pair(ref: Run, other: Run) -> Paired:
    """Errored on either side = not comparable for that scenario (errored is never wrong)."""
    common = sorted(
        sid
        for sid in ref.grades.keys() & other.grades.keys()
        if not ref.grades[sid].errored and not other.grades[sid].errored
    )

    def right(g: Grade) -> bool:
        return g.verdict == "correct"  # as in accuracy: "acceptable" is not correct

    ref_only = [s for s in common if right(ref.grades[s]) and not right(other.grades[s])]
    other_only = [s for s in common if right(other.grades[s]) and not right(ref.grades[s])]
    both_right = sum(right(ref.grades[s]) and right(other.grades[s]) for s in common)
    return Paired(
        n=len(common),
        both_right=both_right,
        ref_only=len(ref_only),
        other_only=len(other_only),
        both_wrong=len(common) - both_right - len(ref_only) - len(other_only),
        ref_only_ids=ref_only,
        other_only_ids=other_only,
    )


def effective_temperature(m: Manifest) -> float:
    """The temperature a row REALLY ran at (see MISLABELLED_TEMPERATURE)."""
    if m.data_hash is None and m.temperature == MISLABELLED_TEMPERATURE:
        return TRUE_STRUCTURED_TEMPERATURE
    return m.temperature


def comparability(runs: Sequence[Run]) -> tuple[list[str], list[str]]:
    """(problems, notes) ACROSS the runs. Problems make the comparison invalid; notes are
    corrections or gaps a reader must know about. The routes are not checked: they differ on
    purpose (one model per run)."""
    problems: list[str] = []
    notes: list[str] = []
    per_run = {r.name: [m for m in r.manifests if m is not None] for r in runs}

    agent = {name: {m.agent_hash for m in ms} for name, ms in per_run.items()}
    if len(set().union(*agent.values())) > 1:
        problems.append(f"agent code differs: {agent}")

    data = {name: {m.data_hash for m in ms if m.data_hash} for name, ms in per_run.items()}
    if len(set().union(*data.values())) > 1:
        problems.append(f"data differs: {data}")
    unknown = [name for name, ms in per_run.items() if any(m.data_hash is None for m in ms)]
    if unknown:
        notes.append(
            f"data_hash not recorded (rows from before 8.6a) in: {', '.join(unknown)}; "
            "data/ provably unchanged since (git log -- data/)"
        )

    temps = {name: {effective_temperature(m) for m in ms} for name, ms in per_run.items()}
    if len(set().union(*temps.values())) > 1:
        problems.append(f"temperature differs: {temps}")
    relabelled = [
        name
        for name, ms in per_run.items()
        if any(effective_temperature(m) != m.temperature for m in ms)
    ]
    if relabelled:
        notes.append(
            f"temperature recorded as {MISLABELLED_TEMPERATURE} in {', '.join(relabelled)} is a "
            f"mislabel (8.6b): every agent call ran at {TRUE_STRUCTURED_TEMPERATURE}"
        )

    for r in runs:
        if r.summary.manifest_problems:
            problems.append(f"{r.name} is not one configuration: {r.summary.manifest_problems}")
        if r.summary.stale:
            problems.append(f"{r.name}: {r.summary.stale} stale scenario(s), re-run them")
    return problems, notes


# ---------------------------------------------------------------- rendering


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def _frac(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "-"


def _judge_cell(s: summary.Summary) -> str:
    j = s.judge
    if j is None or j.judged == 0:
        return "pending"
    trust = "" if j.calibrated else ", NOT calibrated"
    return f"{j.agree}/{j.judged} agree{trust}"


def render(runs: Sequence[Run]) -> str:
    ref, others = runs[0], runs[1:]
    problems, notes = comparability(runs)
    out = [f"# Model comparison (8.6): {len(runs)} runs, reference {ref.model}", ""]
    out += [f"- ! INVALID: {p}" for p in problems] or [
        "- comparable: same agent code, data and temperature; only the model differs"
    ]
    out += [f"- note: {n}" for n in notes]

    out += ["", "## Results", ""]
    out.append("| metric | " + " | ".join(r.model for r in runs) + " |")
    out.append("|---|" + "---|" * len(runs))
    rows: list[tuple[str, list[str]]] = [
        (
            "root-cause accuracy",
            [
                _frac(r.summary.totals.correct, r.summary.totals.n - r.summary.totals.errored)
                for r in runs
            ],
        ),
        (
            "completed",
            [
                _frac(r.summary.totals.n - r.summary.totals.errored, r.summary.totals.n)
                for r in runs
            ],
        ),
        (
            "fault invented on a healthy machine",
            [_frac(r.summary.invented, r.summary.controls) for r in runs],
        ),
        (
            "sensor drift blamed on the machine",
            [_frac(r.summary.blamed, r.summary.drifts) for r in runs],
        ),
        (
            "misleading-question traps correct",
            [
                _frac(
                    r.summary.by_tag["trap"].correct,
                    r.summary.by_tag["trap"].n - r.summary.by_tag["trap"].errored,
                )
                if "trap" in r.summary.by_tag
                else "-"
                for r in runs
            ],
        ),
        ("citations valid", [_pct(r.summary.citations_valid) for r in runs]),
        ("citation recall", [_pct(r.summary.citation_recall) for r in runs]),
        ("work-order rule", [_pct(r.summary.work_order_ok) for r in runs]),
        ("judge (root cause)", [_judge_cell(r.summary) for r in runs]),
        ("tokens / requests", [f"{r.summary.tokens:,} / {r.summary.requests}" for r in runs]),
        ("median seconds per scenario", [f"{r.summary.median_seconds:.0f}" for r in runs]),
    ]
    out += [f"| {name} | " + " | ".join(cells) + " |" for name, cells in rows]

    faults = sorted({f for r in runs for f in r.summary.by_fault})
    out += ["", "## Correct per fault (correct / completed)", ""]
    out.append("| fault | " + " | ".join(r.model for r in runs) + " |")
    out.append("|---|" + "---|" * len(runs))
    for f in faults:
        cells = []
        for r in runs:
            c = r.summary.by_fault.get(f)
            cells.append(_frac(c.correct, c.n - c.errored) if c else "-")
        out.append(f"| {f} | " + " | ".join(cells) + " |")

    out += ["", f"## Paired against {ref.model} (McNemar, exact, two-sided)", ""]
    out.append(
        "| model | scenarios both completed | both right | only reference "
        "| only this model | both wrong | p |"
    )
    out.append("|---|---|---|---|---|---|---|")
    pairs = {o.name: pair(ref, o) for o in others}
    for o in others:
        p = pairs[o.name]
        pv = mcnemar_exact_p(p.ref_only, p.other_only)
        out.append(
            f"| {o.model} | {p.n} | {p.both_right} | {p.ref_only} | {p.other_only} | "
            f"{p.both_wrong} | {pv:.3f} |"
        )
    out += [
        "",
        "p < 0.05 would mean: a split of the discordant pairs this lopsided is unlikely if the "
        "two models were equally good. A larger p does not prove them equal; it means 51 "
        "scenarios can't tell them apart.",
    ]
    for o in others:
        p = pairs[o.name]
        out += ["", f"### Discordant scenarios: {ref.model} vs {o.model}", ""]
        out.append(f"- only {ref.model} right: " + (", ".join(p.ref_only_ids) or "none"))
        out.append(f"- only {o.model} right: " + (", ".join(p.other_only_ids) or "none"))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- loading + CLI


def load(path: Path, scenarios: dict[str, Scenario]) -> Run:
    newest = [r for r in results.latest(results.read(path)).values() if r.scenario_id in scenarios]
    judged = judge.read_judged(judge.sidecar(path))
    s = summary.summarize(path.stem, newest, scenarios, judged)
    models = set(s.answered_by)
    return Run(
        name=path.stem,
        model=next(iter(models)) if len(models) == 1 else "?",
        grades={r.scenario_id: grade(r, scenarios[r.scenario_id]) for r in newest},
        manifests=[r.manifest for r in newest],
        summary=s,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_evals.compare", description=__doc__.split("\n")[0])
    parser.add_argument("results_files", type=Path, nargs="+", help="the first is the reference")
    parser.add_argument("--out", type=Path, help="write the markdown here too")
    args = parser.parse_args(argv)
    if len(args.results_files) < 2:
        parser.error("compare needs at least two runs")

    settings = get_settings()
    plant = eval_plant(settings)
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    scenarios = {s.id: s for s in load_scenarios(SCENARIOS, plant, doc_ids)}
    text = render([load(p, scenarios) for p in args.results_files])
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
