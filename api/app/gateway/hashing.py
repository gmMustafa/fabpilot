"""Stable prompt fingerprints: same request -> same hash, always (logging in 2.2, cache key in 2.3).

A hash is only useful if it's DETERMINISTIC across processes, machines and Python versions. JSON
serialization is not deterministic by default (key order, whitespace, unicode escaping can vary),
so we first build a CANONICAL form.
"""

import hashlib
import json
from typing import Any

from app.gateway.types import ChatMessage

# Bump when the payload shape below changes: old cache entries then simply stop matching instead
# of being silently misinterpreted (cheap, explicit cache invalidation).
HASH_VERSION = 1


def canonical_json(obj: Any) -> str:
    """One exact string per value: keys sorted, no whitespace, unicode kept as-is (not \\u-escaped).

    Example: canonical_json({"b": 1, "a": "°C"}) == '{"a":"°C","b":1}'
    Lists keep their order (message order is part of the meaning of a conversation).
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def prompt_hash(messages: list[ChatMessage], *, max_tokens: int, temperature: float) -> str:
    """SHA-256 (hex) of the canonical request payload.

    The payload is exactly:
        {"v": HASH_VERSION, "messages": [{"role": ..., "content": ...}, ...],
         "max_tokens": ..., "temperature": ...}
    The MODEL is deliberately NOT part of it: the cache (2.3) answers "same question", no matter
    which fallback model answered it last time.
    """
    payload = {
        "v": HASH_VERSION,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    canonical_payload = canonical_json(payload)

    payload_bytes = canonical_payload.encode("utf-8")

    return hashlib.sha256(payload_bytes).hexdigest()
