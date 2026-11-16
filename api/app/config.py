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

    # Embeddings (3.2b). The model runs locally (fastembed / ONNX): no API, no quota.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: Path = Path.home() / ".cache" / "fastembed"  # ~130 MB, outside the repo
    chunk_max_tokens: int = 350  # measured with the model's tokenizer; the model's limit is 512

    # --- LLM gateway (OpenRouter). Optional: tests and CI run without a key (LLM is mocked).
    # SecretStr: never shows up in repr(), logs or error messages; read with .get_secret_value().
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    groq_api_key: SecretStr | None = None  # C: a second provider; free key, per-model limits
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # The fallback list across providers, tried in order: "provider:model[@effort]" entries, e.g.
    # "groq:openai/gpt-oss-120b@low,openrouter:google/gemma-4-31b-it:free". An entry without a
    # provider prefix is OpenRouter (so an old OPENROUTER_MODELS list still works: it is used
    # when this is empty).
    llm_fallbacks: Annotated[list[str], NoDecode] = []
    # Fallback order, best first. Comma-separated in .env; NoDecode stops pydantic-settings from
    # trying to parse it as JSON (its default for list fields).
    openrouter_models: Annotated[list[str], NoDecode] = []
    # The eval JUDGE's own fallback list (8.4), ideally another model family than the agent's:
    # a judge of the same family prefers its own style (self-preference). Empty = the agent's
    # list, and the eval report warns about it.
    judge_models: Annotated[list[str], NoDecode] = []
    llm_connect_timeout: float = 10.0  # is the server reachable at all?
    llm_timeout_seconds: float = 60.0  # read timeout: is the model still producing output?
    llm_max_retries: int = 1  # same-model retries (measured 5.2b: 2 starved the 3rd model)
    llm_max_attempts: int = 4  # total requests per gateway call, across all models
    # OpenRouter `reasoning.effort` for every request; "none" = thinking off where switchable.
    # Empty = the model's default. (5.2b: free reasoning models over-thought long prompts.)
    llm_reasoning_effort: str | None = "none"
    llm_deadline_seconds: float = 120.0  # wall clock for one gateway call incl. backoff
    # Prompt cache (2.3): answer repeated requests from Postgres, 0 tokens, ~ms. false = OFF
    # everywhere. TTL: free models change, a months-old answer no longer represents them.
    llm_cache_enabled: bool = True
    llm_cache_ttl_hours: float = 168.0
    # Free-tier requests per day (7.6: the dashboard's quota bar). ~50 for free models, ~1000
    # after a one-time credit purchase. Shown as a guide: FabPilot can't see requests made
    # outside FabPilot, and the reset time is not verified (see StatsTotals).
    llm_daily_request_limit: int = 50
    # Run budget of the incident agent (5.4b): provider requests and tokens per question. A
    # normal run measured 2 requests / ~5,700 tokens; these stop a runaway, not a normal run.
    agent_max_llm_attempts: int = 12
    agent_max_tokens: int = 40_000
    # Investigations running at once in one API process (6.1); more get HTTP 429 + Retry-After.
    agent_max_concurrent_runs: int = 2
    # Browser origins allowed to call the API (7.1): the dashboard. Comma-separated, no spaces.
    # Never "*" in production: any website could then read our API from a visitor's browser.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]

    @field_validator(
        "llm_fallbacks", "openrouter_models", "judge_models", "cors_origins", mode="before"
    )
    @classmethod
    def _split_comma_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [m.strip() for m in value.split(",") if m.strip()]
        return value

    @field_validator("openrouter_api_key", "groq_api_key", mode="before")
    @classmethod
    def _empty_key_is_no_key(cls, value: object) -> object:
        """`KEY=` (as in .env.example) and a missing GitHub secret both arrive as "": that is NO
        key, not a key. Otherwise a client is built with an empty key and fails later with a
        misleading SDK error (measured in CI 8.5: "set OPENAI_API_KEY"). Per field on purpose,
        not env_ignore_empty for all: LLM_REASONING_EFFORT= empty means "model default"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def fallbacks(self) -> list[str]:
        """The gateway's routes: LLM_FALLBACKS, or the older OPENROUTER_MODELS if that is all."""
        return self.llm_fallbacks or self.openrouter_models


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton. Cached so .env is parsed once; tests can cache_clear()."""
    return Settings()
