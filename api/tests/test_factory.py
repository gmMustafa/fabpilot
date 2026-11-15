"""Building the gateway from settings (C3): which providers, which routes, which startup errors.
No network: constructing a provider makes no request."""

import pytest
from pydantic import SecretStr

from app.config import Settings, get_settings
from app.gateway.factory import build_gateway, build_providers
from app.gateway.routes import Route


def settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "openrouter_api_key": None,
        "groq_api_key": None,
        "llm_fallbacks": [],
        "openrouter_models": [],
    }
    return get_settings().model_copy(update=base | over)


def test_only_providers_with_a_key_are_built() -> None:
    assert list(build_providers(settings(groq_api_key=SecretStr("gsk_x")))) == ["groq"]
    both = settings(openrouter_api_key=SecretStr("sk-or-x"), groq_api_key=SecretStr("gsk_x"))
    assert list(build_providers(both)) == ["openrouter", "groq"]  # openrouter = the default


def test_no_key_at_all_is_a_clear_startup_error() -> None:
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY and/or GROQ_API_KEY"):
        build_providers(settings())


def test_llm_fallbacks_become_routes_across_providers() -> None:
    gw = build_gateway(
        settings(
            openrouter_api_key=SecretStr("sk-or-x"),
            groq_api_key=SecretStr("gsk_x"),
            llm_fallbacks=["groq:openai/gpt-oss-120b@low", "openrouter:google/gemma-4-31b-it:free"],
        )
    )
    assert gw.routes == [
        Route("groq", "openai/gpt-oss-120b", "low"),
        Route("openrouter", "google/gemma-4-31b-it:free"),
    ]


def test_the_old_openrouter_models_still_works() -> None:
    gw = build_gateway(
        settings(openrouter_api_key=SecretStr("sk-or-x"), openrouter_models=["nvidia/x:free"])
    )
    assert gw.routes == [Route("openrouter", "nvidia/x:free")]


def test_a_route_to_a_provider_without_a_key_fails_at_startup() -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_gateway(
            settings(
                openrouter_api_key=SecretStr("sk-or-x"), llm_fallbacks=["groq:openai/gpt-oss-120b"]
            )
        )
