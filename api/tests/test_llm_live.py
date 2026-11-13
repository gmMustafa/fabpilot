"""ONE real request through the full gateway. Deselected by default (see addopts in pyproject).

uv run pytest -m llm -v      # uses 1 of your ~50 free daily requests
"""

import pytest

from app.config import get_settings
from app.gateway.factory import build_gateway
from app.gateway.types import ChatMessage

pytestmark = pytest.mark.llm


async def test_real_llm_answers_through_the_gateway() -> None:
    settings = get_settings()
    if settings.openrouter_api_key is None or not settings.openrouter_models:
        pytest.skip("OPENROUTER_API_KEY / OPENROUTER_MODELS not set")
    gateway = build_gateway(settings)
    try:
        # 400, not 20: reasoning models spend tokens "thinking" before answering. With 20,
        # nemotron-3.5 used 20/20 on reasoning and returned no content at all.
        result = await gateway.chat(
            [ChatMessage(role="user", content="Reply with exactly the word: OK")], max_tokens=400
        )
    finally:
        await gateway.provider.aclose()
    assert result.text.strip(), "empty answer"
    assert result.model and result.tokens_in > 0
    print(
        f"\n{result.model}: {result.text.strip()!r} in {result.latency_ms} ms, "
        f"finish={result.finish_reason} reasoning_tokens={result.reasoning_tokens}, "
        f"attempts={[(a.model, a.error) for a in result.attempts]}"
    )
