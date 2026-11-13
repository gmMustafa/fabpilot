"""One real LLM call through the gateway, for a quick manual check (uses 1 free request).

uv run python -m app.gateway.smoke "In one sentence: what is a hypertable?"
"""

import asyncio
import logging
import sys

from app.config import get_settings
from app.gateway.factory import build_gateway
from app.gateway.types import ChatMessage, LLMError


async def _main(question: str) -> int:
    gateway = build_gateway(get_settings())
    try:
        r = await gateway.chat(
            [
                ChatMessage(
                    role="system", content="You are a concise industrial maintenance assistant."
                ),
                ChatMessage(role="user", content=question),
            ],
            max_tokens=800,  # room for reasoning models to think AND answer
        )
    except LLMError as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        await gateway.provider.aclose()
    print(r.text.strip())
    print(
        f"\n-- model={r.model} tokens_in={r.tokens_in} tokens_out={r.tokens_out} "
        f"finish={r.finish_reason} reasoning_tokens={r.reasoning_tokens} "
        f"latency={r.latency_ms} ms attempts={[(a.model, a.error) for a in r.attempts]}"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    question = " ".join(sys.argv[1:]) or "In one sentence: what is a hypertable?"
    sys.exit(asyncio.run(_main(question)))
