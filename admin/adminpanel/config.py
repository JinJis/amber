"""Admin panel settings (reads the shared platform .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"

    # login (single credential; change in production)
    adminui_username: str = "admin"               # ADMINUI_USERNAME
    adminui_password: str = "admin"               # ADMINUI_PASSWORD
    adminui_secret: str = "dev-adminui-secret-change-me"  # ADMINUI_SECRET (session signing)

    # service databases (SQLite files mounted from each service's volume)
    controlplane_db: str = "sqlite:////dbs/controlplane/controlplane.db"
    studio_db: str = "sqlite:////dbs/studio/studio.db"
    datasets_db: str = "sqlite:////dbs/datasets/datasets.db"

    # ops targets (in-cluster service URLs)
    datasets_url: str = "http://datasets:8000"
    rag_url: str = "http://rag:8002"
    gateway_url: str = "http://control-plane:8001"
    agent_engine_url: str = "http://agent-engine:8003"   # AGENT_ENGINE_URL (agent /agent/info)
    admin_token: str = "dev-admin-token"          # ADMIN_TOKEN, for control-plane admin proxies


settings = Settings()

# (key, display title, sqlalchemy url)
DATABASES = [
    ("controlplane", "Control plane", settings.controlplane_db),
    ("studio", "Studio", settings.studio_db),
    ("datasets", "Data plane", settings.datasets_db),
]
