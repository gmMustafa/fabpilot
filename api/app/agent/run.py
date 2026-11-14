"""Ask the agent one question from the command line (5.2b), and decide on its draft (5.3b/5.4).

    cd api
    uv run python -m app.agent.run "M-08 has a discharge temperature alarm. Is it overheating?"
    uv run python -m app.agent.run --pending               # paused runs waiting for a decision
    uv run python -m app.agent.run --resume <thread_id>    # decide on one, in a NEW process
    uv run python -m app.agent.run --draft-only "..."      # diagnosis + draft, never pauses

Uses REAL LLM requests (~2-4). Starts both MCP servers as subprocesses (like Claude Desktop),
sends every LLM call through our gateway.

If the agent drafts a work order, the run PAUSES and you decide: approve, reject, or change the
priority. Only then is anything written to `work_orders`. The paused run lives in Postgres
(5.4): quit, and continue it later from any process with --resume. Resuming needs no MCP
servers and no LLM: only await_approval and save_work_order run again.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.checkpoint import postgres_checkpointer
from app.agent.graph import Graph, build_graph, pending_approval, resume, run_question, thread
from app.agent.state import AgentState, Approval, WorkOrderDraft
from app.agent.store import SqlWorkOrderStore
from app.agent.tools import McpTools, Server
from app.config import get_settings
from app.db.models import WorkOrderPriority
from app.db.session import create_engine
from app.gateway.factory import build_gateway
from app.runtime import run

Read = Callable[[str], Awaitable[str]]
CHOICES = "[a]pprove  [r]eject  [p]riority change  [q]uit without saving: "


async def console_read(prompt: str) -> str:
    """input() blocks the WHOLE event loop while it waits; in a thread it blocks only itself."""
    return (await asyncio.to_thread(input, prompt)).strip()


async def ask_decision(read: Read, pending: dict[str, Any]) -> Approval | None:
    """Turn the reviewer's keystrokes into an Approval, or None for "quit, save nothing".

    Rules:
    - "a" -> read "Your name: "                       -> Approval(action="approve", by=name)
    - "r" -> read "Your name: ", then "Reason: "  -> Approval(action="reject", by=..., reason=...)
    - "p" -> read "New priority (low/medium/high/urgent): " until it is a WorkOrderPriority value,
             then "Your name: " -> Approval(action="edit", by=name, edited=<the draft with the
             new priority>, reason=f"priority changed to {priority}")
    - "q" -> None
    - anything else: read CHOICES again
    Choices are case-insensitive. Don't validate the name or reason here: check_approval does that
    (one rule, one place), and the run asks again with its error message.

    Hints: `draft = WorkOrderDraft.model_validate(pending["work_order"])`;
    `draft.model_copy(update={"priority": ...})`; `x in set(WorkOrderPriority)` is False for
    an invalid string, `WorkOrderPriority(x)` raises ValueError.
    """
    while True:
        choice = (await read(CHOICES)).strip().lower()

        if choice == "q":
            return None

        if choice == "a":
            name = await read("Your name: ")
            return Approval(action="approve", by=name)

        if choice == "r":
            name = await read("Your name: ")
            reason = await read("Reason: ")
            return Approval(action="reject", by=name, reason=reason)

        if choice == "p":
            draft = WorkOrderDraft.model_validate(pending["work_order"])

            while True:
                value = (await read("New priority (low/medium/high/urgent): ")).strip().lower()

                try:
                    priority = WorkOrderPriority(value)
                    break
                except ValueError:
                    continue

            name = await read("Your name: ")

            edited = draft.model_copy(update={"priority": priority})

            return Approval(
                action="edit",
                by=name,
                edited=edited,
                reason=f"priority changed to {priority.value}",
            )


def outcome(state: AgentState) -> str:
    a = state.approval
    if state.work_order_id is None or a is None:
        return "Not saved: no work order was written."
    verb = "rejected" if a.action == "reject" else "approved"
    return f"Saved as work order #{state.work_order_id} ({verb} by {a.by.strip()})."


async def decide(graph: Graph, thread_id: str, read: Read = console_read) -> AgentState | None:
    """Ask until the run is no longer paused, or the reviewer quits. None: nothing was decided."""
    state: AgentState | None = None
    while (pending := await pending_approval(graph, thread_id)) is not None:
        for error in pending["errors"]:
            print(f"  ! {error}")
        approval = await ask_decision(read, pending)
        if approval is None:
            return state
        state = await resume(graph, approval, thread_id)
    return state


class NoTools:
    """For --resume: only await_approval and save_work_order run again, and neither calls a tool.
    So no MCP server is started, and a call would be a bug: fail loudly."""

    async def call(self, server: Server, tool: str, args: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"resuming a paused run must not call {server}.{tool}")


async def list_pending(
    graph: Graph, saver: BaseCheckpointSaver[Any], limit: int = 20
) -> list[tuple[str, str]]:
    """(thread_id, question) of the runs waiting for a decision, at most `limit`."""
    threads: list[str] = []
    async for checkpoint in saver.alist(None):
        thread_id = str(checkpoint.config["configurable"]["thread_id"])
        if thread_id not in threads:
            threads.append(thread_id)
    waiting = []
    for thread_id in threads:
        if await pending_approval(graph, thread_id) is not None:
            snapshot = await graph.aget_state(thread(thread_id))
            waiting.append((thread_id, str(snapshot.values["question"])))
            if len(waiting) == limit:
                break
    return waiting


def paused_hint(thread_id: str) -> str:
    return (
        "Not saved. The run stays paused in Postgres; decide later with:\n"
        f"  uv run python -m app.agent.run --resume {thread_id}"
    )


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    gateway = build_gateway(settings, engine=engine)
    store = None if args.draft_only else SqlWorkOrderStore(engine)
    try:
        async with postgres_checkpointer(
            str(settings.database_url), settings.db_connect_timeout
        ) as saver:
            if args.pending or args.resume:
                graph = build_graph(NoTools(), gateway, store=store, checkpointer=saver)
                if args.pending:
                    waiting = await list_pending(graph, saver)
                    for thread_id, question in waiting:
                        print(f"{thread_id}  {question}")
                    print(f"({len(waiting)} run(s) waiting for a decision)")
                    return 0
                pending = await pending_approval(graph, args.resume)
                if pending is None:
                    print(f"Run {args.resume} is not waiting for a decision.")
                    return 1
                print(WorkOrderDraft.model_validate(pending["work_order"]).title)
                final = await decide(graph, args.resume)
                print(outcome(final) if final else paused_hint(args.resume))
                return 0

            thread_id = str(uuid.uuid4())
            async with McpTools() as tools:
                checkpointer = None if store is None else saver
                graph = build_graph(tools, gateway, store=store, checkpointer=checkpointer)
                state = await run_question(graph, " ".join(args.question), thread_id)
                print(state.answer)  # the report already ends with the request count
                if store is not None and await pending_approval(graph, thread_id) is not None:
                    print(f"\nThe work order above is a DRAFT (run {thread_id}). Your decision:")
                    final = await decide(graph, thread_id)
                    print(outcome(final) if final else paused_hint(thread_id))
    finally:
        await gateway.provider.aclose()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level="WARNING", format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ask the FabPilot incident agent a question.")
    parser.add_argument("question", nargs="*", default=["Is M-05 OK?"], help='e.g. "Is M-05 OK?"')
    parser.add_argument("--draft-only", action="store_true", help="never pause for approval")
    parser.add_argument("--pending", action="store_true", help="list runs waiting for a decision")
    parser.add_argument("--resume", metavar="THREAD_ID", help="decide on a paused run")
    sys.exit(run(_main(parser.parse_args())))
