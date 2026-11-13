"""Gateway vocabulary: messages, results, and a provider-independent error taxonomy.

Providers (OpenRouter, Ollama, a test fake) translate their own errors INTO these classes, so the
retry/fallback logic never needs to know which HTTP library or vendor is underneath.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class Completion:
    """What a provider returns for one successful request."""

    text: str
    model: str  # the model that actually answered (providers may resolve aliases)
    tokens_in: int
    tokens_out: int  # includes reasoning tokens
    finish_reason: str | None = None  # "stop" = complete; "length" = cut off by max_tokens
    reasoning_tokens: int = 0  # spent "thinking" (reasoning models): billed, never shown


@dataclass(frozen=True)
class Attempt:
    """One request the gateway made. Becomes one row in llm_calls (2.2)."""

    model: str
    latency_ms: int
    error: str | None = None  # None = success; else e.g. "ServerError 503"
    error_type: str | None = None  # exception class name, e.g. "RateLimitedError"
    http_status: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    finish_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int  # total, including retries and backoff waits
    finish_reason: str | None = None
    reasoning_tokens: int = 0
    attempts: list[Attempt] = field(default_factory=list)
    call_id: uuid.UUID | None = None  # groups this call's attempts in llm_calls
    cached: bool = False  # answered from the prompt cache (2.3): no request made


class LLMProvider(Protocol):
    """Anything that can answer a chat request (OpenRouter in 2.1b, Ollama in 2.4)."""

    name: str

    async def complete(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Completion: ...

    async def aclose(self) -> None:
        """Release the HTTP connection pool."""


@dataclass(frozen=True)
class CallRecord:
    """Everything worth logging about one gateway.chat() call (all of its attempts)."""

    call_id: uuid.UUID
    purpose: str  # who called: "smoke", "agent.triage", "eval", ...
    provider: str
    prompt_hash: str
    attempts: list[Attempt]
    cached: bool = False  # answered from the prompt cache (2.3), no request made


class CallRecorder(Protocol):
    """Where call records go (Postgres now; Langfuse in Phase 9). Observer pattern: the gateway
    reports, it doesn't know or care where records end up."""

    async def record(self, call: CallRecord) -> None: ...


# ------------------------------------------------------------------ errors


class LLMError(Exception):
    """Base class for everything the gateway raises."""


class ProviderError(LLMError):
    """A failed request, already classified by the provider into one of the subclasses below."""

    def __init__(
        self,
        message: str,
        *,
        model: str = "",
        status: int | None = None,
        retry_after: float | None = None,  # seconds, from a Retry-After header if present
    ) -> None:
        super().__init__(message)
        self.model = model
        self.status = status
        self.retry_after = retry_after


# Transient: this model/provider has a temporary problem.
class LLMTimeoutError(ProviderError):
    """No (complete) answer within the timeout."""


class ConnectionFailedError(ProviderError):
    """Couldn't reach the provider at all (DNS, TCP, TLS)."""


class ServerError(ProviderError):
    """5xx from the provider."""


class RateLimitedError(ProviderError):
    """429: too many requests right now (per-minute style limits)."""


# This model won't work now; another one might.
class ModelUnavailableError(ProviderError):
    """Model removed, renamed, or no provider currently serving it (404 and friends)."""


# Our side: every model would fail the same way, so neither retry nor fallback helps.
class QuotaExhaustedError(ProviderError):
    """Account-level limit (daily free quota, no credits). Applies to ALL models on the account."""


class BadRequestError(ProviderError):
    """400: our request is invalid (too long a prompt, bad parameters)."""


class OutputTruncatedError(BadRequestError):
    """200 OK but no answer: max_tokens ran out before any content (typically a reasoning model
    spent the whole budget thinking). Our parameter is too small, so it FAILs like a bad request
    instead of falling back: a fallback would hide the misconfigured budget."""


class AuthError(ProviderError):
    """401/403: missing or invalid API key."""


class AllModelsFailedError(LLMError):
    """Gave up: every model in the fallback list failed, or the attempt budget/deadline ran out."""

    def __init__(self, reason: str, attempts: list[Attempt]) -> None:
        tried = ", ".join(f"{a.model}: {a.error}" for a in attempts) or "no attempts"
        super().__init__(f"{reason} ({tried})")
        self.reason = reason
        self.attempts = attempts
