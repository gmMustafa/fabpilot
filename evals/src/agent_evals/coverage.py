"""What the eval suite covers (8.2): a table to review, and later to quote in the README.

    cd evals
    uv run python -m agent_evals.coverage

Coverage is the point of the suite, not its size: 50 copies of one case test one case.
"""

from __future__ import annotations

from collections import Counter

from agent_evals.scenario import SCENARIOS, Scenario, load_scenarios
from app.config import get_settings
from app.plant import PlantConfig, load_plant
from app.rag.corpus import load_corpus
from simulator.faults import APPLICABLE

DIFFICULTIES = ("typical", "mild", "advanced")


def machine_type(s: Scenario, plant: PlantConfig) -> str:
    return next(str(m.type) for m in plant.machines if m.code == s.setup.machine)


def coverage_table(scenarios: list[Scenario], plant: PlantConfig) -> str:
    """A markdown table: rows = (fault, machine type), columns = what tests it."""
    cells: Counter[tuple[str, str, str]] = Counter()
    for s in scenarios:
        fault = str(s.setup.fault) if s.setup.fault else "(none: control)"
        kind = next((t for t in s.tags if t in DIFFICULTIES), "plain")
        if "trap" in s.tags:
            kind = "trap"
        cells[(fault, machine_type(s, plant), kind)] += 1

    columns = (*DIFFICULTIES, "trap", "plain")
    rows = sorted({(f, t) for f, t, _ in cells})
    lines = [
        "| fault | machine type | " + " | ".join(columns) + " | total |",
        "|---|---|" + "---|" * (len(columns) + 1),
    ]
    for fault, mtype in rows:
        counts = [cells[(fault, mtype, c)] for c in columns]
        shown = [str(n) if n else "" for n in counts]
        lines.append(f"| {fault} | {mtype} | " + " | ".join(shown) + f" | {sum(counts)} |")

    tags = Counter(t for s in scenarios for t in s.tags)
    lines += [
        "",
        f"{len(scenarios)} scenarios; tags: "
        + ", ".join(f"{t} {n}" for t, n in sorted(tags.items())),
    ]
    return "\n".join(lines)


def missing_cells(scenarios: list[Scenario], plant: PlantConfig) -> list[str]:
    """Every (fault, machine type) the simulator can produce needs at least one scenario."""
    seen = {(s.setup.fault, machine_type(s, plant)) for s in scenarios if s.setup.fault}
    return [
        f"{fault} on {mtype}"
        for fault, types in APPLICABLE.items()
        for mtype in sorted(types)
        if (fault, str(mtype)) not in seen
    ]


def main() -> None:
    settings = get_settings()
    plant = load_plant(settings.plant_file)
    doc_ids = [d.meta.doc_id for d in load_corpus(settings.docs_dir)]
    scenarios = load_scenarios(SCENARIOS, plant, doc_ids)
    print(coverage_table(scenarios, plant))
    missing = missing_cells(scenarios, plant)
    print("\nmissing: " + (", ".join(missing) if missing else "nothing"))


if __name__ == "__main__":
    main()
