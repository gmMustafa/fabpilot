"""The CI gate (8.5): does a run's summary pass? Exit 0 = pass, 1 = fail. 0 requests.

    cd evals
    uv run python -m agent_evals.gate results/x.summary.json                 # smoke defaults
    uv run python -m agent_evals.gate results/x.summary.json --max-drift-blamed 0.2   # full suite

An LLM varies between runs, so "every answer right" would make CI flaky. The gate has:
- HARD rules (default 0): a fault invented on a healthy machine, sensor drift blamed on the
  machine. A maintenance tool that sends people to fix a healthy machine is worse than no tool.
- SOFT floors: accuracy, completion. A run that didn't complete (quota, 429) is reported as
  infrastructure, never as the agent being wrong: the same split as the report.
- VALIDITY (8.6-pre): a run whose rows were answered by different models, or measured with
  different settings, is "invalid": its score describes no single configuration.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_evals import summary as summary_mod
from agent_evals.summary import Summary


@dataclass(frozen=True)
class Thresholds:
    min_accuracy: float = 0.8  # correct / completed
    min_completion: float = 0.8  # completed / run
    max_false_alarm: float = 0.0  # invented / controls
    max_drift_blamed: float = 0.0  # blamed / drifts


def check(s: Summary, t: Thresholds) -> list[str]:
    """Every rule the run breaks, as a message for the CI log; [] = pass.

    Messages start with "regression:" (the agent got worse) or "infrastructure:" (the run
    didn't complete: quota, rate limits), so whoever reads the log knows where to look.
    """
    failures: list[str] = []

    # 1. No scenarios executed
    if s.totals.n == 0:
        return ["infrastructure: no scenario ran"]

    # 2. Completion rate
    if s.completion is not None and s.completion < t.min_completion:
        completed = s.totals.n - s.totals.errored
        failures.append(
            f"infrastructure: completion {completed}/{s.totals.n} "
            f"({s.completion:.0%}) < {t.min_completion:.0%}"
        )

    # 3. Diagnosis accuracy
    if s.accuracy is not None and s.accuracy < t.min_accuracy:
        completed = s.totals.n - s.totals.errored
        failures.append(
            f"regression: accuracy {s.totals.correct}/{completed} "
            f"({s.accuracy:.0%}) < {t.min_accuracy:.0%}"
        )

    # 4. False alarms on healthy machines
    if s.controls > 0:
        false_alarm_rate = s.invented / s.controls
        if false_alarm_rate > t.max_false_alarm:
            failures.append(
                f"regression: false alarms {s.invented}/{s.controls} "
                f"({false_alarm_rate:.0%}) > {t.max_false_alarm:.0%}"
            )

    # 5. Sensor drift blamed on machine faults
    if s.drifts > 0:
        drift_rate = s.blamed / s.drifts
        if drift_rate > t.max_drift_blamed:
            failures.append(
                f"regression: drift blamed {s.blamed}/{s.drifts} "
                f"({drift_rate:.0%}) > {t.max_drift_blamed:.0%}"
            )

    # 6. Stale evaluation results
    if s.stale > 0:
        failures.append(
            f"regression: {s.stale} stale scenario result(s); re-run with the current setup"
        )

    return failures


def validity(s: Summary) -> list[str]:
    """The run measures one thing, or it measures nothing: never averaged over a mix."""
    return [f"invalid: {p}" for p in s.manifest_problems]


def main(argv: Sequence[str] | None = None) -> int:
    defaults = Thresholds()
    parser = argparse.ArgumentParser(prog="agent_evals.gate", description=__doc__.split("\n")[0])
    parser.add_argument("summary_file", type=Path)
    parser.add_argument("--min-accuracy", type=float, default=defaults.min_accuracy)
    parser.add_argument("--min-completion", type=float, default=defaults.min_completion)
    parser.add_argument("--max-false-alarm", type=float, default=defaults.max_false_alarm)
    parser.add_argument("--max-drift-blamed", type=float, default=defaults.max_drift_blamed)
    args = parser.parse_args(argv)

    t = Thresholds(
        min_accuracy=args.min_accuracy,
        min_completion=args.min_completion,
        max_false_alarm=args.max_false_alarm,
        max_drift_blamed=args.max_drift_blamed,
    )
    s = summary_mod.read(args.summary_file)
    failures = validity(s) + check(s, t)
    for f in failures:
        print(f"FAIL {f}")
    if not failures:
        print(f"PASS {args.summary_file.name} ({t})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
