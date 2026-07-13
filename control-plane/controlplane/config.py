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
    # HI-1: this DB backs the GATEWAY (every entitlement/meter/audit) — size it LARGEST + pre-ping.
    # (default QueuePool 5+10=15 with pre_ping off is the tightest bottleneck under load.)
    db_pool_size: int = 20                             # DB_POOL_SIZE
    db_pool_max_overflow: int = 40                     # DB_POOL_MAX_OVERFLOW
    db_pool_recycle_seconds: int = 1800               # DB_POOL_RECYCLE_SECONDS
    # The backend services this gateway fronts (chosen per connector via its manifest `service`).
    datasets_url: str = "http://127.0.0.1:8000"
    rag_url: str = "http://127.0.0.1:8002"
    redis_url: str = ""
    # Guards the /admin management endpoints (X-Admin-Token header).
    admin_token: str = "dev-admin-token"
    rate_limit_per_minute: int = 120
    http_timeout_seconds: float = 30.0
    # CR-4/SC-1.3: the gateway hot path did 4 sync DB round-trips per proxied call (auth SELECT +
    # entitlement SELECT + meter INSERT+COMMIT + audit INSERT+COMMIT), blocking the event loop and
    # capping throughput at ~50-100 req/s. Cache auth+entitlement (TTL) and batch meter/audit writes.
    gateway_auth_cache_ttl_seconds: float = 30.0        # GATEWAY_AUTH_CACHE_TTL_SECONDS
    gateway_entitlement_cache_ttl_seconds: float = 30.0  # GATEWAY_ENTITLEMENT_CACHE_TTL_SECONDS
    gateway_flush_interval_seconds: float = 2.0          # GATEWAY_FLUSH_INTERVAL_SECONDS (meter/audit)
    gateway_queue_max: int = 20000                       # GATEWAY_QUEUE_MAX (drop-oldest backstop)
    # RAG search embeds the query via an external model API and can run long under concurrent
    # ingest — a 30s proxy cap silently dropped ALL RAG evidence from a turn (502). RAG-bound
    # requests get their own, longer budget.
    rag_http_timeout_seconds: float = 90.0
    # HI-13: usage_events + audit_log append 2 rows per proxied call (~2.6 GB/day at 100 req/s) on the
    # OLTP instance. A daily retention job rolls usage_events into cumulative per-project totals
    # (usage_rollup) then drops the raw rows past this window; audit_log rows are dropped outright.
    usage_retention_days: int = 90                       # USAGE_RETENTION_DAYS
    audit_retention_days: int = 30                       # AUDIT_RETENTION_DAYS
    retention_interval_seconds: int = 86400              # RETENTION_INTERVAL_SECONDS (daily)


settings = Settings()


def assert_production_secrets() -> None:
    """AUTH-1 / SC-0: ENV=production에서 잘 알려진 dev 기본값이 남아 있으면 기동 거부 — /admin은
    테넌트·키·엔타이틀먼트 전체를 쥐고 있어 잘 알려진 토큰으로는 절대 열어둘 수 없다."""
    if settings.env.lower() not in ("production", "prod"):
        return
    leaked = [name for name, value, dev_default in (
        ("ADMIN_TOKEN", settings.admin_token, "dev-admin-token"),
    ) if value == dev_default]
    if "rag:rag@" in (settings.database_url or ""):
        leaked.append("DATABASE_URL(pg 기본 비밀번호 rag:rag)")
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")


# Cost units charged per request, by the matched connector's cost tier.
COST_UNITS = {"free": 0, "low": 1, "medium": 5, "high": 20}
