"""The LLM gateway: one place that decides retry vs. fallback vs. give up.

for model in fallback list:
    call model
      success          -> return
      RETRY_SAME       -> back off (exponential + jitter, or Retry-After), call again
      NEXT_MODEL       -> try the next model in the list
      FAIL             -> raise immediately (every model would fail the same way)
...all bounded by a TOTAL attempt budget and a wall-clock deadline: free-tier requests are
scarce (~50/day), so one question must never fan out into 3 models x 3 retries = 9 calls.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from app.gateway.types import (
    AllModelsFailedError,
    Attempt,
    AuthError,
    BadRequestError,
    ChatMessage,
    ConnectionFailedError,
    LLMProvider,
    LLMResponse,
    LLMTimeoutError,
    ModelUnavailableError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitedError,
    ServerError,
)

logger = logging.getLogger(__name__)


class Action(StrEnum):
    RETRY_SAME = "retry_same"  # transient: wait, then call the same model again
    NEXT_MODEL = "next_model"  # this model is out for now: move down the fallback list
    FAIL = "fail"  # our side / account-wide: stop, don't waste quota


def classify(error: ProviderError, max_delay: float) -> Action:
    """Decide what to do after a failed request.

    Rules (the tests in tests/test_gateway.py pin each one down):
    - LLMTimeoutError, ConnectionFailedError, ServerError        -> RETRY_SAME
    - RateLimitedError:
        no retry_after, or retry_after <= max_delay              -> RETRY_SAME
        retry_after > max_delay (we won't wait that long)        -> NEXT_MODEL
    - ModelUnavailableError                                       -> NEXT_MODEL
    - QuotaExhaustedError, BadRequestError, AuthError             -> FAIL
    - anything else (an unknown ProviderError subclass)           -> FAIL
      (fail closed: when unsure, don't burn quota on blind retries)
    """
    if isinstance(
        error,
        (
            LLMTimeoutError,
            ConnectionFailedError,
            ServerError,
        ),
    ):
        return Action.RETRY_SAME

    if isinstance(error, RateLimitedError):
        if error.retry_after is None or error.retry_after <= max_delay:
            return Action.RETRY_SAME
        return Action.NEXT_MODEL

    if isinstance(error, ModelUnavailableError):
        return Action.NEXT_MODEL

    if isinstance(
        error,
        (
            QuotaExhaustedError,
            BadRequestError,
            AuthError,
        ),
    ):
        return Action.FAIL

    # Unknown errors: fail closed.
    return Action.FAIL


def backoff_delay(
    retry: int,
    retry_after: float | None,
    base_delay: float,
    max_delay: float,
    rng: Callable[[], float],
) -> float:
    """Seconds to wait before retry number `retry` (1-based).

    The provider's Retry-After wins (it knows best), capped at max_delay. Otherwise exponential
    backoff base * 2^(retry-1) with "equal jitter": half fixed + half random, so many clients that
    failed together don't all retry in the same instant (thundering herd).
    """
    if retry_after is not None:
        return min(retry_after, max_delay)
    cap = min(max_delay, base_delay * 2.0 ** (retry - 1))
    return cap / 2 + rng() * cap / 2


@dataclass(frozen=True)
class GatewayConfig:
    models: list[str]  # fallback order, best first (OPENROUTER_MODELS)
    max_retries_per_model: int = 2  # RETRY_SAME at most this often per model
    max_attempts: int = 4  # total requests per chat() call, across all models
    base_delay: float = 1.0
    max_delay: float = 20.0  # never sleep longer than this; longer Retry-After -> next model
    deadline: float = 120.0  # wall-clock seconds for the whole chat() call


class LLMGateway:
    def __init__(
        self,
        provider: LLMProvider,
        config: GatewayConfig,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not config.models:
            raise ValueError("GatewayConfig.models is empty: set OPENROUTER_MODELS")
        self.provider = provider
        self.config = config
        # Injected so tests run instantly and deterministically (no real sleeping/randomness).
        self._sleep, self._clock, self._rng = sleep, clock, rng

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        cfg = self.config
        started = self._clock()
        attempts: list[Attempt] = []

        for model in cfg.models:
            retries = 0
            while True:
                if len(attempts) >= cfg.max_attempts:
                    raise AllModelsFailedError("attempt budget exhausted", attempts)
                if self._clock() - started >= cfg.deadline:
                    raise AllModelsFailedError("deadline exceeded", attempts)

                t0 = self._clock()
                try:
                    result = await self.provider.complete(
                        model, messages, max_tokens=max_tokens, temperature=temperature
                    )
                except ProviderError as error:
                    attempts.append(Attempt(model, _ms(self._clock() - t0), _describe(error)))
                    action = classify(error, cfg.max_delay)
                    logger.warning("LLM %s failed (%s) -> %s", model, _describe(error), action)
                    if action is Action.FAIL:
                        raise
                    if action is Action.NEXT_MODEL or retries >= cfg.max_retries_per_model:
                        break  # next model in the list
                    retries += 1
                    delay = backoff_delay(
                        retries, error.retry_after, cfg.base_delay, cfg.max_delay, self._rng
                    )
                    if self._clock() - started + delay >= cfg.deadline:
                        raise AllModelsFailedError("deadline exceeded", attempts) from error
                    await self._sleep(delay)
                    continue

                attempts.append(Attempt(model, _ms(self._clock() - t0)))
                return LLMResponse(
                    text=result.text,
                    model=result.model,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    latency_ms=_ms(self._clock() - started),
                    finish_reason=result.finish_reason,
                    reasoning_tokens=result.reasoning_tokens,
                    attempts=attempts,
                )

        raise AllModelsFailedError("all models failed", attempts)


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


def _describe(error: ProviderError) -> str:
    status = f" {error.status}" if error.status is not None else ""
    return f"{type(error).__name__}{status}"
