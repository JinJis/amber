"""Studio API settings (shared platform .env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"
    # Deployment environment (dev|production). production refuses to start on dev-default secrets.
    env: str = "dev"                                     # ENV
    # Trust token shared with the first-party web BFF.
    service_token: str = "dev-service-token"            # SERVICE_TOKEN
    # Control plane (for provisioning tenants/keys/activations) + its admin token.
    control_plane_url: str = "http://127.0.0.1:8010"    # CONTROL_PLANE_URL
    admin_token: str = "dev-admin-token"                 # ADMIN_TOKEN (shared with control-plane)
    # Agent engine (chat).
    agent_engine_url: str = "http://127.0.0.1:8003"      # AGENT_ENGINE_URL
    database_url: str = "sqlite:///./studio.db"          # DATABASE_URL
    http_timeout_seconds: float = 120.0
    # M-SHARE: the public base URL share links point at (the web app) + per-user active-share cap.
    public_base_url: str = "http://localhost:3000"       # PUBLIC_BASE_URL
    shares_per_user_cap: int = 200                        # SHARES_PER_USER_CAP
    share_ttl_days: int = 90                              # SHARE_TTL_DAYS (IMP-13)
    # M-DESK: desk-feed cache TTL — within it GET /desk-feed serves the stored payload without an
    # agent-engine call. Invalidated early on any watchlist change.
    desk_feed_ttl_seconds: int = 2700                    # DESK_FEED_TTL_SECONDS (45min)
    # IMP-10: hard cap on one desk-feed generation call (gather + Gemini synthesis)
    desk_feed_generate_timeout_seconds: float = 45.0     # DESK_FEED_GENERATE_TIMEOUT_SECONDS
    # Chat-first feature flag (FLAG-1): the 알림봇 surface. Default OFF — the alert scheduler does not
    # start unless this is on (shares the FEATURE_ALERTS env with the web rail so both flip together).
    feature_alerts: bool = False                         # FEATURE_ALERTS
    # Notification-alert scheduler (F3): the background worker fires due alerts every tick. Gated by
    # feature_alerts above; this second switch stays as a fine-grained kill-switch (disable in a
    # flags-on install without touching the UI).
    alerts_scheduler_enabled: bool = True                # ALERTS_SCHEDULER_ENABLED
    alerts_tick_seconds: int = 60                        # ALERTS_TICK_SECONDS
    # ASK-6: the 물어보기 entry feed — background news_feed questions + on-demand ticker pools.
    ask_feed_enabled: bool = True                        # ASK_FEED_ENABLED
    ask_feed_refresh_seconds: int = 300                  # ASK_FEED_REFRESH_SECONDS (Macro Trends, 5 min)
    ask_feed_ticker_ttl_seconds: int = 1800              # ASK_FEED_TICKER_TTL_SECONDS (per-ticker cache, 30 min)
    ask_feed_generate_timeout_seconds: float = 45.0      # ASK_FEED_GENERATE_TIMEOUT_SECONDS (one gather+synth)
    # GUEST-1: 익명 체험 — 비로그인 방문자가 게스트로 2~3턴 맛보고 가입으로 이어지는 퍼널.
    feature_guest: bool = False                          # FEATURE_GUEST
    guest_turns_max: int = 3                             # GUEST_TURNS_MAX (디바이스당 평생)
    guest_turns_per_ip_day: int = 10                     # GUEST_TURNS_PER_IP_DAY (어뷰즈 백스톱)
    guest_ip_salt: str = "dev-guest-salt"                # GUEST_IP_SALT (ip_hash 솔트)
    # PLAN-4 롤아웃 스위치: true면 기존 유저도 다음 요청에서 플랜 기준으로 커넥터를 reconcile
    # (free 유저의 fmp/kis 회수 포함). 기본 false — 켜기 전까지 기존 활성화는 건드리지 않는다.
    plan_enforce_connectors: bool = False                # PLAN_ENFORCE_CONNECTORS


settings = Settings()


def assert_production_secrets() -> None:
    """AUTH-1: ENV=production에서 잘 알려진 dev 기본 토큰이 남아 있으면 기동을 거부한다 —
    조용한 폴백은 BFF↔studio↔control-plane 신뢰 경계를 통째로 여는 것과 같다."""
    if settings.env.lower() not in ("production", "prod"):
        return
    leaked = [name for name, value, dev_default in (
        ("SERVICE_TOKEN", settings.service_token, "dev-service-token"),
        ("ADMIN_TOKEN", settings.admin_token, "dev-admin-token"),
    ) if value == dev_default]
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")


# PLAN-4: connectors auto-activated at signup = the FREE plan set (single source: plans.py).
# Premium connectors (fmp 컨센서스 · kis 실시간 수급) are flipped on by plans.apply_plan when a
# user upgrades to pro — no longer part of every provisioning. Existing users keep whatever is
# already activated until PLAN_ENFORCE_CONNECTORS=true turns on the reconcile (rollout switch).
from studioapi.plans import FREE_CONNECTORS as DEFAULT_CONNECTORS  # noqa: E402
