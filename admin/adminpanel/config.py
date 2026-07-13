"""Admin panel settings (reads the shared platform .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"
    # Deployment environment (dev|production). production refuses to start on dev-default secrets.
    env: str = "dev"

    # login (single credential; change in production)
    adminui_username: str = "admin"               # ADMINUI_USERNAME
    adminui_password: str = "admin"               # ADMINUI_PASSWORD
    adminui_secret: str = "dev-adminui-secret-change-me"  # ADMINUI_SECRET (session signing)
    # SC-0.2: optional source-IP allowlist (comma-separated IPs/CIDRs). Empty = allow all (dev).
    # When set, requests from other IPs are refused before the login form (defense behind a proxy).
    adminui_ip_allowlist: str = ""                # ADMINUI_IP_ALLOWLIST

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
    studio_url: str = "http://studio-api:8004"    # STUDIO_URL (Macro Trends 수동 갱신 ops)
    service_token: str = "dev-service-token"      # SERVICE_TOKEN (studio-api first-party guard)


settings = Settings()


def assert_production_secrets() -> None:
    """SC-0/AUTH-1/CR-10: ENV=production에서 admin 콘솔이 dev 크레덴셜·세션 시크릿·토큰으로 남아
    있으면 기동 거부. 이 패널은 모든 서비스 DB에 대한 CRUD를 쥐고 있어 잘 알려진 값으로는 절대 열 수
    없다. 세션 시크릿이 dev면 쿠키 위조가 가능하다."""
    if settings.env.lower() not in ("production", "prod"):
        return
    leaked = [name for name, value, dev_default in (
        ("ADMINUI_USERNAME", settings.adminui_username, "admin"),
        ("ADMINUI_PASSWORD", settings.adminui_password, "admin"),
        ("ADMINUI_SECRET", settings.adminui_secret, "dev-adminui-secret-change-me"),
        ("ADMIN_TOKEN", settings.admin_token, "dev-admin-token"),
        ("SERVICE_TOKEN", settings.service_token, "dev-service-token"),
    ) if value == dev_default]
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")


# (key, display title, sqlalchemy url)
DATABASES = [
    ("controlplane", "Control plane", settings.controlplane_db),
    ("studio", "Studio", settings.studio_db),
    ("datasets", "Data plane", settings.datasets_db),
]
