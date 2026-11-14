"""Ask the agent one question from the command line (5.2b), and decide on its draft (5.3b).

    cd api
    uv run python -m app.agent.run "M-08 has a discharge temperature alarm. Is it overheating?"
    uv run python -m app.agent.run --draft-only "..."      # diagnosis + draft, never pauses

Uses REAL LLM requests (~2-4; a repeated question comes from the prompt cache: 0). Starts both MCP
servers as subprocesses (like Claude Desktop), sends every LLM call through our gateway.

If the agent drafts a work order, the run PAUSES and you decide: approve, reject, or change the
priority. Only then is anything written to `work_orders`. Quitting leaves it unsaved, and with
5.3's in-memory checkpointer the paused run is gone when this process ends (5.4 fixes that).
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.agent.graph import Graph, build_graph, pending_approval, resume, run_question
from app.agent.state import AgentState, Approval, WorkOrderDraft
from app.agent.store import SqlWorkOrderStore
from app.agent.tools import McpTools
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


async def _main(question: str, draft_only: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    gateway = build_gateway(settings, engine=engine)
    store = None if draft_only else SqlWorkOrderStore(engine)
    thread_id = str(uuid.uuid4())
    try:
        async with McpTools() as tools:
            graph = build_graph(tools, gateway, store=store)
            state = await run_question(graph, question, thread_id)
            print(state.answer)  # the report already ends with the request count
            if store is not None and await pending_approval(graph, thread_id) is not None:
                print("\nThe work order above is a DRAFT. Your decision:")
                final = await decide(graph, thread_id)
                print(outcome(final) if final else "Not saved: no work order was written.")
    finally:
        await gateway.provider.aclose()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level="WARNING", format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ask the FabPilot incident agent a question.")
    parser.add_argument("question", nargs="*", help='e.g. "Is M-05 OK?"')
    parser.add_argument("--draft-only", action="store_true", help="never pause for approval")
    args = parser.parse_args()
    sys.exit(run(_main(" ".join(args.question) or "Is M-05 OK?", args.draft_only)))
