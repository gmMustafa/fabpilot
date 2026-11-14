"""The CLI decision (5.3b): ask_decision (your TODO) with scripted keystrokes, and decide() on a
paused graph. No MCP servers, no LLM, no terminal: `read` is injected, like ScriptedChat."""

from typing import Any

import pytest

from app.agent.run import CHOICES, ask_decision, decide, outcome
from app.agent.state import AgentState, Approval
from app.db.models import WorkOrderPriority
from tests.test_approval import FakeStore, paused
from tests.test_work_order import draft

PENDING: dict[str, Any] = {"work_order": draft().model_dump(mode="json"), "errors": []}


class ScriptedRead:
    """Answers the prompts in order and remembers them; running out of answers is a test bug."""

    def __init__(self, *answers: str) -> None:
        self.answers: list[str] = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert self.answers, f"unexpected prompt {prompt!r}: the code asked more than scripted"
        return self.answers.pop(0)


# ------------------------------------------------------------------ ask_decision (your TODO)


async def test_approve() -> None:
    read = ScriptedRead("a", "Mustafa")
    assert await ask_decision(read, PENDING) == Approval(action="approve", by="Mustafa")
    assert read.prompts == [CHOICES, "Your name: "]


async def test_choices_are_case_insensitive() -> None:
    decision = await ask_decision(ScriptedRead("A", "Mustafa"), PENDING)
    assert decision is not None and decision.action == "approve"


async def test_reject_asks_for_a_reason() -> None:
    read = ScriptedRead("r", "Mustafa", "RTD replaced yesterday")
    assert await ask_decision(read, PENDING) == Approval(
        action="reject", by="Mustafa", reason="RTD replaced yesterday"
    )
    assert read.prompts == [CHOICES, "Your name: ", "Reason: "]


async def test_priority_change_asks_until_valid_and_keeps_the_rest() -> None:
    read = ScriptedRead("p", "critical", "urgent", "Mustafa")
    decision = await ask_decision(read, PENDING)
    assert decision == Approval(
        action="edit",
        by="Mustafa",
        edited=draft(priority=WorkOrderPriority.URGENT),  # ONLY the priority changed
        reason="priority changed to urgent",
    )
    # "critical" isn't a priority: asked twice
    assert read.prompts.count("New priority (low/medium/high/urgent): ") == 2


async def test_quit_saves_nothing_and_asks_nothing_more() -> None:
    read = ScriptedRead("q")
    assert await ask_decision(read, PENDING) is None
    assert read.prompts == [CHOICES]


async def test_an_unknown_choice_asks_again() -> None:
    read = ScriptedRead("yes", "", "q")
    assert await ask_decision(read, PENDING) is None
    assert read.prompts == [CHOICES, CHOICES, CHOICES]


# ------------------------------------------------------------------ decide() on a paused run


async def test_an_empty_name_is_sent_back_then_saved(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI doesn't check the name, check_approval does: the run asks again with its error."""
    store = FakeStore()
    graph = await paused(store)
    state = await decide(graph, "t1", ScriptedRead("a", "  ", "a", "Mustafa"))
    assert "! the decision needs the reviewer's name (by)" in capsys.readouterr().out
    assert state is not None and len(store.saved) == 1
    assert outcome(state) == "Saved as work order #1 (approved by Mustafa)."


async def test_quitting_a_paused_run_saves_nothing() -> None:
    store = FakeStore()
    graph = await paused(store)
    assert await decide(graph, "t1", ScriptedRead("q")) is None
    assert store.saved == []


def test_outcome_without_a_saved_order() -> None:
    assert outcome(AgentState(question="q")) == "Not saved: no work order was written."
