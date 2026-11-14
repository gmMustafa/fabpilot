"""The evidence view: every id the model can see must be an id the guardrail accepts."""

import re
from typing import Any

import pytest

from app.agent.evidence_view import render
from app.agent.state import AgentState, Evidence
from tests.test_agent_nodes import ALARMS, ANOMALIES, MACHINE, NOW

# ANYTHING that looks like an id: capitals, digits, "-", "*" ("F1", "A-", "A*", "DATA"). 5.2c
# caught "Q"; 5.5b showed letter+digit was too narrow: the model cited "A-" and "DATA".
LABEL = re.compile(r"^[A-Z][A-Z0-9*-]*$")


def rendered(**evidence: Any) -> tuple[str, set[str]]:
    base: dict[str, Any] = {"machine": MACHINE, "alarm_log": ALARMS, "anomalies": ANOMALIES}
    return render(AgentState(question="q", evidence=Evidence(**{**base, **evidence})), NOW)


def id_like_labels(text: str) -> set[str]:
    """The first word of every line that LOOKS like an id to the model."""
    firsts = (line.split(" ", 1)[0] for line in text.splitlines())
    return {label for label in firsts if LABEL.match(label)}


def test_every_visible_label_is_a_citable_id() -> None:
    """Live run 5.2c: the model cited the line labelled "Q" and the guard rejected it as unknown.
    Any line that starts with an id-like label must be in the id set, and vice versa."""
    text, ids = rendered()
    assert id_like_labels(text) == ids


def test_quiet_sensors_are_citable_as_q1() -> None:
    """The key evidence for a sensor fault: the physical neighbours stayed quiet."""
    text, ids = rendered()
    assert "Q1" in ids
    assert "Q1 quiet sensors (no finding): discharge_pressure" in text


@pytest.mark.parametrize(
    "alarm_log",
    [
        {"alarms": [], "total_count": 0, "truncated": False},  # was "A- no alarms in the window"
        {**ALARMS, "truncated": True, "total_count": 999},  # was "A* only the newest ..."
    ],
)
def test_no_alarms_and_a_cut_log_are_citable_too(alarm_log: dict[str, Any]) -> None:
    """5.5b live run ("Is M-05 OK?"): "no alarms" IS evidence for no_fault, so it must be an id
    the model may cite; the machine and data headers must not look like ids at all."""
    text, ids = rendered(alarm_log=alarm_log)
    assert id_like_labels(text) == ids
    assert text.startswith("Machine: ") and "\nData: " in text
