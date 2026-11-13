"""One real LLM call through the gateway, for a quick manual check (uses 1 free request).

uv run python -m app.gateway.smoke "In one sentence: what is a hypertable?"
"""

import logging
import sys

from app.config import get_settings
from app.db.session import create_engine
from app.gateway.factory import build_gateway
from app.gateway.types import ChatMessage, LLMError
from app.runtime import run


async def _main(question: str) -> int:
    settings = get_settings()
    engine = create_engine(settings)  # log the call to llm_calls
    gateway = build_gateway(settings, engine=engine)
    try:
        r = await gateway.chat(
            [
                ChatMessage(
                    role="system", content="You are a concise industrial maintenance assistant."
                ),
                ChatMessage(role="user", content=question),
            ],
            purpose="smoke",
            max_tokens=800,  # room for reasoning models to think AND answer
        )
    except LLMError as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return 1
    finally:
        await gateway.provider.aclose()
        await engine.dispose()
    print(r.text.strip())
    print(
        f"\n-- {'CACHE HIT ' if r.cached else ''}model={r.model} "
        f"tokens_in={r.tokens_in} tokens_out={r.tokens_out} "
        f"finish={r.finish_reason} reasoning_tokens={r.reasoning_tokens} "
        f"latency={r.latency_ms} ms attempts={[(a.model, a.error) for a in r.attempts]}"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    question = " ".join(sys.argv[1:]) or "In one sentence: what is a hypertable?"
    sys.exit(run(_main(question)))
