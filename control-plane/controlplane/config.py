"""Control-plane settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read the shared platform env first, then any service-local .env override.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"

    # Control-plane store (tenants, keys, activations, usage, audit).
    database_url: str = "sqlite:///./controlplane.db"
    # The backend services this gateway fronts (chosen per connector via its manifest `service`).
    datasets_url: str = "http://127.0.0.1:8000"
    rag_url: str = "http://127.0.0.1:8002"
    redis_url: str = ""
    # Guards the /admin management endpoints (X-Admin-Token header).
    admin_token: str = "dev-admin-token"
    rate_limit_per_minute: int = 120
    http_timeout_seconds: float = 30.0


settings = Settings()

# Cost units charged per request, by the matched connector's cost tier.
COST_UNITS = {"free": 0, "low": 1, "medium": 5, "high": 20}
