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
    # HI-1: size the Postgres pool + pre-ping (default QueuePool 5+10=15 with pre_ping off → stale-
    # connection errors + exhaustion under concurrency). SQLite (unit tests) keeps its default.
    db_pool_size: int = 10                               # DB_POOL_SIZE
    db_pool_max_overflow: int = 20                       # DB_POOL_MAX_OVERFLOW
    db_pool_recycle_seconds: int = 1800                  # DB_POOL_RECYCLE_SECONDS
    http_timeout_seconds: float = 120.0
    # HI-9: background chat runs (studioapi/runs.py). A global concurrency cap so a flood of turns
    # can't spawn unbounded detached tasks, a per-run deadline + watchdog that force-finishes a run
    # whose driver hangs (an upstream call that never returns) so it can't stay 'running' forever.
    run_max_concurrent: int = 96                         # RUN_MAX_CONCURRENT
    run_deadline_seconds: float = 300.0                  # RUN_DEADLINE_SECONDS
    run_watchdog_interval_seconds: float = 30.0          # RUN_WATCHDOG_INTERVAL_SECONDS
    # SC-2.3/CR-1: a run whose owning replica stops heartbeating for this long is presumed dead — the
    # reaper marks it 'reaped' and REFUNDS its consumed quota (the user got no answer). Keep it a few
    # watchdog intervals so a couple missed beats don't falsely reap a live-but-busy replica.
    run_heartbeat_stale_seconds: float = 120.0           # RUN_HEARTBEAT_STALE_SECONDS
    run_record_retention_hours: int = 24                 # RUN_RECORD_RETENTION_HOURS (GC finished rows)
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
    ask_feed_section_refresh_seconds: int = 3600         # ASK_FEED_SECTION_REFRESH_SECONDS (어닝·거장·히스토리 마키, 1h)
    # GUEST-1: 익명 체험 — 비로그인 방문자가 게스트로 2~3턴 맛보고 가입으로 이어지는 퍼널.
    feature_guest: bool = False                          # FEATURE_GUEST
    guest_turns_max: int = 3                             # GUEST_TURNS_MAX (디바이스당 평생)
    guest_turns_per_ip_day: int = 10                     # GUEST_TURNS_PER_IP_DAY (어뷰즈 백스톱)
    guest_ip_salt: str = "dev-guest-salt"                # GUEST_IP_SALT (ip_hash 솔트)
    # ME-4: 버려진 게스트 행 GC — 미클레임(claimed_by=NULL) 세션이 이 기간보다 오래되면 정리.
    # 종속 데이터(대화 등)가 남은 게스트 User 행은 FK RESTRICT로 건너뛴다(유실 방지).
    guest_session_ttl_days: int = 30                     # GUEST_SESSION_TTL_DAYS
    guest_gc_interval_seconds: int = 21600               # GUEST_GC_INTERVAL_SECONDS (6h)
    # PLAN-4 롤아웃 스위치: true면 기존 유저도 다음 요청에서 플랜 기준으로 커넥터를 reconcile
    # (free 유저의 fmp/kis 회수 포함). 기본 false — 켜기 전까지 기존 활성화는 건드리지 않는다.
    plan_enforce_connectors: bool = False                # PLAN_ENFORCE_CONNECTORS
    # ME-2: reconcile version — bump when the default connector set changes so existing users get the
    # new set activated ONCE (persisted on User.connectors_reconciled_ver, replacing a process-local
    # set that re-fired the reconcile herd on every replica after a deploy).
    connectors_reconcile_ver: str = "1"                  # CONNECTORS_RECONCILE_VER
    # AUTH-2: 이메일 OTP 로그인 발송 (Resend). 키 없으면 dev 모드 — 코드가 로그로만 남는다.
    resend_api_key: str = ""                             # RESEND_API_KEY
    email_from: str = "finnote <login@valuegraph.app>"  # EMAIL_FROM (Resend 도메인 인증 필요)
    # BILL: 토스페이먼츠 빌링 — 시크릿 키 없으면 FakeGateway(로컬/테스트). 빌링키는 Fernet 암호화.
    billing_enabled: bool = False                        # BILLING_ENABLED (스케줄러 틱 게이트)
    toss_secret_key: str = ""                            # TOSS_SECRET_KEY
    billing_enc_key: str = ""                            # BILLING_ENC_KEY (Fernet, 32b urlsafe b64)
    toss_webhook_secret: str = "dev-webhook-secret"      # TOSS_WEBHOOK_PATH_SECRET (URL 세그먼트 2차 인증)
    plan_price_pro_krw: int = 19900                      # PLAN_PRICE_PRO_KRW
    referral_kickback_monthly_cap_krw: int = 100000      # REFERRAL_KICKBACK_MONTHLY_CAP_KRW


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
    # 게스트 퍼널을 켰다면 ip_hash 솔트도 실값이어야 한다 (dev 솔트는 재식별 가능).
    if settings.feature_guest and settings.guest_ip_salt == "dev-guest-salt":
        leaked.append("GUEST_IP_SALT")
    if "rag:rag@" in (settings.database_url or ""):
        leaked.append("DATABASE_URL(pg 기본 비밀번호 rag:rag)")
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")
    # BILL: 실키 결제를 켰다면 빌링키 암호화 키·웹훅 시크릿도 실값이어야 한다
    if settings.toss_secret_key:
        if not settings.billing_enc_key:
            raise RuntimeError("production billing requires BILLING_ENC_KEY")
        if settings.toss_webhook_secret == "dev-webhook-secret":
            raise RuntimeError("production billing requires a real TOSS_WEBHOOK_PATH_SECRET")


# PLAN-4: connectors auto-activated at signup = the FREE plan set (single source: plans.py).
# Premium connectors (fmp 컨센서스 · kis 실시간 수급) are flipped on by plans.apply_plan when a
# user upgrades to pro — no longer part of every provisioning. Existing users keep whatever is
# already activated until PLAN_ENFORCE_CONNECTORS=true turns on the reconcile (rollout switch).
from studioapi.plans import FREE_CONNECTORS as DEFAULT_CONNECTORS  # noqa: E402
