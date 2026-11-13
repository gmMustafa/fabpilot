"""prompt_hash / canonical_json (your TODO): the properties a cache key must have."""

import hashlib

from app.gateway.hashing import HASH_VERSION, canonical_json, prompt_hash
from app.gateway.types import ChatMessage


def msgs(*pairs: tuple[str, str]) -> list[ChatMessage]:
    return [ChatMessage(role=r, content=c) for r, c in pairs]


BASE = msgs(("system", "You are a maintenance assistant."), ("user", "Why did M-01 stop?"))


# ------------------------------------------------------------------ canonical_json


def test_canonical_json_is_compact_and_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1}) == '{"a":2,"b":1}'


def test_canonical_json_sorts_nested_keys_but_keeps_list_order() -> None:
    assert canonical_json({"z": [{"y": 1, "x": 2}, 3]}) == '{"z":[{"x":2,"y":1},3]}'
    assert canonical_json([2, 1]) != canonical_json([1, 2])  # order of a list is meaning


def test_canonical_json_keeps_unicode_as_is() -> None:
    assert canonical_json({"unit": "°C"}) == '{"unit":"°C"}'  # not "\\u00b0C"


# ------------------------------------------------------------------ prompt_hash


def test_prompt_hash_is_sha256_of_the_documented_payload() -> None:
    expected_payload = canonical_json(
        {
            "v": HASH_VERSION,
            "messages": [m.model_dump() for m in BASE],
            "max_tokens": 100,
            "temperature": 0.2,
        }
    )
    expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
    assert prompt_hash(BASE, max_tokens=100, temperature=0.2) == expected
    assert len(expected) == 64


def test_prompt_hash_is_deterministic() -> None:
    a = prompt_hash(BASE, max_tokens=100, temperature=0.2)
    b = prompt_hash(list(BASE), max_tokens=100, temperature=0.2)
    assert a == b


def test_any_change_in_content_or_parameters_changes_the_hash() -> None:
    h = prompt_hash(BASE, max_tokens=100, temperature=0.2)
    variants = [
        prompt_hash(
            msgs(("system", "You are a maintenance assistant."), ("user", "Why did M-02 stop?")),
            max_tokens=100,
            temperature=0.2,
        ),
        prompt_hash(BASE, max_tokens=101, temperature=0.2),
        prompt_hash(BASE, max_tokens=100, temperature=0.3),
        prompt_hash(list(reversed(BASE)), max_tokens=100, temperature=0.2),  # order matters
        prompt_hash(
            msgs(("user", "You are a maintenance assistant."), ("user", "Why did M-01 stop?")),
            max_tokens=100,
            temperature=0.2,
        ),
    ]
    assert h not in variants
    assert len(set(variants)) == len(variants)
