"""DbCallRecorder against a throwaway migrated database."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.gateway.recorder import DbCallRecorder
from app.gateway.types import Attempt, CallRecord

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(migrated_db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_db_url)
    yield eng
    await eng.dispose()


async def test_one_row_per_attempt_in_order(engine: AsyncEngine) -> None:
    call_id = uuid.uuid4()
    await DbCallRecorder(engine).record(
        CallRecord(
            call_id=call_id,
            purpose="smoke",
            provider="openrouter",
            prompt_hash="a" * 64,
            attempts=[
                Attempt(
                    "m1:free",
                    120,
                    error="RateLimitedError 429",
                    error_type="RateLimitedError",
                    http_status=429,
                ),
                Attempt(
                    "m2:free",
                    900,
                    tokens_in=38,
                    tokens_out=295,
                    reasoning_tokens=250,
                    finish_reason="stop",
                ),
            ],
        )
    )
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT attempt_no, model, status, error_type, http_status, tokens_out,"
                    " reasoning_tokens, finish_reason, cached FROM llm_calls"
                    " WHERE call_id = :c ORDER BY attempt_no"
                ),
                {"c": call_id},
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        (1, "m1:free", "error", "RateLimitedError", 429, 0, 0, None, False),
        (2, "m2:free", "ok", None, None, 295, 250, "stop", False),
    ]


async def test_same_attempt_twice_is_rejected(engine: AsyncEngine) -> None:
    record = CallRecord(uuid.uuid4(), "t", "fake", "b" * 64, [Attempt("m", 1)])
    recorder = DbCallRecorder(engine)
    await recorder.record(record)
    with pytest.raises(Exception, match="uq_llm_calls_call_id_attempt_no"):
        await recorder.record(record)  # same call_id + attempt_no: the DB refuses
