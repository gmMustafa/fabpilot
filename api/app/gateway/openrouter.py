"""OpenRouter provider: the official `openai` SDK pointed at OpenRouter's OpenAI-compatible API.

Its main job is translating OpenRouter's responses into our error taxonomy (gateway/types.py).
The mapping is based on OpenRouter's docs AND on observed responses, which differ:

  observed (2026-11): unknown/removed model -> HTTP 400 "<id> is not a valid model ID"
                      (the docs list 404 not_found). Free models rotate, so a model that
                      vanished from the list must FALL BACK, not fail the whole call.

  429 is used for both the per-minute and the per-DAY free limit, with the same message; only
  X-RateLimit-Reset (ms since epoch) tells them apart. The daily limit is account-wide.
  Errors can also arrive inside an HTTP 200 (body with `error`, or finish_reason "error").
"""

import time
from collections.abc import Mapping
from typing import Any, cast

import httpx2
import openai
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from app.gateway.types import (
    AuthError,
    BadRequestError,
    ChatMessage,
    Completion,
    ConnectionFailedError,
    LLMTimeoutError,
    ModelUnavailableError,
    OutputTruncatedError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitedError,
    ServerError,
)

QUOTA_RESET_THRESHOLD = 3600.0  # a 429 that resets later than this is the DAILY quota


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,  # tests inject a MockTransport
        clock: Any = time.time,
    ) -> None:
        self._clock = clock
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            # The gateway owns retries. The SDK's default (2) would silently multiply ours.
            max_retries=0,
            timeout=httpx2.Timeout(read_timeout, connect=connect_timeout),
            # Optional attribution: shows the app name on OpenRouter's dashboard.
            default_headers={
                "HTTP-Referer": "https://github.com/gmMustafa/fabpilot",
                "X-Title": "FabPilot",
            },
            http_client=http_client,
        )

    async def complete(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                # ChatMessage has exactly the fields of the SDK's message TypedDicts.
                messages=cast(
                    "list[ChatCompletionMessageParam]", [m.model_dump() for m in messages]
                ),
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except openai.APITimeoutError as e:  # subclass of APIConnectionError: check it first
            raise LLMTimeoutError(str(e), model=model) from e
        except openai.APIConnectionError as e:
            raise ConnectionFailedError(str(e), model=model) from e
        except openai.APIStatusError as e:
            raise map_status_error(
                e.status_code, _as_dict(e.body), e.response.headers, model, self._clock()
            ) from e
        return _completion_or_raise(response, model, self._clock(), max_tokens)

    async def aclose(self) -> None:
        await self._client.close()


# ------------------------------------------------------------------ mapping


def map_status_error(
    status: int,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    model: str,
    now: float,
) -> ProviderError:
    """HTTP status (+ OpenRouter's error body and headers) -> our taxonomy."""
    message = str(body.get("message") or f"HTTP {status}")
    metadata = body.get("metadata") or {}
    error_type = metadata.get("error_type") if isinstance(metadata, Mapping) else None
    retry_after = _retry_after(headers)
    kw: dict[str, Any] = {"model": model, "status": status, "retry_after": retry_after}

    match status:
        case 400 if "not a valid model id" in message.lower():
            return ModelUnavailableError(message, **kw)  # observed behaviour, see module doc
        case 400 | 413 | 422:
            return BadRequestError(message, **kw)
        case 401 | 403:  # 403: permissions, moderation, budget guardrails: none fixed by retrying
            return AuthError(message, **kw)
        case 402:
            # With Retry-After: in-flight budget, settles soon. Without: out of credits.
            if retry_after is not None:
                return RateLimitedError(message, **kw)
            return QuotaExhaustedError(message, **kw)
        case 404:
            return ModelUnavailableError(message, **kw)
        case 408 | 504:
            return LLMTimeoutError(message, **kw)
        case 429:
            reset_in = _reset_in(headers, now)
            if reset_in is not None and reset_in > QUOTA_RESET_THRESHOLD:
                return QuotaExhaustedError(message, **kw)  # daily free quota: account-wide
            if retry_after is None and reset_in is not None:
                kw["retry_after"] = reset_in
            return RateLimitedError(message, **kw)
        case 503 if error_type == "provider_overloaded":
            return ServerError(message, **kw)  # busy: worth a retry
        case 503:
            return ModelUnavailableError(message, **kw)  # no provider can serve this model
        case _ if 500 <= status < 600:
            return ServerError(message, **kw)
        case _:
            return ProviderError(message, **kw)  # unknown -> gateway fails closed


def _completion_or_raise(
    response: ChatCompletion, model: str, now: float, max_tokens: int
) -> Completion:
    """A 200 isn't always a success on OpenRouter: check for an embedded error first."""
    extra = response.model_extra or {}
    embedded = extra.get("error")
    choice = response.choices[0] if response.choices else None
    # "error" is an OpenRouter extension: the SDK's Literal type doesn't include it, but the SDK
    # doesn't validate responses, so it does occur at runtime. Compare as a plain string.
    failed = choice is None or str(choice.finish_reason) == "error"
    if embedded is not None or failed:
        err = _as_dict(embedded) or {"message": "empty response (no choices)"}
        raise map_status_error(int(err.get("code") or 502), err, {}, model, now)
    assert choice is not None
    usage = response.usage
    details = usage.completion_tokens_details if usage else None
    reasoning = (details.reasoning_tokens or 0) if details else 0
    text = choice.message.content or ""
    finish = str(choice.finish_reason) if choice.finish_reason else None
    if not text.strip() and finish == "length":
        # Observed (2026-11) with a reasoning model and max_tokens=20: 20/20 tokens were
        # reasoning, content None. "Successful" but useless -> make it loud.
        raise OutputTruncatedError(
            f"no answer within max_tokens={max_tokens} "
            f"({reasoning} reasoning tokens used); raise max_tokens for reasoning models",
            model=model,
            status=200,
        )
    return Completion(
        text=text,
        model=response.model or model,
        tokens_in=usage.prompt_tokens if usage else 0,
        tokens_out=usage.completion_tokens if usage else 0,
        finish_reason=finish,
        reasoning_tokens=reasoning,
    )


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _retry_after(headers: Mapping[str, str]) -> float | None:
    """Retry-After in seconds. (The HTTP-date form isn't used by OpenRouter; ignored if seen.)"""
    raw = headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _reset_in(headers: Mapping[str, str], now: float) -> float | None:
    """Seconds until X-RateLimit-Reset (sent as milliseconds since the Unix epoch)."""
    raw = headers.get("x-ratelimit-reset")
    try:
        return max(0.0, int(raw) / 1000 - now) if raw is not None else None
    except ValueError:
        return None
