"""Groq provider (C2): the official `openai` SDK pointed at Groq's OpenAI-compatible API.

The same wire format as OpenRouter; three things differ, and they are this module's job:

1. Reasoning. Groq takes a top-level `reasoning_effort`, and which values a model accepts is
   per MODEL (gpt-oss: low/medium/high, default medium; Qwen: none). A wrong one is a 400. So
   nothing is sent unless the ROUTE says so ("groq:openai/gpt-oss-120b@low"); OpenRouter's
   `reasoning` object would be rejected here. gpt-oss returns its reasoning in a separate field,
   not in the content we parse as JSON.

2. Limits are per MODEL, not per account. Each model has its own RPM / RPD / TPM / TPD (the
   console's limits page: gpt-oss-120b 30 RPM, 1K RPD, 8K TPM, 200K TPD). So a DAILY limit on
   one model means "try the next model", NOT "Groq is out": it maps to ModelUnavailableError
   (NEXT_MODEL), unlike OpenRouter's account-wide free-models-per-day (QuotaExhaustedError,
   NEXT_PROVIDER). Per-minute limits (RPM, TPM) are short waits: RateLimitedError.

3. 413 "Request too large": ONE request is bigger than the model's per-minute token budget. No
   retry can fix it, another model (larger TPM) or provider may: ModelUnavailableError.

Mapping based on Groq's rate-limit docs (console.groq.com/docs/rate-limits: a hit is a 429,
`retry-after` in seconds) and on reported responses (the message names the limit: "... on
requests per day (RPD)" / "tokens per minute (TPM)"; 413 "Request too large"). Not yet observed
here first-hand: check the first live 429 / 413 against this mapping (C3 smoke).
"""

import re
import time
from collections.abc import Mapping
from typing import Any, cast

import httpx2
import openai
from openai.types.chat import ChatCompletionMessageParam

from app.gateway.openrouter import as_dict, completion_or_raise, retry_after_seconds
from app.gateway.types import (
    AuthError,
    BadRequestError,
    ChatMessage,
    Completion,
    ConnectionFailedError,
    LLMTimeoutError,
    ModelUnavailableError,
    ProviderError,
    RateLimitedError,
    ServerError,
)

# "Rate limit reached for model `x` ... on requests per day (RPD)" / "tokens per day (TPD)"
_DAILY = re.compile(r"\bper day\b|\((?:RPD|TPD)\)", re.IGNORECASE)
DAILY_RETRY_AFTER = 3600.0  # a retry-after longer than this is a daily limit too


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.groq.com/openai/v1",
        connect_timeout: float = 10.0,
        read_timeout: float = 60.0,
        http_client: httpx2.AsyncClient | None = None,  # tests inject a MockTransport
        clock: Any = time.time,
    ) -> None:
        self._clock = clock
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # the gateway owns retries
            timeout=httpx2.Timeout(read_timeout, connect=connect_timeout),
            http_client=http_client,
        )

    async def complete(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None = None,
    ) -> Completion:
        # Only the route's own effort: a global default can't fit models that disagree.
        extra_body = {"reasoning_effort": reasoning_effort} if reasoning_effort else None
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=cast(
                    "list[ChatCompletionMessageParam]", [m.model_dump() for m in messages]
                ),
                max_tokens=max_tokens,
                temperature=temperature,
                extra_body=extra_body,
            )
        except openai.APITimeoutError as e:  # subclass of APIConnectionError: check it first
            raise LLMTimeoutError(str(e), model=model) from e
        except openai.APIConnectionError as e:
            raise ConnectionFailedError(str(e), model=model) from e
        except openai.APIStatusError as e:
            raise map_groq_error(e.status_code, as_dict(e.body), e.response.headers, model) from e
        return completion_or_raise(response, model, self._clock(), max_tokens)

    async def aclose(self) -> None:
        await self._client.close()


def _message(body: Mapping[str, Any], status: int) -> str:
    """Groq's errors look like OpenAI's: {"error": {"message": ..., "type": ..., "code": ...}}."""
    error = body.get("error")
    inner = as_dict(error) if error is not None else as_dict(body)
    return str(inner.get("message") or f"HTTP {status}")


def map_groq_error(
    status: int, body: Mapping[str, Any], headers: Mapping[str, str], model: str
) -> ProviderError:
    """HTTP status (+ Groq's error body and headers) -> our taxonomy."""
    message = _message(body, status)
    retry_after = retry_after_seconds(headers)
    kw: dict[str, Any] = {"model": model, "status": status, "retry_after": retry_after}
    lowered = message.lower()

    match status:
        case 400 if "decommissioned" in lowered or "does not exist" in lowered:
            return ModelUnavailableError(message, **kw)  # a retired / unknown model: next one
        case 400 | 422:
            return BadRequestError(message, **kw)  # e.g. a reasoning_effort the model rejects
        case 401 | 403:
            return AuthError(message, **kw)  # the key: no Groq model will answer
        case 404:
            return ModelUnavailableError(message, **kw)
        case 413:
            # ONE request above this model's per-minute token budget: waiting can't help,
            # a model with a larger TPM (or another provider) can
            return ModelUnavailableError(message, **kw)
        case 429:
            daily = bool(_DAILY.search(message)) or (
                retry_after is not None and retry_after > DAILY_RETRY_AFTER
            )
            if daily:
                # per MODEL on Groq: this model is done for today, its siblings are not
                return ModelUnavailableError(message, **kw)
            return RateLimitedError(message, **kw)  # RPM / TPM: seconds
        case 408 | 504:
            return LLMTimeoutError(message, **kw)
        case _ if 500 <= status < 600:
            return ServerError(message, **kw)
        case _:
            return ProviderError(message, **kw)  # unknown -> the gateway fails closed
