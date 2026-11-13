"""Run a coroutine from a sync entry point (CLI scripts, Alembic) with the right event loop.

Async psycopg can't use Windows' default ProactorEventLoop (see 0.3), so every
`asyncio.run(...)` in this codebase needs loop_factory=SelectorEventLoop. That's a rule people
forget (smoke.py did, in 2.2), so it lives in exactly one place. On Linux the selector loop is
already the default: this changes nothing in Docker or CI.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
