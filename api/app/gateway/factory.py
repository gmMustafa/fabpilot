"""Build the gateway from settings: the one place the rest of the app gets an LLM from."""

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.gateway.gateway import GatewayConfig, LLMGateway
from app.gateway.openrouter import OpenRouterProvider
from app.gateway.recorder import DbCallRecorder


def build_gateway(settings: Settings, *, engine: AsyncEngine | None = None) -> LLMGateway:
    """OpenRouter gateway with the configured fallback list, retries and budgets.

    With `engine`, every attempt is logged to llm_calls (2.2).
    """
    if settings.openrouter_api_key is None:
        raise RuntimeError("OPENROUTER_API_KEY is not set (see .env.example)")
    provider = OpenRouterProvider(
        settings.openrouter_api_key.get_secret_value(),
        base_url=settings.openrouter_base_url,
        connect_timeout=settings.llm_connect_timeout,
        read_timeout=settings.llm_timeout_seconds,
    )
    config = GatewayConfig(
        models=settings.openrouter_models,
        max_retries_per_model=settings.llm_max_retries,
        max_attempts=settings.llm_max_attempts,
        deadline=settings.llm_deadline_seconds,
    )
    recorder = DbCallRecorder(engine) if engine is not None else None
    return LLMGateway(provider, config, recorder=recorder)
