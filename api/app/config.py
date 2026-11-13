"""Application settings, loaded from environment variables (and the repo-root .env in local dev)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# api/app/config.py -> parents[2] is the repo root, where .env lives. Resolving from this file
# (not the current working directory) means it works whether you run from api/ or the repo root.
# In Docker/CI there is no .env file: pydantic-settings skips a missing file, reads real env vars.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT_ENV = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        # .env is shared with docker compose and other services; ignore vars this app doesn't use.
        extra="ignore",
    )

    # Required, no default: the app refuses to start without it (fail fast, not on first request).
    # PostgresDsn validates the URL shape, e.g. postgresql+psycopg://user:pass@host:5433/db
    database_url: PostgresDsn
    # Seconds to wait for a TCP connection to Postgres. Keeps /health fast when the DB is down.
    db_connect_timeout: int = 3
    log_level: str = "INFO"
    # Plant layout shared by seed / simulator / evals. Overridable (e.g. a path inside a container).
    plant_file: Path = _REPO_ROOT / "data" / "plant.yaml"
    docs_dir: Path = _REPO_ROOT / "data" / "docs"  # knowledge base markdown (3.1)

    # --- LLM gateway (OpenRouter). Optional: tests and CI run without a key (LLM is mocked).
    # SecretStr: never shows up in repr(), logs or error messages; read with .get_secret_value().
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Fallback order, best first. Comma-separated in .env; NoDecode stops pydantic-settings from
    # trying to parse it as JSON (its default for list fields).
    openrouter_models: Annotated[list[str], NoDecode] = []
    llm_connect_timeout: float = 10.0  # is the server reachable at all?
    llm_timeout_seconds: float = 60.0  # read timeout: is the model still producing output?
    llm_max_retries: int = 2  # same-model retries for transient errors
    llm_max_attempts: int = 4  # total requests per gateway call, across all models
    llm_deadline_seconds: float = 120.0  # wall clock for one gateway call incl. backoff
    # Prompt cache (2.3): answer repeated requests from Postgres, 0 tokens, ~ms. false = OFF
    # everywhere. TTL: free models change, a months-old answer no longer represents them.
    llm_cache_enabled: bool = True
    llm_cache_ttl_hours: float = 168.0

    @field_validator("openrouter_models", mode="before")
    @classmethod
    def _split_models(cls, value: object) -> object:
        if isinstance(value, str):
            return [m.strip() for m in value.split(",") if m.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton. Cached so .env is parsed once; tests can cache_clear()."""
    return Settings()
