"""An SOP's structure, parsed by code (5.5): procedure steps, hold points, safety notes.

Why parse instead of count: "1 ⚠ STOP in the SOP, 1 safety action in the draft" passes even
when the model marks a harmless step as safety and drops the lockout. Mapping each hold point to
the action that references ITS location closes that hole.

What the corpus actually contains (all 9 SOPs; tests/test_procedure.py pins each one):
    4. Apply LOTO (SOP-GEN-001), vent the system. ⚠ STOP: separator tank at 0 bar?   on a step
    3. Apply LOTO ...                                                                 on the
       ⚠ STOP: housing drained and pressure-free?            (SOP-PMP-006)          continuation
    ### 4.3 Verify zero energy — ⚠ STOP                       (SOP-GEN-001)          a SECTION
Steps are numbered continuously across subsections (4.1 has 1-3, 4.2 has 4-7, ...).

Strict vs. warn (decided in 5.5): a hold point without a safety action is an ERROR (the draft is
rejected: a missing lockout can hurt someone, and the draft still LOOKS complete). A missing
ordinary step is a WARNING for the reviewer: visible, not dangerous on its own, and free models
like to condense steps, so rejecting would cost retries and often the whole draft.
"""

import re
from dataclasses import dataclass, field

from app.agent.state import WorkOrderDraft

HOLD_POINT = "⚠ STOP"

_PROCEDURE = re.compile(r"^## .*Procedure", re.IGNORECASE)
_SAFETY = re.compile(r"^## .*Safety", re.IGNORECASE)
_SUBSECTION = re.compile(r"^### §?(\d+(?:\.\d+)+)")  # "### 4.3 Verify zero energy"
_STEP = re.compile(r"^(\d+)\. ")  # "4. Apply LOTO ..."
# "step 7", "steps 7-8", and with an EN DASH (U+2013), as the SOPs and the models write ranges.
# Written as the regex escape \u2013 so the intent is visible (ruff RUF001 flags a literal one).
_STEP_REF = re.compile(r"steps?\s+(\d+)(?:\s*[\u2013-]\s*(\d+))?", re.IGNORECASE)
_SECTION_REF = re.compile(r"§\s*(\d+(?:\.\d+)+)")  # §4.3


@dataclass(frozen=True)
class HoldPoint:
    """A ⚠ STOP: on one step, or on a whole subsection (then `steps` are that section's steps)."""

    where: str  # "step 4" or "§4.3": for messages, and what the reviewer reads
    steps: frozenset[int]
    section: str | None = None  # "4.3" for a section hold point


@dataclass(frozen=True)
class Procedure:
    steps: list[int] = field(default_factory=list)
    hold_points: list[HoldPoint] = field(default_factory=list)
    sections: dict[str, frozenset[int]] = field(default_factory=dict)  # "4.3" -> {8, 9, 10}

    def referenced(self, sop_step: str) -> set[int]:
        """The procedure steps an action's sop_step points at. Explicit step numbers win:
        "§4.3 step 9" is step 9 ONLY (counting all of §4.3 would hide that 8 and 10 are
        missing); a bare "§4.3" means the whole section."""
        explicit = steps_in(sop_step)
        if explicit:
            return explicit
        return set().union(*(self.sections.get(s, frozenset()) for s in sections_in(sop_step)))


def _section_lines(sop_text: str, heading: re.Pattern[str]) -> list[str]:
    """The lines of the `## ...` section whose heading matches, up to the next `## `."""
    lines, inside = [], False
    for line in sop_text.splitlines():
        if line.startswith("## "):
            inside = bool(heading.match(line))
            continue
        if inside:
            lines.append(line)
    return lines


def parse_procedure(sop_text: str) -> Procedure:
    steps: list[int] = []
    holds: list[HoldPoint] = []
    current: int | None = None  # the step a continuation line belongs to
    section: str | None = None
    section_steps: dict[str, list[int]] = {}
    section_holds: list[str] = []
    for line in _section_lines(sop_text, _PROCEDURE):
        if sub := _SUBSECTION.match(line):
            section, current = sub.group(1), None
            section_steps[section] = []
            if HOLD_POINT in line:
                section_holds.append(section)
            continue
        if step := _STEP.match(line):
            current = int(step.group(1))
            steps.append(current)
            if section is not None:
                section_steps[section].append(current)
        elif not line.startswith((" ", "\t")):
            current = None  # a blank line or prose ("**Do not start work ...**") ends a step
        if HOLD_POINT in line and current is not None:
            hold = HoldPoint(f"step {current}", frozenset({current}))
            if hold not in holds:  # ⚠ STOP on the step line AND a continuation: still ONE
                holds.append(hold)
    for s in section_holds:
        holds.append(HoldPoint(f"§{s}", frozenset(section_steps[s]), section=s))
    return Procedure(steps, holds, {s: frozenset(n) for s, n in section_steps.items()})


def steps_in(sop_step: str) -> set[int]:
    """Step numbers an action references: "§4.3 step 7" -> {7}; "§4 steps 7-8" -> {7, 8}."""
    found: set[int] = set()
    for first, last in _STEP_REF.findall(sop_step):
        a = int(first)
        found.update(range(a, int(last) + 1) if last else [a])
    return found


def sections_in(sop_step: str) -> set[str]:
    """Subsections an action references: "§4.3 step 7" -> {"4.3"}."""
    return set(_SECTION_REF.findall(sop_step))


def safety_notes(sop_text: str) -> list[str]:
    """The SOP's §2 Safety bullets, VERBATIM (continuation lines joined). Attached by code, not
    the model: it can't forget or paraphrase them."""
    notes: list[str] = []
    for line in _section_lines(sop_text, _SAFETY):
        if line.startswith("- "):
            notes.append(line[2:].strip())
        elif line.startswith("  ") and notes and line.strip():
            notes[-1] += " " + line.strip()
    return notes


def check_procedure(
    wo: WorkOrderDraft, procedure: Procedure, sop_id: str
) -> tuple[list[str], list[str]]:
    """(errors, warnings) for how the draft covers the SOP's procedure.

    Both rules use procedure.referenced(), so steps and sections are covered by ONE rule:
    "step 4", "steps 8-10" and a bare "§4.3" (= all of its steps) all count.

    Rule B, an ERROR (strict): ALL steps of every hold point are referenced by actions with
    safety=True. For a step hold point that is the step; for a section hold point (§4.3 =
    steps 8-10) every step of it (5.5a quiz: "one of them" let a draft ignore 9 and 10).
         -> f"hold point at {hp.where} (⚠ STOP) is not covered by a safety action; mark the
              action for {hp.where} as safety=true"                 (ONE line in the real message)
            partly covered section: the same, plus f" (no safety action for steps 8, 10)"
    Rule A, a WARNING (for the reviewer): every procedure step is referenced by some action,
    missing ones listed in procedure order:
         -> f"steps 3, 6 of {sop_id} are not in the draft; check whether they apply"
            f"step 6 of {sop_id} is not in the draft; check whether it applies"
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Rule B: every step of every hold point must be referenced by a safety action
    safety_steps: set[int] = set()
    for action in wo.actions:
        if action.safety:
            safety_steps |= procedure.referenced(action.sop_step)

    for hp in procedure.hold_points:
        uncovered = sorted(hp.steps - safety_steps)
        if not uncovered:
            continue
        message = (
            f"hold point at {hp.where} (⚠ STOP) is not covered by a safety action; "
            f"mark the action for {hp.where} as safety=true"
        )
        if len(uncovered) < len(hp.steps):  # a section, partly covered: say which steps
            message += f" (no safety action for steps {', '.join(map(str, uncovered))})"
        errors.append(message)

    # Rule A: collect all referenced procedure steps once
    referenced_steps: set[int] = set()
    for action in wo.actions:
        referenced_steps |= procedure.referenced(action.sop_step)

    # Find SOP steps NOT referenced in the draft
    missing = [step for step in procedure.steps if step not in referenced_steps]

    if len(missing) == 1:
        warnings.append(
            f"step {missing[0]} of {sop_id} is not in the draft; check whether it applies"
        )
    elif missing:
        warnings.append(
            f"steps {', '.join(str(step) for step in missing)} of {sop_id} "
            "are not in the draft; check whether they apply"
        )

    return errors, warnings
