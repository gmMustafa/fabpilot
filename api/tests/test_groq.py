"""Groq provider against fake HTTP responses (httpx2.MockTransport): no network, no quota."""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.gateway.gateway import Action, classify
from app.gateway.groq import GroqProvider, map_groq_error
from app.gateway.types import (
    AuthError,
    BadRequestError,
    ChatMessage,
    ModelUnavailableError,
    ProviderError,
    RateLimitedError,
    ServerError,
)

MSG = [ChatMessage(role="user", content="hi")]
Handler = Callable[[httpx2.Request], httpx2.Response]


def provider(handler: Handler) -> tuple[GroqProvider, list[httpx2.Request]]:
    sent: list[httpx2.Request] = []

    def record(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return handler(request)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(record))
    return GroqProvider("gsk_test", http_client=client), sent


def completion(text: str = "OK") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


def error(message: str, code: str = "rate_limit_exceeded") -> dict[str, Any]:
    return {"error": {"message": message, "type": "tokens", "code": code}}


async def test_success_and_no_reasoning_parameter_unless_the_route_sets_one() -> None:
    p, sent = provider(lambda r: httpx2.Response(200, json=completion("Hello")))
    result = await p.complete("openai/gpt-oss-120b", MSG, max_tokens=50, temperature=0.0)
    assert (result.text, result.tokens_in, result.tokens_out) == ("Hello", 12, 3)
    body = json.loads(sent[0].content)
    assert sent[0].url.path == "/openai/v1/chat/completions"
    assert "reasoning_effort" not in body and "reasoning" not in body  # the model's default

    await p.complete(
        "openai/gpt-oss-120b", MSG, max_tokens=50, temperature=0.0, reasoning_effort="low"
    )
    body = json.loads(sent[1].content)
    assert body["reasoning_effort"] == "low"
    assert "reasoning" not in body  # OpenRouter's object would be rejected by Groq


@pytest.mark.parametrize(
    ("status", "body", "headers", "expected"),
    [
        # per-minute limits: seconds to wait
        (
            429,
            error("... on tokens per minute (TPM): Limit 8000"),
            {"retry-after": "7"},
            RateLimitedError,
        ),
        (
            429,
            error("... on requests per minute (RPM): Limit 30"),
            {"retry-after": "2"},
            RateLimitedError,
        ),
        # a DAILY limit is per MODEL on Groq: this model is done, its siblings aren't
        (
            429,
            error("... on requests per day (RPD): Limit 1000"),
            {"retry-after": "3600"},
            ModelUnavailableError,
        ),
        (429, error("... on tokens per day (TPD)"), {}, ModelUnavailableError),
        (429, error("Rate limit reached"), {"retry-after": "7200"}, ModelUnavailableError),
        # one request above the model's per-minute token budget: no retry can fix it
        (413, error("Request too large ... Limit 8000, Requested 9700"), {}, ModelUnavailableError),
        (
            400,
            error("The model `x` has been decommissioned", "model_decommissioned"),
            {},
            ModelUnavailableError,
        ),
        (404, error("The model `x` does not exist", "model_not_found"), {}, ModelUnavailableError),
        (
            400,
            error("reasoning_effort must be one of low, medium, high", "invalid_request"),
            {},
            BadRequestError,
        ),
        (401, error("Invalid API Key", "invalid_api_key"), {}, AuthError),
        (503, error("Service unavailable", "service_unavailable"), {}, ServerError),
    ],
)
def test_error_mapping(
    status: int, body: dict[str, Any], headers: dict[str, str], expected: type[ProviderError]
) -> None:
    err = map_groq_error(status, body, headers, "openai/gpt-oss-120b")
    assert type(err) is expected
    assert err.status == status and err.model == "openai/gpt-oss-120b"


def test_groq_limits_route_like_this() -> None:
    """What the gateway DOES with each Groq limit: the reason the mapping exists."""
    tpm = map_groq_error(429, error("(TPM)"), {"retry-after": "7"}, "m")
    rpd = map_groq_error(429, error("on requests per day (RPD)"), {}, "m")
    bad_key = map_groq_error(401, error("Invalid API Key"), {}, "m")
    assert classify(tpm, max_delay=20) is Action.RETRY_SAME  # wait 7 s, same model
    assert classify(rpd, max_delay=20) is Action.NEXT_MODEL  # another Groq model still has quota
    assert classify(bad_key, max_delay=20) is Action.NEXT_PROVIDER  # no Groq model will answer


async def test_http_errors_from_the_sdk_are_mapped() -> None:
    p, _ = provider(
        lambda r: httpx2.Response(
            429, json=error("... on requests per day (RPD)"), headers={"retry-after": "3600"}
        )
    )
    with pytest.raises(ModelUnavailableError, match="per day"):
        await p.complete("openai/gpt-oss-120b", MSG, max_tokens=50, temperature=0.0)
