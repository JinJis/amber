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


settings = Settings()

# Connectors auto-activated for every project — the subscription model provides ALL data via
# server-side keys, so every connector in the catalog is entitled (users pick TOOLS per agent, not
# whole APIs). fmp (consensus/calendar, CE-11) + kis (KR realtime, CE-12) were missing, so those
# tools 403'd through the gateway; include them.
DEFAULT_CONNECTORS = ["sec_edgar", "yahoo", "fred", "opendart", "ecos", "google_news",
                      "datasets_store", "rag", "fmp", "kis", "market_history"]
