import asyncio
from collections.abc import Mapping
from typing import Any

import pytest


def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> Mapping[str, Any]:
    """Run async tests on a SelectorEventLoop.

    On Windows the default is ProactorEventLoop, which async psycopg refuses to use. On Linux the
    selector loop is already the default, so this changes nothing in CI.
    """
    return {"selector": asyncio.SelectorEventLoop}
