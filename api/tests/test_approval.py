"""Human-in-the-loop (5.3): check_approval (your TODO) and pause -> decide -> save in the graph.

ScriptedChat fails on any unscripted call, so every test here also proves that deciding costs
NO extra LLM request: resuming re-runs await_approval, never the model.
"""

import pytest

from app.agent.approval import Decision, check_approval
from app.agent.graph import (
    Graph,
    build_graph,
    checkpoint_serde,
    pending_approval,
    resume,
    run_question,
)
from app.agent.state import AgentState, Approval, Category, WorkOrderDraft
from app.agent.work_order import VERIFICATION_SOP
from app.db.models import WorkOrderPriority
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, hypothesis_json
from tests.test_work_order import SOP_TEXT, draft, hypothesis, tools_with_sop

ME = "M. Engineer"


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[Decision] = []

    async def save(self, decision: Decision) -> int:
        self.saved.append(decision)
        return len(self.saved)


def unsafe_edit() -> WorkOrderDraft:
    """The reviewer 'simplified' the order and lost the hold point."""
    return draft(actions=[a.model_copy(update={"safety": False}) for a in draft().actions])


# ------------------------------------------------------------------ check_approval (your TODO)


def paused_state() -> AgentState:
    return AgentState(
        question=Q1,
        machine="M-08",
        hypothesis=hypothesis(category=Category.SENSOR_FAULT),
        sop_id=VERIFICATION_SOP,
        sop_text=SOP_TEXT,
        work_order=draft(),
    )


def test_a_named_approval_passes() -> None:
    assert check_approval(Approval(action="approve", by=ME), paused_state()) == []


def test_an_anonymous_decision_is_sent_back() -> None:
    errors = check_approval(Approval(action="approve", by="   "), paused_state())
    assert errors == ["the decision needs the reviewer's name (by)"]


@pytest.mark.parametrize("reason", [None, "", "  "])
def test_a_rejection_needs_a_reason(reason: str | None) -> None:
    errors = check_approval(Approval(action="reject", by=ME, reason=reason), paused_state())
    assert errors == ["a rejection needs a reason (it is kept for the audit trail)"]


def test_an_edit_needs_the_edited_draft() -> None:
    errors = check_approval(Approval(action="edit", by=ME), paused_state())
    assert errors == ["an edit needs the edited work order"]


def test_an_edit_is_checked_like_the_models_draft() -> None:
    errors = check_approval(Approval(action="edit", by=ME, edited=unsafe_edit()), paused_state())
    assert errors == [
        "your edit: SOP-GEN-004 has 1 hold point(s) (⚠ STOP) but only 0 safety action(s)"
    ]


def test_a_valid_edit_passes() -> None:
    edited = draft(priority=WorkOrderPriority.HIGH)
    assert check_approval(Approval(action="edit", by=ME, edited=edited), paused_state()) == []


def test_all_violations_at_once() -> None:
    errors = check_approval(Approval(action="reject", by="", reason=""), paused_state())
    assert len(errors) == 2


# ------------------------------------------------------------------ through the graph


async def paused(store: FakeStore, thread_id: str = "t1") -> Graph:
    """Q1 up to the pause: the report is written, the draft waits for a human."""
    chat = ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())
    graph = build_graph(tools_with_sop(), chat, clock=lambda: NOW, store=store)
    state = await run_question(graph, Q1, thread_id)
    assert state.answer is not None and "DRAFT work order (not saved" in state.answer
    assert state.work_order_id is None and store.saved == []  # NOTHING saved before a decision
    return graph


async def test_the_run_pauses_with_the_draft_and_saves_nothing() -> None:
    graph = await paused(FakeStore())
    pending = await pending_approval(graph, "t1")
    assert pending is not None
    assert pending["work_order"]["sop_ref"] == VERIFICATION_SOP and pending["errors"] == []


async def test_approve_saves_exactly_once_with_who_and_when() -> None:
    store = FakeStore()
    graph = await paused(store)
    state = await resume(graph, Approval(action="approve", by=ME), "t1")
    assert state.work_order_id == 1 and len(store.saved) == 1  # the node re-ran; the save didn't
    saved = store.saved[0]
    assert (saved.status, saved.by, saved.at, saved.draft) == ("approved", ME, NOW, draft())
    assert saved.machine == "M-08" and state.llm_requests == 2  # deciding cost no request
    assert saved.thread_id == "t1"  # the idempotency key comes from the run's config
    assert await pending_approval(graph, "t1") is None


async def test_reject_is_saved_with_the_reason() -> None:
    store = FakeStore()
    graph = await paused(store)
    await resume(graph, Approval(action="reject", by=ME, reason="RTD replaced yesterday"), "t1")
    assert [(d.status, d.note) for d in store.saved] == [("rejected", "RTD replaced yesterday")]


async def test_an_unsafe_edit_is_sent_back_then_a_good_one_is_saved() -> None:
    store = FakeStore()
    graph = await paused(store)
    state = await resume(graph, Approval(action="edit", by=ME, edited=unsafe_edit()), "t1")
    assert state.work_order_id is None and store.saved == []  # still paused
    pending = await pending_approval(graph, "t1")
    assert pending is not None and pending["errors"][0].startswith("your edit: SOP-GEN-004 has 1")

    better = draft(priority=WorkOrderPriority.HIGH)
    state = await resume(graph, Approval(action="edit", by=ME, edited=better), "t1")
    assert state.work_order_id == 1 and store.saved[0].draft == better
    assert store.saved[0].note == "edited by the reviewer before approval"


async def test_a_malformed_decision_asks_again_instead_of_crashing() -> None:
    store = FakeStore()
    graph = await paused(store)
    await resume(graph, {"action": "maybe", "by": ME}, "t1")
    pending = await pending_approval(graph, "t1")
    assert pending is not None and pending["errors"][0].startswith("invalid decision")
    assert store.saved == []


async def test_resuming_a_finished_run_is_refused() -> None:
    store = FakeStore()
    graph = await paused(store)
    await resume(graph, Approval(action="approve", by=ME), "t1")
    with pytest.raises(RuntimeError, match="not waiting"):
        await resume(graph, Approval(action="approve", by=ME), "t1")
    assert len(store.saved) == 1


def test_every_state_field_survives_the_checkpoint_serializer() -> None:
    """The allowlist BLOCKS unlisted types (a warning is logged only once per process, so a log
    check could miss one). Round-trip every field of a fully populated paused state instead."""
    state = paused_state().model_copy(
        update={"approval": Approval(action="edit", by=ME, edited=draft(), reason="r")}
    )
    serde = checkpoint_serde()
    for field in AgentState.model_fields:
        value = getattr(state, field)
        restored = serde.loads_typed(serde.dumps_typed(value))
        assert restored == value and type(restored) is type(value), field
    # ...and the state object itself: LangGraph stores the graph INPUT (an AgentState) as a
    # checkpoint write; the field loop above missed that (5.4 live run, --pending warned).
    whole = serde.loads_typed(serde.dumps_typed(state))
    assert whole == state and type(whole) is AgentState


async def test_nothing_to_approve_means_no_pause() -> None:
    """A healthy machine: no draft, so the run ends even with a store (5.1's no-action path)."""
    healthy = hypothesis_json(
        category="no_fault", evidence=[], citations=[], needs_action=False, root_cause="Normal."
    )
    graph = build_graph(
        tools_with_sop(), ScriptedChat(healthy), clock=lambda: NOW, store=FakeStore()
    )
    await run_question(graph, "Is M-05 OK?", "t2")
    assert await pending_approval(graph, "t2") is None
