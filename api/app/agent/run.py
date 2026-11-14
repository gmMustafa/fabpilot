"""Ask the agent one question from the command line (5.2b). Uses REAL LLM requests (~2-4).

    cd api
    uv run python -m app.agent.run "M-08 has a discharge temperature alarm. Is it overheating?"

Starts both MCP servers as subprocesses (like Claude Desktop), sends every LLM call through our
gateway (fallback models, call log, prompt cache: repeating a question costs nothing).
"""

import logging
import sys

from app.agent.graph import build_graph, run_question
from app.agent.tools import McpTools
from app.config import get_settings
from app.db.session import create_engine
from app.gateway.factory import build_gateway
from app.runtime import run


async def _main(question: str) -> int:
    settings = get_settings()
    engine = create_engine(settings)
    gateway = build_gateway(settings, engine=engine)
    try:
        async with McpTools() as tools:
            state = await run_question(build_graph(tools, gateway), question)
    finally:
        await gateway.provider.aclose()
        await engine.dispose()
    print(state.answer)
    return 0


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level="WARNING", format="%(levelname)s %(message)s")
    sys.exit(run(_main(" ".join(sys.argv[1:]) or "Is M-05 OK?")))
