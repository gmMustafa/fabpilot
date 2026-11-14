"""The Postgres checkpointer (5.4): a paused run survives a new connection, saver and graph."""

import asyncio

import pytest
from alembic import command
from alembic.config import Config

from app.agent.checkpoint import libpq_url, postgres_checkpointer
from app.agent.graph import build_graph, pending_approval, resume, run_question
from app.agent.run import NoTools, list_pending
from app.agent.state import Approval, Hypothesis, WorkOrderDraft
from app.agent.work_order import VERIFICATION_SOP
from app.testing import ScriptedChat
from tests.test_agent_graph import NOW, Q1, hypothesis_json
from tests.test_approval import ME, FakeStore
from tests.test_work_order import draft, tools_with_sop


def test_libpq_url_drops_the_driver_and_keeps_the_timeout() -> None:
    url = libpq_url("postgresql+psycopg://u:secret@127.0.0.1:5433/db", connect_timeout=3)
    assert url == "postgresql://u:secret@127.0.0.1:5433/db?connect_timeout=3"


@pytest.mark.integration
async def test_a_paused_run_survives_its_process(migrated_db_url: str) -> None:
    store = FakeStore()
    night = ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())
    async with postgres_checkpointer(migrated_db_url) as saver:  # "process 1", 02:00
        g1 = build_graph(
            tools_with_sop(), night, clock=lambda: NOW, store=store, checkpointer=saver
        )
        await run_question(g1, Q1, "night-shift")
    assert store.saved == []

    # "process 2", 07:00: new connection, new saver, new graph, no tools and a model that may
    # NOT be called (ScriptedChat without answers fails on any call): resuming costs nothing.
    async with postgres_checkpointer(migrated_db_url) as saver:
        g2 = build_graph(
            NoTools(), ScriptedChat(), clock=lambda: NOW, store=store, checkpointer=saver
        )
        pending = await pending_approval(g2, "night-shift")
        assert pending is not None and pending["work_order"]["sop_ref"] == VERIFICATION_SOP
        state = await resume(g2, Approval(action="approve", by=ME), "night-shift")

    assert state.work_order_id == 1 and store.saved[0].thread_id == "night-shift"
    # Restored as OUR types, not dicts: the allowlist works with the Postgres saver too.
    assert isinstance(state.hypothesis, Hypothesis) and isinstance(state.work_order, WorkOrderDraft)


@pytest.mark.integration
async def test_pending_lists_only_runs_waiting_for_a_decision(migrated_db_url: str) -> None:
    store = FakeStore()
    async with postgres_checkpointer(migrated_db_url) as saver:
        for thread_id in ("waiting", "decided"):
            chat = ScriptedChat(hypothesis_json(needs_action=True), draft().model_dump_json())
            g = build_graph(
                tools_with_sop(), chat, clock=lambda: NOW, store=store, checkpointer=saver
            )
            await run_question(g, Q1, thread_id)
        await resume(g, Approval(action="approve", by=ME), "decided")
        assert await list_pending(g, saver) == [("waiting", Q1)]


@pytest.mark.integration
async def test_checkpoint_tables_are_not_alembic_drift(
    alembic_cfg: Config, migrated_db_url: str
) -> None:
    """The library owns its tables: after setup(), `alembic check` must still see no drift
    (without the filter in migrations/env.py, autogenerate would want to DROP them)."""
    async with postgres_checkpointer(migrated_db_url):
        pass
    # env.py starts its own event loop: run it in a thread, not inside this test's loop.
    await asyncio.to_thread(command.check, alembic_cfg)
