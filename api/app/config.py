"""Application settings, loaded from environment variables (and the repo-root .env in local dev)."""

from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

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


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton. Cached so .env is parsed once; tests can cache_clear()."""
    return Settings()
