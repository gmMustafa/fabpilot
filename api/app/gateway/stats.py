"""Usage statistics from llm_calls (2.5): requests used, failures, latency per model, cache savings.

Remember the grain: one llm_calls row = one ATTEMPT (a request to a provider, or a cache hit).
- "requests" = attempts that reached a provider (cached = false): what the free quota counts.
- "calls"    = distinct call_id: questions the app asked; a call can take several requests.
- a call FAILED when none of its attempts succeeded.
Cache hits are excluded from per-model numbers: a 47 ms hit would make a model look 30x faster.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import LlmCallStatus
from app.schemas import ModelStats, StatsTotals

_TOTALS_SQL = text("""
    WITH calls AS (
        SELECT call_id, bool_or(status = :ok) AS succeeded
        FROM llm_calls
        WHERE time >= :since
        GROUP BY call_id
    )
    SELECT
        (SELECT count(*) FROM calls)                       AS calls,
        (SELECT count(*) FROM calls WHERE NOT succeeded)   AS failed_calls,
        count(*) FILTER (WHERE NOT cached)                 AS requests,
        count(*) FILTER (WHERE cached)                     AS cache_hits,
        coalesce(sum(tokens_in), 0)                        AS tokens_in,
        coalesce(sum(tokens_out), 0)                       AS tokens_out,
        (SELECT count(*) FROM llm_calls
         WHERE NOT cached AND time >= :day_start)          AS requests_today_utc
    FROM llm_calls
    WHERE time >= :since
""")

_ERROR_TYPES_SQL = text("""
    SELECT model, coalesce(error_type, 'unknown') AS error_type, count(*) AS n
    FROM llm_calls
    WHERE time >= :since AND NOT cached AND status = :error
    GROUP BY model, error_type
""")


async def fetch_totals(conn: AsyncConnection, since: datetime, day_start: datetime) -> StatsTotals:
    """Whole-gateway numbers for the window, plus provider requests since `day_start`."""
    row = (
        (
            await conn.execute(
                _TOTALS_SQL, {"since": since, "day_start": day_start, "ok": LlmCallStatus.OK.value}
            )
        )
        .mappings()
        .one()  # aggregates without GROUP BY always return exactly one row (zeros if empty)
    )
    return StatsTotals(**row)


async def fetch_model_rows(conn: AsyncConnection, since: datetime) -> list[dict[str, Any]]:
    """One row per model for provider requests (NOT cache hits) since `since`, busiest first.

    Columns: model, requests, ok, errors, latency_p50_ms, latency_p95_ms, tokens_in, tokens_out.
    """
    query = text("""
        SELECT
            model,
            count(*) AS requests,
            count(*) FILTER (WHERE status = :ok) AS ok,
            count(*) FILTER (WHERE status = :error) AS errors,

            percentile_cont(0.5)
                WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE status = :ok) AS latency_p50_ms,

            percentile_cont(0.95)
                WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE status = :ok) AS latency_p95_ms,

            sum(tokens_in) AS tokens_in,
            sum(tokens_out) AS tokens_out

        FROM llm_calls

        WHERE time >= :since
          AND NOT cached

        GROUP BY model
        ORDER BY requests DESC, model
    """)

    result = await conn.execute(
        query,
        {
            "since": since,
            "ok": LlmCallStatus.OK.value,
            "error": LlmCallStatus.ERROR.value,
        },
    )

    return [dict(r) for r in result.mappings()]


async def fetch_stats(
    conn: AsyncConnection, since: datetime, day_start: datetime
) -> tuple[StatsTotals, list[ModelStats]]:
    """Everything /gateway/stats returns. Three small queries, all on the time-indexed window."""
    totals = await fetch_totals(conn, since, day_start)
    rows = await fetch_model_rows(conn, since)

    errors: dict[str, dict[str, int]] = defaultdict(dict)
    result = await conn.execute(
        _ERROR_TYPES_SQL, {"since": since, "error": LlmCallStatus.ERROR.value}
    )
    for r in result.mappings():
        errors[r["model"]][r["error_type"]] = r["n"]

    models = [ModelStats(**row, error_types=errors.get(row["model"], {})) for row in rows]
    return totals, models
