"""/gateway/stats against a throwaway migrated DB with hand-made llm_calls rows (your TODO).

Every number below is computed by hand from ROWS, so a wrong FILTER / GROUP BY shows up as one
specific wrong number.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.deps import get_engine
from app.db.models import LlmCall, LlmCallStatus
from app.gateway.stats import fetch_stats
from app.main import app

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)
A, B = "vendor/a:free", "vendor/b:free"
C1, C2, C3, C4, C5, C6 = (uuid.uuid4() for _ in range(6))


def _row(
    call: uuid.UUID,
    n: int,
    model: str,
    latency: int,
    *,
    error: str | None = None,
    tokens: tuple[int, int] = (0, 0),
    cached: bool = False,
    minutes_ago: float = 10,
) -> dict[str, Any]:
    return {
        "time": NOW - timedelta(minutes=minutes_ago),
        "call_id": call,
        "attempt_no": n,
        "purpose": "test",
        "provider": "openrouter",
        "model": model,
        "status": LlmCallStatus.ERROR if error else LlmCallStatus.OK,
        "error_type": error,
        "tokens_in": tokens[0],
        "tokens_out": tokens[1],
        "latency_ms": latency,
        "prompt_hash": "h",
        "cached": cached,
    }


ROWS = [
    # C1: per-minute 429, then retry on A succeeds
    _row(C1, 1, A, 500, error="RateLimitedError"),
    _row(C1, 2, A, 1000, tokens=(10, 100)),
    # C2: A rotated away (instant 400), fallback to B succeeds
    _row(C2, 1, A, 50, error="ModelUnavailableError"),
    _row(C2, 2, B, 2000, tokens=(20, 200)),
    # C3: both models down -> a FAILED call
    _row(C3, 1, A, 3000, error="ServerError"),
    _row(C3, 2, B, 4000, error="ServerError"),
    # C4: cache hit -> not a request, not in A's latency
    _row(C4, 1, A, 47, cached=True),
    # C5: outside a 24 h window
    _row(C5, 1, A, 9999, tokens=(1, 1), minutes_ago=30 * 60),
    # C6: plain success on A
    _row(C6, 1, A, 3000, tokens=(5, 50)),
]


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    async with eng.begin() as conn:
        await conn.execute(insert(LlmCall), ROWS)
    yield eng
    await eng.dispose()


async def test_stats_numbers(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        totals, models = await fetch_stats(
            conn, since=NOW - timedelta(hours=24), day_start=NOW - timedelta(hours=2)
        )

    assert totals.model_dump() == {
        "calls": 5,  # C1 C2 C3 C4 C6 (C5 is outside the window)
        "failed_calls": 1,  # C3
        "requests": 7,  # every in-window row except the cache hit
        "cache_hits": 1,
        "tokens_in": 35,
        "tokens_out": 350,
        "requests_today_utc": 7,  # C5 (30 h ago) is before day_start
    }
    assert [m.model for m in models] == [A, B]  # busiest first

    a, b = models
    assert (a.requests, a.ok, a.errors) == (5, 2, 3)
    assert a.error_types == {"RateLimitedError": 1, "ModelUnavailableError": 1, "ServerError": 1}
    # successful A latencies [1000, 3000]: p50 = 2000; p95 = 1000 + 0.95 * 2000 (interpolated)
    assert (a.latency_p50_ms, a.latency_p95_ms) == (2000, 2900)
    assert (a.tokens_in, a.tokens_out) == (15, 150)

    assert (b.requests, b.ok, b.errors, b.error_types) == (2, 1, 1, {"ServerError": 1})
    assert (b.latency_p50_ms, b.latency_p95_ms) == (2000, 2000)  # the ServerError's 4 s excluded


async def test_model_without_success_has_null_latency(engine: AsyncEngine) -> None:
    c = "vendor/c:free"
    async with engine.begin() as conn:
        await conn.execute(
            insert(LlmCall), [_row(uuid.uuid4(), 1, c, 60000, error="LLMTimeoutError")]
        )
        _, models = await fetch_stats(conn, since=NOW - timedelta(hours=1), day_start=NOW)
    only_errors = next(m for m in models if m.model == c)
    assert (only_errors.ok, only_errors.errors) == (0, 1)
    assert only_errors.latency_p50_ms is None  # a 60 s timeout is not "how fast it answers"


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_engine] = lambda: engine
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def test_endpoint_returns_the_window(client: AsyncClient) -> None:
    r = await client.get("/gateway/stats", params={"hours": 24})
    assert r.status_code == 200
    body = r.json()
    assert body["window_hours"] == 24
    assert (body["totals"]["calls"], body["totals"]["requests"]) == (5, 7)
    assert [m["model"] for m in body["models"]] == [A, B]

    wide = (await client.get("/gateway/stats", params={"hours": 48})).json()
    assert wide["totals"]["calls"] == 6  # now C5 (30 h ago) is inside


@pytest.mark.parametrize("hours", [0, 721, "abc"])
async def test_endpoint_rejects_bad_windows(client: AsyncClient, hours: int | str) -> None:
    assert (await client.get("/gateway/stats", params={"hours": hours})).status_code == 422
