"""Persist gateway call records to Postgres (llm_calls)."""

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import LlmCall, LlmCallStatus
from app.gateway.types import CallRecord


class DbCallRecorder:
    """One multi-row INSERT per gateway call: all of its attempts, in order."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, call: CallRecord) -> None:
        if not call.attempts:
            return  # nothing was attempted (e.g. budget 0): nothing to log
        rows = [
            {
                "call_id": call.call_id,
                "attempt_no": n,
                "purpose": call.purpose,
                "provider": call.provider,
                "model": a.model,
                "status": LlmCallStatus.OK if a.ok else LlmCallStatus.ERROR,
                "error_type": a.error_type,
                "http_status": a.http_status,
                "finish_reason": a.finish_reason,
                "tokens_in": a.tokens_in,
                "tokens_out": a.tokens_out,
                "reasoning_tokens": a.reasoning_tokens,
                "latency_ms": a.latency_ms,
                "prompt_hash": call.prompt_hash,
                "cached": call.cached,
            }
            for n, a in enumerate(call.attempts, start=1)
        ]
        async with self._engine.begin() as conn:
            await conn.execute(insert(LlmCall), rows)
