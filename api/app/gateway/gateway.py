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
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from app.gateway.cache import CachedAnswer, CachePolicy, PromptCache, cache_key, is_cacheable
from app.gateway.hashing import prompt_hash
from app.gateway.types import (
    AllModelsFailedError,
    Attempt,
    AuthError,
    BadRequestError,
    CallRecord,
    CallRecorder,
    ChatMessage,
    ConnectionFailedError,
    LLMProvider,
    LLMResponse,
    LLMTimeoutError,
    ModelUnavailableError,
    OutputTruncatedError,
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


# A budget this large is not a misconfiguration: a model that still gives no answer over-thinks
# (5.2b: nemotron used 1500, then 4000 reasoning tokens; gemma doesn't reason by default).
TRUNCATION_FALLBACK_MIN = 1000


def classify(error: ProviderError, max_delay: float) -> Action:
    """Decide what to do after a failed request.

    Rules (the tests in tests/test_gateway.py pin each one down):
    - LLMTimeoutError, ConnectionFailedError, ServerError        -> RETRY_SAME
    - RateLimitedError:
        no retry_after, or retry_after <= max_delay              -> RETRY_SAME
        retry_after > max_delay (we won't wait that long)        -> NEXT_MODEL
    - ModelUnavailableError                                       -> NEXT_MODEL
    - OutputTruncatedError (a BadRequestError, so checked FIRST):
        max_tokens >= TRUNCATION_FALLBACK_MIN (this model over-thinks) -> NEXT_MODEL
        smaller or unknown budget (our misconfiguration)             -> FAIL
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

    # Before the BadRequestError check below: OutputTruncatedError IS a BadRequestError.
    if isinstance(error, OutputTruncatedError):
        generous = error.max_tokens is not None and error.max_tokens >= TRUNCATION_FALLBACK_MIN
        return Action.NEXT_MODEL if generous else Action.FAIL

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
        recorder: CallRecorder | None = None,
        cache: PromptCache | None = None,
        cache_ttl: timedelta = timedelta(hours=168),
    ) -> None:
        if not config.models:
            raise ValueError("GatewayConfig.models is empty: set OPENROUTER_MODELS")
        self.provider = provider
        self.config = config
        self._recorder = recorder
        self._cache, self._cache_ttl = cache, cache_ttl
        # Injected so tests run instantly and deterministically (no real sleeping/randomness).
        self._sleep, self._clock, self._rng = sleep, clock, rng

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        purpose: str = "unspecified",
        cache: CachePolicy = CachePolicy.SHARED,
    ) -> LLMResponse:
        """Answer `messages`: from the prompt cache if possible (2.3), else via fallbacks/retries.

        Every attempt is recorded (2.2), also when the call ultimately fails: those are exactly
        the rows worth looking at later. A cache hit is recorded as one attempt with cached=True.
        """
        call_id = uuid.uuid4()
        attempts: list[Attempt] = []
        cached = False
        needs_hash = self._recorder is not None or (
            self._cache is not None and cache is not CachePolicy.OFF
        )
        digest = (
            prompt_hash(messages, max_tokens=max_tokens, temperature=temperature)
            if needs_hash
            else ""
        )
        key = cache_key(digest, cache, self.config.models) if self._cache is not None else None
        try:
            if key is not None:
                t0 = self._clock()
                hit = await self._cache_get(key[0])
                if hit is not None:
                    cached = True
                    attempts.append(
                        Attempt(hit.model, _ms(self._clock() - t0), finish_reason=hit.finish_reason)
                    )
                    return LLMResponse(
                        text=hit.text,
                        model=hit.model,
                        tokens_in=0,  # tokens actually SPENT by this call: none
                        tokens_out=0,
                        latency_ms=_ms(self._clock() - t0),
                        finish_reason=hit.finish_reason,
                        attempts=attempts,
                        call_id=call_id,
                        cached=True,
                    )
            response = await self._run(messages, max_tokens, temperature, attempts)
            if key is not None and is_cacheable(response.text, response.finish_reason):
                await self._cache_put(key, digest, response)
            return replace(response, call_id=call_id)
        finally:
            if self._recorder is not None:
                record = CallRecord(
                    call_id, purpose, self.provider.name, digest, attempts, cached=cached
                )
                await self._record(record)

    async def _cache_get(self, key: str) -> CachedAnswer | None:
        """Best effort: a broken cache is a MISS (logged), never the reason a call fails."""
        assert self._cache is not None
        try:
            return await self._cache.get(key, self._cache_ttl)
        except Exception:
            logger.exception("Prompt cache lookup failed; treating as a miss")
            return None

    async def _cache_put(self, key: tuple[str, str], digest: str, r: LLMResponse) -> None:
        assert self._cache is not None and r.finish_reason is not None
        answer = CachedAnswer(
            r.text, r.model, r.tokens_in, r.tokens_out, r.reasoning_tokens, r.finish_reason
        )
        try:
            await self._cache.put(key[0], digest, key[1], answer, self._cache_ttl)
        except Exception:
            logger.exception("Prompt cache store failed (answer unaffected)")

    async def _record(self, record: CallRecord) -> None:
        """Best effort, on purpose: if logging fails (DB briefly down), the caller still gets
        its answer. The one place an exception is swallowed, and it's logged as ERROR."""
        assert self._recorder is not None
        try:
            await self._recorder.record(record)
        except Exception:
            logger.exception("Failed to record LLM call %s (answer unaffected)", record.call_id)

    async def _run(
        self,
        messages: list[ChatMessage],
        max_tokens: int,
        temperature: float,
        attempts: list[Attempt],
    ) -> LLMResponse:
        cfg = self.config
        started = self._clock()

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
                    attempts.append(
                        Attempt(
                            model,
                            _ms(self._clock() - t0),
                            error=_describe(error),
                            error_type=type(error).__name__,
                            http_status=error.status,
                        )
                    )
                    action = classify(error, cfg.max_delay)
                    # Log what the gateway DOES, not only what classify suggested: a RETRY_SAME
                    # past the retry limit moves on, and the old log said "retry_same" anyway
                    # (misleading while debugging the 5.2b live runs).
                    if action is Action.RETRY_SAME and retries >= cfg.max_retries_per_model:
                        decision = f"{action} limit reached ({retries} retries) -> next_model"
                    else:
                        decision = str(action)
                    logger.warning("LLM %s failed (%s) -> %s", model, _describe(error), decision)
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

                attempts.append(
                    Attempt(
                        model,
                        _ms(self._clock() - t0),
                        tokens_in=result.tokens_in,
                        tokens_out=result.tokens_out,
                        reasoning_tokens=result.reasoning_tokens,
                        finish_reason=result.finish_reason,
                    )
                )
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
