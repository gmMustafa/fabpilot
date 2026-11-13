"""DbPromptCache against a throwaway migrated database (your TODO: get())."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.gateway.cache import CachedAnswer, DbPromptCache

pytestmark = pytest.mark.integration

WEEK = timedelta(days=7)
ANSWER = CachedAnswer("Cavitation is...", "vendor/m:free", 35, 215, 170, "stop")


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


async def _hits(engine: AsyncEngine, key: str) -> tuple[int, bool]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT hits, last_hit_at IS NOT NULL FROM llm_cache WHERE cache_key = :k"),
                {"k": key},
            )
        ).one()
    return int(row[0]), bool(row[1])


async def test_put_then_get_returns_the_answer_and_counts_hits(engine: AsyncEngine) -> None:
    cache = DbPromptCache(engine)
    await cache.put("k1", "h1", "shared", ANSWER, WEEK)
    assert await _hits(engine, "k1") == (0, False)

    assert await cache.get("k1", WEEK) == ANSWER
    assert await cache.get("k1", WEEK) == ANSWER
    assert await _hits(engine, "k1") == (2, True)  # counted, and the counter COMMITTED


async def test_unknown_key_is_a_miss(engine: AsyncEngine) -> None:
    assert await DbPromptCache(engine).get("nope", WEEK) is None


async def _age(engine: AsyncEngine, key: str, days: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE llm_cache SET created_at = now() - make_interval(days => :d)"
                " WHERE cache_key = :k"
            ),
            {"d": days, "k": key},
        )


async def test_expired_entry_is_a_miss_and_not_counted(engine: AsyncEngine) -> None:
    cache = DbPromptCache(engine)
    await cache.put("old", "h", "shared", ANSWER, WEEK)
    await _age(engine, "old", days=8)
    assert await cache.get("old", WEEK) is None
    assert await _hits(engine, "old") == (0, False)


async def test_second_put_keeps_the_first_answer(engine: AsyncEngine) -> None:
    """Cache stampede: two identical requests both missed and both called the model."""
    cache = DbPromptCache(engine)
    await cache.put("k", "h", "shared", ANSWER, WEEK)
    await cache.put(
        "k", "h", "shared", CachedAnswer("other", "x", 1, 1, 0, "stop"), WEEK
    )  # no error
    assert await cache.get("k", WEEK) == ANSWER


async def test_put_replaces_an_expired_entry(engine: AsyncEngine) -> None:
    """Regression: with DO NOTHING an expired row blocked every new put -> miss forever."""
    cache = DbPromptCache(engine)
    await cache.put("k", "h", "shared", ANSWER, WEEK)
    assert await cache.get("k", WEEK) == ANSWER  # hits = 1
    await _age(engine, "k", days=8)
    assert await cache.get("k", WEEK) is None

    fresh = CachedAnswer("newer answer", "vendor/other:free", 30, 100, 0, "stop")
    await cache.put("k", "h", "shared", fresh, WEEK)
    assert await cache.get("k", WEEK) == fresh
    assert await _hits(engine, "k") == (1, True)  # counter restarted with the new answer
