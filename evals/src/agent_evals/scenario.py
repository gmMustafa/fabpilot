"""Eval scenarios (8.1): what to inject, what to ask, what a correct answer must contain.

One YAML file per scenario in evals/scenarios/. Three layers of checks, all BEFORE any LLM call
(a broken scenario must fail a free CI test, not waste 4 requests of the daily quota):

1. Shape (Pydantic, strict): unknown keys, wrong types, a bad duration -> error.
2. Consistency (check_consistency): fields that must agree with each other, e.g. a sensor
   drift scenario must expect "sensor_fault". Needs only the scenario.
3. References (check_references): everything it names exists -- the machine, the sensor, the
   documents, the tools -- and the fault can physically happen on that machine type (the
   simulator's own APPLICABLE table, so the two can never disagree).

Ground truth stays on this side: the scenario and the simulator's `sim` schema are read by the
eval runner, never by the agent or its tools.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.agent.reasoning import FOLLOW_UP_TOOLS
from app.agent.state import Category
from app.db.models import FaultType
from app.plant import PlantConfig
from simulator.cli import parse_duration
from simulator.faults import APPLICABLE

# Every tool the agent can call: the investigate node's own ones + the follow-up list.
KNOWN_TOOLS = frozenset({"list_machines", "get_machine", *FOLLOW_UP_TOOLS})


def _duration(value: object) -> object:
    """'3d', '1h30m' -> timedelta: the simulator CLI's format, so a setup reads like a CLI call."""
    if isinstance(value, str):
        try:
            return parse_duration(value)
        except argparse.ArgumentTypeError as e:
            raise ValueError(str(e)) from e
    return value


def _as_list(value: object) -> object:
    """`category: machine_fault` and `category: [no_fault, unknown]` both work."""
    return [value] if isinstance(value, str) else value


def _keyword_groups(value: object) -> object:
    """Lower-case, stripped: matching (8.3) is case-insensitive, so store it that way once."""
    if isinstance(value, list):
        return [
            [w.strip().lower() if isinstance(w, str) else w for w in g]
            if isinstance(g, list)
            else g
            for g in value
        ]
    return value


class _Strict(BaseModel):
    # forbid: a typo like `must_cites:` would otherwise be silently ignored = a check never run
    model_config = ConfigDict(extra="forbid", frozen=True)


class Setup(_Strict):
    """What the simulator injects before the question is asked. No fault = a control scenario."""

    machine: str = Field(pattern=r"^M-\d{2}$")
    fault: FaultType | None = None
    severity: float = Field(default=1.0, gt=0, le=3)
    # None = the simulator's default for that fault type (DEFAULT_DURATION)
    duration: Annotated[timedelta | None, BeforeValidator(_duration)] = None
    sensor: str | None = None  # sensor_drift only: which sensor lies


WorkOrderRule = Literal["required", "forbidden", "optional"]


class Expect(_Strict):
    """What a correct answer must contain. Exact checks where the output is structured, keyword
    groups for the free-text root cause (brittle on purpose: 8.4 adds an LLM judge)."""

    # ANY of these is correct (a control scenario: "no_fault" and "unknown" are both honest)
    category: Annotated[list[Category], BeforeValidator(_as_list), Field(min_length=1)]
    # every group must match, any word within a group counts: [[bearing], [wear, worn, degrad]]
    root_cause_keywords: Annotated[
        list[Annotated[list[Annotated[str, Field(min_length=2)]], Field(min_length=1)]],
        BeforeValidator(_keyword_groups),
    ] = []
    must_cite: list[Annotated[str, Field(pattern=r"^(MAN|SOP|INC)-[A-Z]{3}-\d{3}$")]] = []
    tools: list[str] = []  # must have been called (checked against KNOWN_TOOLS)
    work_order: WorkOrderRule = "optional"


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=10)
    setup: Setup
    # the API's limits for an incident question (POST /incidents)
    question: str = Field(min_length=3, max_length=200)
    expect: Expect


class ScenarioError(Exception):
    """One or more scenarios are invalid; the message lists every problem, one per line."""


def check_consistency(s: Scenario) -> list[str]:
    """Rules that tie the scenario's own fields together. [] = consistent.

    Pure: needs no plant, no documents. Each rule returns a message that says what to fix.
    """
    errors: list[str] = []
    fault = s.setup.fault
    categories = set(s.expect.category)

    if fault is None:
        if not categories <= {Category.NO_FAULT, Category.UNKNOWN}:
            errors.append("no-fault scenario may only expect no_fault or unknown")

        if s.expect.work_order == "required":
            errors.append("no-fault scenario cannot require a work order")

    elif fault == FaultType.SENSOR_DRIFT:
        if s.expect.category != [Category.SENSOR_FAULT]:
            errors.append("sensor_drift must expect exactly [sensor_fault]")

    else:
        if Category.MACHINE_FAULT not in categories:
            errors.append(f"{fault} must expect machine_fault")

        if not s.expect.root_cause_keywords:
            errors.append(f"{fault} must specify root_cause_keywords")

    if s.setup.sensor is not None and fault != FaultType.SENSOR_DRIFT:
        errors.append("setup.sensor is only allowed for sensor_drift")

    return errors


def check_references(s: Scenario, plant: PlantConfig, doc_ids: Iterable[str]) -> list[str]:
    """Everything the scenario names exists, and the fault can happen on that machine."""
    errors: list[str] = []
    machine = next((m for m in plant.machines if m.code == s.setup.machine), None)
    if machine is None:
        known = ", ".join(m.code for m in plant.machines)
        return [f"unknown machine {s.setup.machine} (known: {known})"]

    fault = s.setup.fault
    if fault is not None and machine.type not in APPLICABLE[fault]:
        where = ", ".join(sorted(APPLICABLE[fault]))
        errors.append(
            f"{fault} can't happen on {machine.code} ({machine.type}); it needs one of: {where}"
        )

    sensors = {sp.name for sp in plant.sensor_templates[machine.type]}
    if s.setup.sensor is not None and s.setup.sensor not in sensors:
        errors.append(f"{machine.code} has no sensor {s.setup.sensor} (has: {sorted(sensors)})")

    missing = sorted(set(s.expect.must_cite) - set(doc_ids))
    if missing:
        errors.append(f"must_cite names documents that don't exist: {missing}")

    unknown_tools = sorted(set(s.expect.tools) - KNOWN_TOOLS)
    if unknown_tools:
        errors.append(f"unknown tools {unknown_tools} (known: {sorted(KNOWN_TOOLS)})")
    return errors


def parse_scenario(path: Path) -> Scenario:
    """One file -> a Scenario (shape only). The file name must be the id: easy to find."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenario = Scenario.model_validate(raw)
    if path.stem != scenario.id:
        raise ValueError(f"file name must match the id: rename to {scenario.id}.yaml")
    return scenario


def load_scenarios(directory: Path, plant: PlantConfig, doc_ids: Iterable[str]) -> list[Scenario]:
    """Every *.yaml in `directory`, sorted by id, all three layers checked.

    Collects EVERY problem in every file before raising: fixing 5 typos should take one run,
    not five.
    """
    doc_ids = frozenset(doc_ids)
    scenarios: list[Scenario] = []
    problems: list[str] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            s = parse_scenario(path)
        except (ValidationError, ValueError, yaml.YAMLError) as e:
            problems.append(f"{path.name}: {e}")
            continue
        problems += [f"{path.name}: {m}" for m in check_consistency(s)]
        problems += [f"{path.name}: {m}" for m in check_references(s, plant, doc_ids)]
        scenarios.append(s)

    if not scenarios and not problems:
        problems.append(f"no scenarios in {directory}")
    if problems:
        raise ScenarioError("\n".join(problems))
    return sorted(scenarios, key=lambda s: s.id)
