"""Routes (C1): one entry of the fallback list = a provider, a model and, optionally, a reasoning
effort. "groq:openai/gpt-oss-120b@low", "openrouter:google/gemma-4-31b-it:free".

Why the effort lives in the ROUTE: providers and even models disagree on it. Groq's GPT-OSS
accepts low/medium/high, its Qwen3 accepts none, and a value a model doesn't support is a 400.
One global setting can't fit every route of a cross-provider list.

Parsing is backward compatible: an entry WITHOUT a known provider prefix belongs to the default
provider, so an old OPENROUTER_MODELS="nvidia/x:free,google/y:free" still means what it meant.
(OpenRouter ids contain ":" themselves, ":free": only a KNOWN name before the first ":" counts as
a prefix.)
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

# The values the OpenAI-style `reasoning_effort` knows; which ones a model ACCEPTS is the
# provider's and model's business (C2 maps them, a wrong one comes back as a 400).
EFFORTS = frozenset({"none", "minimal", "low", "medium", "high"})
# Every provider the code KNOWS, configured or not: "groq:x" with no Groq key must be an error
# ("set GROQ_API_KEY"), not an OpenRouter model id called "groq:x" (found by test_factory).
KNOWN_PROVIDERS = frozenset({"openrouter", "groq"})


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    effort: str | None = None  # None: the provider's configured default

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}" + (f"@{self.effort}" if self.effort else "")


def parse_route(text: str, default_provider: str, providers: Collection[str]) -> Route:
    """'groq:qwen/qwen3-32b@none' -> Route. Raises ValueError with what's wrong and what's valid."""
    entry = text.strip()
    if not entry:
        raise ValueError("empty model entry")
    effort = None
    if "@" in entry:
        entry, effort = entry.rsplit("@", 1)
        if effort not in EFFORTS:
            raise ValueError(f"{text!r}: unknown effort {effort!r}, use one of {sorted(EFFORTS)}")
    prefix, sep, rest = entry.partition(":")
    if sep and (prefix in providers or prefix in KNOWN_PROVIDERS):
        provider, model = prefix, rest
    else:
        provider, model = default_provider, entry
    if not model:
        raise ValueError(f"{text!r}: no model after the provider")
    if provider not in providers:
        raise ValueError(
            f"{text!r}: provider {provider!r} is not configured (no {provider.upper()}_API_KEY?); "
            f"configured: {sorted(providers)}"
        )
    return Route(provider, model, effort)


def next_index(routes: Sequence[Route], current: int, dead: Collection[str]) -> int | None:
    """Where the fallback continues after routes[current] gave up: the index of the next route
    to try, or None when nothing is left.

    `dead`: providers that can't answer at all any more (their daily quota is gone, their key is
    rejected): every one of their routes is skipped, wherever it stands in the list.
    """
    for i in range(current + 1, len(routes)):
        if routes[i].provider not in dead:
            return i

    return None
