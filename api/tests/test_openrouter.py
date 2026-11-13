"""OpenRouter provider against fake HTTP responses (httpx2.MockTransport): no network, no quota.

openai 3.x uses `httpx2`, not `httpx`, so httpx mocking libraries (respx) would NOT intercept its
requests. Injecting a client with a MockTransport is library-agnostic and explicit.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.gateway.gateway import GatewayConfig, LLMGateway
from app.gateway.openrouter import OpenRouterProvider
from app.gateway.types import (
    AuthError,
    BadRequestError,
    ChatMessage,
    ConnectionFailedError,
    LLMTimeoutError,
    ModelUnavailableError,
    OutputTruncatedError,
    ProviderError,
    QuotaExhaustedError,
    RateLimitedError,
    ServerError,
)

NOW = 1_800_000_000.0  # fixed "current time" for X-RateLimit-Reset maths
MSG = [ChatMessage(role="user", content="hi")]
Handler = Callable[[httpx2.Request], httpx2.Response]


class Recorder:
    """Wraps a handler and remembers every request the SDK actually sent."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.handler(request)


def provider(handler: Handler) -> tuple[OpenRouterProvider, Recorder]:
    rec = Recorder(handler)
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(rec))
    return OpenRouterProvider("sk-or-test", http_client=client, clock=lambda: NOW), rec


def completion(text: str = "OK", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "vendor/model:free",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }
    body.update(extra)
    return body


def error(status: int, message: str, error_type: str | None = None) -> dict[str, Any]:
    meta = {"metadata": {"error_type": error_type}} if error_type else {}
    return {"error": {"code": status, "message": message, **meta}}


# ------------------------------------------------------------------ success


async def test_success_returns_text_tokens_and_sends_one_request() -> None:
    p, rec = provider(lambda r: httpx2.Response(200, json=completion("Hello")))
    result = await p.complete("vendor/model:free", MSG, max_tokens=50, temperature=0.1)

    assert (result.text, result.tokens_in, result.tokens_out) == ("Hello", 12, 3)
    assert len(rec.requests) == 1
    sent = rec.requests[0]
    assert sent.url.path == "/api/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer sk-or-test"
    assert sent.headers["x-title"] == "FabPilot"
    payload = json.loads(sent.content)
    assert payload["model"] == "vendor/model:free" and payload["max_tokens"] == 50
    assert payload["messages"] == [{"role": "user", "content": "hi"}]


# ------------------------------------------------------------------ HTTP errors -> taxonomy


@pytest.mark.parametrize(
    ("status", "body", "headers", "expected", "retry_after"),
    [
        # Observed on the real API: an unknown/removed model is a 400, not a 404.
        (400, error(400, "x/y:free is not a valid model ID"), {}, ModelUnavailableError, None),
        (400, error(400, "This model's maximum context length is 8192"), {}, BadRequestError, None),
        (401, error(401, "No auth credentials found"), {}, AuthError, None),
        (403, error(403, "Request blocked by guardrail"), {}, AuthError, None),
        (402, error(402, "Insufficient credits"), {}, QuotaExhaustedError, None),
        (402, error(402, "In-flight budget"), {"retry-after": "5"}, RateLimitedError, 5.0),
        (404, error(404, "Not found"), {}, ModelUnavailableError, None),
        (408, error(408, "Request timed out"), {}, LLMTimeoutError, None),
        (504, error(504, "Provider timeout"), {}, LLMTimeoutError, None),
        (429, error(429, "Rate limit exceeded"), {"retry-after": "7"}, RateLimitedError, 7.0),
        # Per-minute limit: resets in 30 s -> wait for it.
        (
            429,
            error(429, "Rate limit exceeded"),
            {"x-ratelimit-reset": str(int((NOW + 30) * 1000))},
            RateLimitedError,
            30.0,
        ),
        # Daily free quota: SAME status and message, but resets in hours -> account-wide, stop.
        (
            429,
            error(429, "Rate limit exceeded"),
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int((NOW + 10 * 3600) * 1000))},
            QuotaExhaustedError,
            None,
        ),
        (502, error(502, "Model down"), {}, ServerError, None),
        (503, error(503, "Overloaded", "provider_overloaded"), {}, ServerError, None),
        (503, error(503, "No available model provider"), {}, ModelUnavailableError, None),
        (500, error(500, "Internal"), {}, ServerError, None),
    ],
    ids=lambda v: str(v) if isinstance(v, int) else None,
)
async def test_status_errors_map_to_the_taxonomy(
    status: int,
    body: dict[str, Any],
    headers: dict[str, str],
    expected: type[ProviderError],
    retry_after: float | None,
) -> None:
    p, rec = provider(lambda r: httpx2.Response(status, json=body, headers=headers))
    with pytest.raises(expected) as exc:
        await p.complete("x/y:free", MSG, max_tokens=10, temperature=0)
    assert exc.value.status == status
    assert exc.value.retry_after == retry_after
    assert exc.value.model == "x/y:free"
    assert len(rec.requests) == 1  # max_retries=0: the SDK never retries on its own


async def test_unknown_status_is_a_plain_provider_error() -> None:
    p, _ = provider(lambda r: httpx2.Response(418, json=error(418, "teapot")))
    with pytest.raises(ProviderError) as exc:
        await p.complete("m", MSG, max_tokens=10, temperature=0)
    assert type(exc.value) is ProviderError  # not a subclass -> the gateway fails closed


# ------------------------------------------------------------------ errors inside a 200


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {**completion(), "choices": [], "error": error(429, "Rate limit")["error"]},
            RateLimitedError,
        ),
        (
            completion(
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "error",
                        "message": {"role": "assistant", "content": "partial"},
                    }
                ],
                error={"code": 502, "message": "Provider disconnected mid-stream"},
            ),
            ServerError,
        ),
        (completion(choices=[]), ServerError),  # 200 with nothing in it
    ],
    ids=["error-body-no-choices", "finish-reason-error", "empty-choices"],
)
async def test_errors_inside_http_200_are_not_mistaken_for_answers(
    body: dict[str, Any], expected: type[ProviderError]
) -> None:
    p, _ = provider(lambda r: httpx2.Response(200, json=body))
    with pytest.raises(expected):
        await p.complete("m", MSG, max_tokens=10, temperature=0)


# ------------------------------------------------------------------ transport failures


async def test_timeout_and_connection_errors() -> None:
    def timeout(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=r)

    def refused(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=r)

    with pytest.raises(LLMTimeoutError):
        await provider(timeout)[0].complete("m", MSG, max_tokens=10, temperature=0)
    with pytest.raises(ConnectionFailedError):
        await provider(refused)[0].complete("m", MSG, max_tokens=10, temperature=0)


# ------------------------------------------------------------------ provider + gateway together


async def test_rotated_away_model_falls_back_to_the_next_one() -> None:
    """The real-world case behind the 400 mapping: free model gone overnight."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        model = json.loads(request.content)["model"]
        if model == "gone/model:free":
            return httpx2.Response(400, json=error(400, f"{model} is not a valid model ID"))
        return httpx2.Response(200, json=completion("from the fallback"))

    p, rec = provider(handler)
    gateway = LLMGateway(p, GatewayConfig(models=["gone/model:free", "ok/model:free"]))
    result = await gateway.chat(MSG)
    assert result.text == "from the fallback"
    assert [json.loads(r.content)["model"] for r in rec.requests] == [
        "gone/model:free",
        "ok/model:free",
    ]


# ------------------------------------------------------------------ reasoning models


def _reasoning_reply(content: str | None, finish: str, reasoning_tokens: int) -> dict[str, Any]:
    return completion(
        choices=[
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning": "The user wants me to...",
                },
            }
        ],
        usage={
            "prompt_tokens": 23,
            "completion_tokens": 20,
            "total_tokens": 43,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    )


async def test_reasoning_spent_whole_budget_is_a_loud_error_not_an_empty_answer() -> None:
    """Exactly what nemotron-3.5 returned with max_tokens=20 on the real API."""
    p, rec = provider(lambda r: httpx2.Response(200, json=_reasoning_reply(None, "length", 20)))
    gateway = LLMGateway(p, GatewayConfig(models=["reasoner:free", "other:free"]))
    with pytest.raises(OutputTruncatedError, match="20 reasoning tokens"):
        await gateway.chat(MSG, max_tokens=20)
    assert len(rec.requests) == 1  # FAIL, not fallback: our max_tokens is the problem


async def test_partial_answer_cut_by_length_is_returned_and_marked() -> None:
    p, _ = provider(
        lambda r: httpx2.Response(200, json=_reasoning_reply("The bearing", "length", 5))
    )
    result = await p.complete("m", MSG, max_tokens=20, temperature=0)
    assert (result.text, result.finish_reason, result.reasoning_tokens) == (
        "The bearing",
        "length",
        5,
    )
