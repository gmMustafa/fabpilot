"""Build the gateway from settings: every provider that has a key, and the fallback routes."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.gateway.cache import DbPromptCache
from app.gateway.gateway import GatewayConfig, LLMGateway
from app.gateway.groq import GroqProvider
from app.gateway.openrouter import OpenRouterProvider
from app.gateway.recorder import DbCallRecorder
from app.gateway.types import LLMProvider


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """One provider per configured key. OpenRouter first: an entry without a provider prefix
    belongs to the FIRST provider, and the old OPENROUTER_MODELS lists have none."""
    providers: dict[str, LLMProvider] = {}
    if settings.openrouter_api_key is not None:
        providers["openrouter"] = OpenRouterProvider(
            settings.openrouter_api_key.get_secret_value(),
            base_url=settings.openrouter_base_url,
            connect_timeout=settings.llm_connect_timeout,
            read_timeout=settings.llm_timeout_seconds,
            reasoning_effort=settings.llm_reasoning_effort,
        )
    if settings.groq_api_key is not None:
        providers["groq"] = GroqProvider(
            settings.groq_api_key.get_secret_value(),
            base_url=settings.groq_base_url,
            connect_timeout=settings.llm_connect_timeout,
            read_timeout=settings.llm_timeout_seconds,
        )
    if not providers:
        raise RuntimeError("no LLM key set: OPENROUTER_API_KEY and/or GROQ_API_KEY (.env.example)")
    return providers


def build_gateway(settings: Settings, *, engine: AsyncEngine | None = None) -> LLMGateway:
    """The gateway over every configured provider, with LLM_FALLBACKS as its route list.

    With `engine`: every attempt is logged to llm_calls (2.2), and, if LLM_CACHE_ENABLED, repeated
    requests are answered from llm_cache (2.3). A route naming a provider without a key fails
    HERE, at startup, with what to set: not on the first question.
    """
    config = GatewayConfig(
        models=settings.fallbacks,
        max_retries_per_model=settings.llm_max_retries,
        max_attempts=settings.llm_max_attempts,
        deadline=settings.llm_deadline_seconds,
    )
    recorder = DbCallRecorder(engine) if engine is not None else None
    cache = DbPromptCache(engine) if engine is not None and settings.llm_cache_enabled else None
    return LLMGateway(
        build_providers(settings),
        config,
        recorder=recorder,
        cache=cache,
        cache_ttl=timedelta(hours=settings.llm_cache_ttl_hours),
    )
