"""Studio API settings (shared platform .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"
    # Trust token shared with the first-party web BFF.
    service_token: str = "dev-service-token"            # SERVICE_TOKEN
    # Control plane (for provisioning tenants/keys/activations) + its admin token.
    control_plane_url: str = "http://127.0.0.1:8010"    # CONTROL_PLANE_URL
    admin_token: str = "dev-admin-token"                 # ADMIN_TOKEN (shared with control-plane)
    # Agent engine (chat).
    agent_engine_url: str = "http://127.0.0.1:8003"      # AGENT_ENGINE_URL
    database_url: str = "sqlite:///./studio.db"          # DATABASE_URL
    http_timeout_seconds: float = 120.0
    # Notification-alert scheduler (F3): the background worker fires due alerts every tick.
    alerts_scheduler_enabled: bool = True                # ALERTS_SCHEDULER_ENABLED
    alerts_tick_seconds: int = 60                        # ALERTS_TICK_SECONDS


settings = Settings()

# Connectors auto-activated for a new user so the default agent has data.
DEFAULT_CONNECTORS = ["sec_edgar", "yahoo", "fred", "opendart", "ecos", "google_news", "datasets_store", "rag"]
