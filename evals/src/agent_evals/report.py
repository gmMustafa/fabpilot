"""The eval report (8.3c): grade a results file and print the metrics as markdown. 0 requests.

    cd evals
    uv run python -m agent_evals.report results/run-20261115-1809.jsonl
    # prints the report and writes results/run-20261115-1809.md next to it

The newest result per scenario counts (a scenario retried on resume appears twice in the file).
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path

from agent_evals import results
from agent_evals.evaldb import eval_plant
from agent_evals.grade import DIFFICULTIES, Grade, grade
from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.config import get_settings
from app.rag.corpus import load_corpus


def pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f} %)" if whole else "-"


def _row(name: str, grades: list[Grade]) -> str:
    v = Counter(g.verdict for g in grades if not g.errored)
    errored = sum(g.errored for g in grades)
    return (
        f"| {name} | {len(grades)} | {v['correct']} | {v['acceptable']} | {v['wrong']} "
        f"| {errored} |"
    )


def _group_table(grades: list[Grade]) -> list[str]:
    groups: list[tuple[str, Callable[[Grade], bool]]] = [
        *[
            (f"fault: {f}", lambda g, f=f: g.fault == f)
            for f in sorted({g.fault or "" for g in grades} - {""})
        ],
        *[
            (t, lambda g, t=t: t in g.tags)
            for t in (*DIFFICULTIES, "drift", "control", "trap", "smoke")
        ],
    ]
    lines = ["| group | n | correct | acceptable | wrong | errored |", "|---|---|---|---|---|---|"]
    for name, member in groups:
        chosen = [g for g in grades if member(g)]
        if chosen:
            lines.append(_row(name, chosen))
    return lines


def _cost(rs: list[results.Result]) -> list[str]:
    requests = [r.usage.provider_requests for r in rs]
    walls = sorted(r.wall_seconds for r in rs)
    p95 = walls[min(len(walls) - 1, int(0.95 * len(walls)))] if walls else 0.0
    attempts: Counter[tuple[str, str]] = Counter(
        (c["model"], c["status"]) for r in rs for c in r.llm_calls
    )
    models = sorted({m for m, _ in attempts})
    lines = [
        f"- provider requests: {sum(requests)} total, "
        f"{statistics.mean(requests) if requests else 0:.1f} per scenario",
        f"- tokens: {sum(r.usage.tokens for r in rs):,}",
        f"- wall time per scenario: median {statistics.median(walls) if walls else 0:.0f} s, "
        f"p95 {p95:.0f} s",
    ]
    if models:
        lines += ["", "| model | attempts ok | attempts failed |", "|---|---|---|"]
        lines += [f"| {m} | {attempts[(m, 'ok')]} | {attempts[(m, 'error')]} |" for m in models]
    else:
        lines.append("- models: not recorded in these results (runs before 8.3c)")
    return lines


def render(name: str, rs: list[results.Result], scenarios: dict[str, Scenario]) -> str:
    """The whole report as markdown. `rs`: the newest result per scenario."""
    known = [r for r in rs if r.scenario_id in scenarios]
    grades = [grade(r, scenarios[r.scenario_id]) for r in known]
    done = [g for g in grades if not g.errored]
    v = Counter(g.verdict for g in done)
    first = rs[0] if rs else None

    out = [f"# Eval report: {name}", ""]
    if first:
        out += [
            f"Eval clock {first.eval_now:%Y-%m-%d %H:%M} UTC; prompt cache "
            f"{'ON (answers may be replayed)' if first.cache else 'off'}; "
            f"{len(known)} of {len(scenarios)} scenarios run.",
            "",
        ]
    out += [
        "## Headline",
        "",
        "| completed | correct | acceptable | wrong | errored | accuracy | completion |",
        "|---|---|---|---|---|---|---|",
        f"| {len(done)} | {v['correct']} | {v['acceptable']} | {v['wrong']} "
        f"| {len(grades) - len(done)} | {pct(v['correct'], len(done))} "
        f"| {pct(len(done), len(grades))} |",
        "",
        "Accuracy = correct / completed. An allowed 'unknown' is acceptable, never correct; "
        "a run that didn't complete is errored, never wrong. Read accuracy WITH completion: "
        "hard scenarios error more often, so a low completion rate flatters accuracy.",
        "",
        "## By group",
        "",
        *_group_table(grades),
        "",
        "## False alarms",
        "",
    ]
    controls = [g for g in done if g.fault is None]
    drifts = [g for g in done if g.fault == "sensor_drift"]
    invented = sum(g.category in ("machine_fault", "sensor_fault") for g in controls)
    blamed = sum(g.category == "machine_fault" for g in drifts)
    out += [
        f"- controls (nothing injected): invented a fault in {pct(invented, len(controls))}",
        f"- sensor drift: blamed the MACHINE in {pct(blamed, len(drifts))}",
        "",
        "## Abstention by difficulty",
        "",
        "| difficulty | said unknown |",
        "|---|---|",
    ]
    for d in DIFFICULTIES:
        chosen = [g for g in done if g.difficulty == d]
        if chosen:
            out.append(
                f"| {d} | {pct(sum(g.category == 'unknown' for g in chosen), len(chosen))} |"
            )
    recalls = [g.citation_recall for g in done if g.citation_recall is not None]
    out += [
        "",
        "## Citations, work orders, tools",
        "",
        f"- citation recall (must_cite found): "
        f"{statistics.mean(recalls) * 100:.0f} % over {len(recalls)} scenarios"
        if recalls
        else "- citation recall: no completed scenario names must_cite documents",
        *[
            f"  - {g.scenario_id}: cited {g.citation_recall * 100:.0f} % of its must_cite"
            for g in done
            if g.citation_recall is not None and g.citation_recall < 1
        ],
        "- citations all valid (retrieved): "
        + pct(sum(not g.invalid_citations for g in done), len(done)),
        f"- work-order rule met: {pct(sum(g.work_order_ok for g in done), len(done))}",
        f"- expected tools called: {pct(sum(g.tools_ok for g in done), len(done))}",
        "",
        "## Cost",
        "",
        *_cost(known),
    ]
    wrong = [g for g in done if g.verdict == "wrong"]
    if wrong:
        out += ["", "## Wrong answers", "", "| scenario | expected | said | root cause |"]
        out += ["|---|---|---|---|"]
        out += [
            f"| {g.scenario_id} | {', '.join(g.expected)} | {g.category} | {g.root_cause[:120]} |"
            for g in wrong
        ]
    errored = [r for r in known if not r.completed]
    if errored:
        out += ["", "## Errored (re-run on resume)", ""]
        out += [f"- {r.scenario_id}: {(r.error or r.llm_error or r.status)[:160]}" for r in errored]
    stale = [g.scenario_id for g in grades if g.stale]
    gone = sorted(r.scenario_id for r in rs if r.scenario_id not in scenarios)
    if stale or gone:
        out += ["", "## Out of date", ""]
        out += [f"- {s}: the scenario's setup changed since this run: re-run it" for s in stale]
        out += [f"- {s}: no longer in the suite (not graded)" for s in gone]
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent_evals.report", description=__doc__.split("\n")[0])
    parser.add_argument("results_file", type=Path)
    args = parser.parse_args(argv)

    settings = get_settings()
    plant = eval_plant(settings)
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    scenarios = {s.id: s for s in load_scenarios(SCENARIOS, plant, doc_ids)}
    newest = list(results.latest(results.read(args.results_file)).values())
    text = render(args.results_file.stem, newest, scenarios)
    print(text)
    out = args.results_file.with_suffix(".md")
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
