"""Control-plane settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read the shared platform env first, then any service-local .env override.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"
    # Deployment environment (dev|production). production refuses to start on dev-default secrets.
    env: str = "dev"

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


def assert_production_secrets() -> None:
    """AUTH-1: ENV=production에서 admin 토큰이 dev 기본값이면 기동 거부 — /admin은 테넌트·키·
    엔타이틀먼트 전체를 쥐고 있어 잘 알려진 토큰으로는 절대 열어둘 수 없다."""
    if settings.env.lower() not in ("production", "prod"):
        return
    if settings.admin_token == "dev-admin-token":
        raise RuntimeError("production requires a real ADMIN_TOKEN (dev default refused)")


# Cost units charged per request, by the matched connector's cost tier.
COST_UNITS = {"free": 0, "low": 1, "medium": 5, "high": 20}
