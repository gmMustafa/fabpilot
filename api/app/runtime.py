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


def use_system_certificates() -> None:
    """Make Python's ssl trust the OPERATING SYSTEM's certificate store (Windows/macOS/Linux).

    Python ships its own CA bundle (certifi) and ignores the OS store. On machines with TLS
    inspection (corporate proxy, antivirus) downloads then fail with CERTIFICATE_VERIFY_FAILED
    although the browser works (seen in 3.2b when downloading the embedding model). pip does the
    same by default. Never "fix" this with verify=False: that accepts any certificate, so a model
    download could be swapped by anyone on the network path. Idempotent; call before downloads.
    """
    import truststore  # local import: only entry points that download pay for it

    truststore.inject_into_ssl()
