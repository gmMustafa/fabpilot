"""Build the gateway from settings: the one place the rest of the app gets an LLM from."""

from app.config import Settings
from app.gateway.gateway import GatewayConfig, LLMGateway
from app.gateway.openrouter import OpenRouterProvider


def build_gateway(settings: Settings) -> LLMGateway:
    """OpenRouter gateway with the configured fallback list, retries and budgets."""
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
    return LLMGateway(provider, config)
