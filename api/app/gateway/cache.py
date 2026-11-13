"""Prompt cache: exact-request -> clean answer, in Postgres (llm_cache).

Sits IN FRONT of the gateway's fallback loop: a hit costs no request, no quota, ~milliseconds.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Protocol

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import LlmCacheEntry


class CachePolicy(StrEnum):
    OFF = "off"  # always ask the model
    SHARED = "shared"  # key = prompt only: any model's answer counts (dev default)
    PER_MODEL = "per_model"  # key includes the model list: evals comparing models (8.6)


@dataclass(frozen=True)
class CachedAnswer:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    reasoning_tokens: int
    finish_reason: str


def cache_key(prompt_hash: str, policy: CachePolicy, models: list[str]) -> tuple[str, str] | None:
    """(cache_key, scope) for this request, or None if the policy is OFF.

    PER_MODEL puts the model list in the key: otherwise evaluating model B would replay model A's
    cached answers and every model would get the same score (quiz 2.2).
    """
    if policy is CachePolicy.OFF:
        return None
    scope = "shared" if policy is CachePolicy.SHARED else "per_model:" + ",".join(models)
    key = hashlib.sha256(f"{scope}|{prompt_hash}".encode()).hexdigest()
    return key, scope


def is_cacheable(text_: str, finish_reason: str | None) -> bool:
    """Only clean answers: complete (stop) and non-empty. A cached truncated/empty answer would be
    replayed forever, instantly and with 0 tokens: nobody would suspect it (quiz 2.1)."""
    return finish_reason == "stop" and bool(text_.strip())


class PromptCache(Protocol):
    async def get(self, key: str, ttl: timedelta) -> CachedAnswer | None: ...

    async def put(
        self, key: str, prompt_hash: str, scope: str, answer: CachedAnswer, ttl: timedelta
    ) -> None: ...


class DbPromptCache:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get(self, key: str, ttl: timedelta) -> CachedAnswer | None:
        """The entry for `key` if it's younger than `ttl`, counting the hit. None otherwise.

        ONE statement, so "is it there and fresh?", "count the hit" and "read it" can't race:
            UPDATE llm_cache SET hits = hits + 1, last_hit_at = now()
            WHERE cache_key = ... AND created_at > now() - ...
            RETURNING text, model, tokens_in, tokens_out, reasoning_tokens, finish_reason
        """
        query = text("""
        UPDATE llm_cache
        SET
            hits = hits + 1,
            last_hit_at = now()
        WHERE
            cache_key = :key
            AND created_at > now() - :ttl
        RETURNING
            text,
            model,
            tokens_in,
            tokens_out,
            reasoning_tokens,
            finish_reason
    """)

        async with self._engine.begin() as conn:
            result = await conn.execute(
                query,
                {"key": key, "ttl": ttl},
            )

            row = result.mappings().first()

            if row is None:
                return None

            return CachedAnswer(**row)

    async def put(
        self, key: str, prompt_hash: str, scope: str, answer: CachedAnswer, ttl: timedelta
    ) -> None:
        """Store an answer. If a row for `key` already exists:
        - still FRESH: keep it. Two identical requests raced (both missed, both called the model:
          a "cache stampede"); the first stored answer wins.
        - EXPIRED: replace it (new answer, new created_at, hits reset). With plain DO NOTHING the
          stale row would block every future put, and that prompt would miss forever.
        """
        stmt = insert(LlmCacheEntry).values(
            cache_key=key,
            prompt_hash=prompt_hash,
            scope=scope,
            text=answer.text,
            model=answer.model,
            tokens_in=answer.tokens_in,
            tokens_out=answer.tokens_out,
            reasoning_tokens=answer.reasoning_tokens,
            finish_reason=answer.finish_reason,
        )
        new = stmt.excluded
        upsert = stmt.on_conflict_do_update(
            index_elements=[LlmCacheEntry.cache_key],
            set_={
                "text": new.text,
                "model": new.model,
                "tokens_in": new.tokens_in,
                "tokens_out": new.tokens_out,
                "reasoning_tokens": new.reasoning_tokens,
                "finish_reason": new.finish_reason,
                "created_at": func.now(),
                "hits": 0,
                "last_hit_at": None,
            },
            # The SAME freshness rule as get(): a row get() would serve is never overwritten.
            where=LlmCacheEntry.created_at <= func.now() - ttl,
        )
        async with self._engine.begin() as conn:
            await conn.execute(upsert)
