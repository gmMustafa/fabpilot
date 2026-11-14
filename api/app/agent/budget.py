"""The run budget (5.4b): every agent run gets a fixed allowance of provider requests and tokens.

Why provider requests, not our calls: one gateway call can be up to LLM_MAX_ATTEMPTS requests
(fallbacks, retries), and the free tier counts REQUESTS (~50/day). "2 calls" can be 8 requests.

Checked BEFORE each LLM call, with a RESERVATION: the cost of a call is only known afterwards,
so a call is allowed only if the remaining budget covers its WORST case (per_call). Otherwise
the last call could overshoot by per_call - 1 requests. A refused call becomes state (llm_error /
work_order_errors) like any LLM failure: the engineer gets an answer with the reason.

The counters live in AgentState (llm_requests / llm_attempts / llm_tokens), not in this object:
they are checkpointed with the run (5.4a) and the report can state them honestly.
"""

from dataclasses import dataclass
from typing import Any

from app.agent.state import AgentState
from app.config import Settings
from app.gateway.structured import Spend

BUDGET_PREFIX = "budget:"  # every refusal starts with it: the report tells "we stopped" apart


@dataclass(frozen=True)
class RunBudget:
    max_attempts: int = 12  # provider requests per run (a normal run measured 2)
    max_tokens: int = 40_000  # in + out per run (a normal run measured ~5,700)
    per_call: int = 4  # the worst case of ONE call: the gateway's LLM_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        if self.per_call > self.max_attempts:
            # Not even the first call could be reserved: every run would be refused at once.
            raise ValueError(
                f"budget of {self.max_attempts} requests can't cover one call ({self.per_call})"
            )

    def refuse(self, attempts_used: int, tokens_used: int) -> str | None:
        """Why the NEXT call is not allowed, or None if it is.

        Rules (report every reason, joined with "; "; each starts with BUDGET_PREFIX):
        1. the remaining requests (max_attempts - attempts_used) can't cover one call's worst
           case (per_call)
             -> f"{BUDGET_PREFIX} {attempts_used} of {self.max_attempts} provider requests used,
                  the next call may need up to {self.per_call}"   (ONE line in the real message)
        2. tokens_used has reached max_tokens
             -> f"{BUDGET_PREFIX} {tokens_used} of {self.max_tokens} tokens used"

        Careful with rule 1: is remaining == per_call allowed? The tests decide (think first).
        """
        errors: list[str] = []

        # Rule 1: Check remaining provider requests
        remaining = self.max_attempts - attempts_used

        if remaining < self.per_call:
            errors.append(
                f"{BUDGET_PREFIX} {attempts_used} of {self.max_attempts} "
                f"provider requests used, the next call may need up to {self.per_call}"
            )

        # Rule 2: Check token budget
        if tokens_used >= self.max_tokens:
            errors.append(f"{BUDGET_PREFIX} {tokens_used} of {self.max_tokens} tokens used")

        return "; ".join(errors) if errors else None

    def check(self, state: AgentState, spend: Spend | None = None) -> str | None:
        """refuse() for this run: what the state has billed so far, plus a node's open meter."""
        s = spend or Spend()
        return self.refuse(state.llm_attempts + s.attempts, state.llm_tokens + s.tokens)


def billed(state: AgentState, spend: Spend) -> dict[str, Any]:
    """The state update that books a node's meter onto the run (also after a failed call)."""
    return {
        "llm_requests": state.llm_requests + spend.calls,
        "llm_attempts": state.llm_attempts + spend.attempts,
        "llm_tokens": state.llm_tokens + spend.tokens,
    }


def usage_line(state: AgentState) -> str:
    return (
        f"({state.llm_requests} LLM call(s), {state.llm_attempts} provider request(s), "
        f"{state.llm_tokens:,} tokens)"
    )


DEFAULT_BUDGET = RunBudget()  # tests and draft-only runs; the CLI and the API use from_settings


def budget_from(settings: Settings) -> RunBudget:
    """per_call = the gateway's attempt cap: the worst case of one call (the reservation)."""
    return RunBudget(
        settings.agent_max_llm_attempts, settings.agent_max_tokens, settings.llm_max_attempts
    )
