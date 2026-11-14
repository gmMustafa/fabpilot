"""The evidence view: every id the model can see must be an id the guardrail accepts."""

import re

from app.agent.evidence_view import render
from app.agent.state import AgentState, Evidence
from tests.test_agent_nodes import ALARMS, ANOMALIES, MACHINE, NOW

LABEL = re.compile(r"^[A-Z]\d+$")


def rendered() -> tuple[str, set[str]]:
    ev = Evidence(machine=MACHINE, alarm_log=ALARMS, anomalies=ANOMALIES)
    return render(AgentState(question="q", evidence=ev), NOW)


def test_every_visible_label_is_a_citable_id() -> None:
    """Live run 5.2c: the model cited the line labelled "Q" and the guard rejected it as unknown.
    Any line that starts with a letter+number label must be in the id set, and vice versa."""
    text, ids = rendered()
    labels = {line.split(" ", 1)[0] for line in text.splitlines()}
    assert {label for label in labels if LABEL.match(label)} == ids


def test_quiet_sensors_are_citable_as_q1() -> None:
    """The key evidence for a sensor fault: the physical neighbours stayed quiet."""
    text, ids = rendered()
    assert "Q1" in ids
    assert "Q1 quiet sensors (no finding): discharge_pressure" in text
