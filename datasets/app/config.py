"""Application settings, loaded from environment / .env (pydantic-settings).

All upstream API keys live here and are read server-side only. Nothing in this
module is ever returned to a client.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read the shared platform env first, then any service-local .env override.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # Deployment environment (dev|production). production refuses to start on dev-default secrets.
    env: str = "dev"

    # --- this service's own auth -------------------------------------------
    auth_disabled: bool = False
    # Comma-separated list of client keys accepted via the X-API-KEY header.
    datasets_api_keys: str = ""

    # --- US upstream credentials -------------------------------------------
    # SEC requires a descriptive User-Agent ("Sample Company name@example.com").
    sec_edgar_user_agent: str = "ValueGraph Datasets contact@example.com"
    fred_api_key: str = ""
    # BLS public API (labor/price series). Keyless works (25 queries/day); a free key raises
    # the limit to 500/day. The DBnomics BLS *mirror* froze at 2025-01, so we read BLS direct.
    bls_api_key: str = ""
    polygon_api_key: str = ""
    tiingo_api_key: str = ""
    fmp_api_key: str = ""
    # Real-time company news (production sources — Google News RSS is the keyless fallback).
    # Finnhub: US company-news (free tier 60/min, real-time). https://finnhub.io
    finnhub_api_key: str = ""
    # Naver Search Open API: KR company news (free 25k/day, native Korean coverage). Client
    # ID + Secret from https://developers.naver.com. Unset → KR news falls back to Google News.
    naver_client_id: str = ""
    naver_client_secret: str = ""
    # Logo.dev — optional publishable token for high-quality company logos by ticker/domain.
    # Unset → logos fall back to FMP profile images + domain favicons (hybrid resolver, /logos).
    logodev_token: str = ""
    # NYT Archive — era news headlines/abstracts by month back to 1851 (HL-5). Free key; the
    # provider self-limits to ~5 req/min (their guidance). Unset → the pre-2017 era-news path is dark.
    nyt_api_key: str = ""
    # API Ninjas (premium) — the earnings-call transcript source (US + KR, ~5y depth) + logos.
    # REQUIRED for the transcript feature; unset → transcripts stay dark.
    api_ninjas_key: str = ""
    transcript_ingest_limit: int = 8   # recent quarters of transcripts to index per ticker

    # Phase 2: 8-K EX-99 earnings/investor presentation decks (PDF) → GCP Document AI Layout Parser
    # → RAG (faithful, layout-aware chunks WITH page+bbox for precise in-app PDF highlight). Auth via
    # Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS → the same SA the reranker uses).
    # Unset processor → the deck feature stays dark (parsed text never fabricated).
    docai_project: str = ""
    docai_location: str = "us"          # Document AI processor region (us | eu)
    docai_processor_id: str = ""        # Layout Parser processor id (create in the GCP console)
    deck_ingest_limit: int = 4          # recent 8-K presentation decks to index per ticker
    kr_earnings_ingest_limit: int = 4   # recent KR 잠정실적 공정공시 disclosures to index per ticker

    # --- KR upstream credentials -------------------------------------------
    opendart_api_key: str = ""
    # Multiple OpenDART keys (comma-separated), rotated when a key's daily 사용한도 is spent
    # (status 020 → that key is parked until the KST-midnight reset and requests continue on
    # the next key). Falls back to the single OPENDART_API_KEY when unset.
    opendart_api_keys: str = ""
    # Per-key daily call limit (free keys: 20,000/day) — the admin quota panel's denominator.
    opendart_daily_limit: int = 20000
    ecos_api_key: str = ""
    # CE-12: Korea Investment & Securities (KIS) — KR realtime rankings + investor flows.
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_domain: str = "https://openapi.koreainvestment.com:9443"  # 실전; 모의 = openapivts:29443
    krx_api_key: str = ""

    # --- per-domain provider selection (override the free defaults) --------
    # IMP-15: "auto" = fallback chain with honest source labels — Yahoo primary,
    # then Stooq (US, keyless) / KIS (KR, only when the broker keys are set). A
    # transient Yahoo 503 no longer takes prices down.
    prices_provider_us: str = "auto"  # auto | yahoo | stooq
    prices_provider_kr: str = "auto"  # auto | yahoo | pykrx | kis
    # macro: FRED's api.stlouisfred.org serves a JS bot-wall to datacenter IPs, so
    # US macro breaks in the cloud. "auto" tries FRED when FRED_API_KEY is set and
    # falls back to keyless, cloud-safe DBnomics (BIS policy rates); "dbnomics"
    # forces the keyless path; "fred" forces FRED only.
    macro_provider_us: str = "auto"  # auto | fred | dbnomics

    # --- infra -------------------------------------------------------------
    # Ingestion store. SQLite by default (zero-setup, dev); set a postgresql://
    # URL in production. Used by the screener / line-items search.
    database_url: str = "sqlite:///./datasets.db"
    redis_url: str = ""
    cache_ttl_seconds: int = 900
    # HI-4: bound the in-memory TTLCache — it never evicted expired entries (only replaced on refresh)
    # and the single-flight locks accumulated forever → an OOM path (a 500-ticker sweep pins GBs).
    cache_max_entries: int = 2000             # CACHE_MAX_ENTRIES (LRU cap)
    http_timeout_seconds: float = 30.0
    # CR-9/ME-11 / SC-1.5: bound upstream load. A short shared TTL cache on Yahoo chart fetches
    # (single-flight collapses N concurrent users on one ticker → 1 upstream call); a fan-out
    # concurrency cap on market snapshots; a per-provider client-side rate limit (SEC ~10 req/s).
    yahoo_price_cache_ttl_seconds: int = 45   # YAHOO_PRICE_CACHE_TTL_SECONDS
    price_fanout_concurrency: int = 8         # PRICE_FANOUT_CONCURRENCY
    sec_edgar_rate_per_sec: int = 10          # SEC_EDGAR_RATE_PER_SEC (EDGAR guideline)
    yahoo_rate_per_sec: int = 0               # YAHOO_RATE_PER_SEC (0 = unthrottled; bounds burst if set)
    # ING-1: the RAG-ingest client budget scales with doc count (a large filing embeds hundreds
    # of chunks). read = min(base + per_doc × docs, max). With the swap atomic, an over-budget
    # request is only wasted spend (never corruption), and the one retry re-confirms for ~free.
    rag_ingest_timeout_base_seconds: float = 120.0
    rag_ingest_timeout_per_doc_seconds: float = 6.0
    rag_ingest_timeout_max_seconds: float = 1200.0
    log_level: str = "INFO"  # app log verbosity (DEBUG|INFO|WARNING|…) → docker logs

    # --- periodic ingestion: the Procrastinate queue (app/queue.py) -------
    # The cron sweeps live in app/queue.py (@app.periodic); the `worker` compose service runs them.
    # This is the universe each sweep refreshes — DYNAMIC source ids (see app/store/universes.py),
    # fetched fresh every sweep: "us_sp500,kr_kospi200,kr_kosdaq150" (also us_all / kr_kospi_all /
    # kr_kosdaq_all), and/or the legacy explicit form "US:AAPL,MSFT;KR:005930". Empty → sweeps no-op.
    scheduler_universe: str = "us_sp500,kr_kospi200,kr_kosdaq150"
    # CE-0: how many years of daily OHLCV the prices pipeline stores. Deep enough for the
    # store-backed screener / quant / backtest (the chart fetches its own history live).
    # Raised 5→10 for History Lab context on ordinary watchlist tickers (HL-1).
    prices_backfill_years: int = 10
    # HL-1: the History Lab anchor universe — Yahoo GLOBAL symbols whose FIRST ingest backfills
    # max available history (S&P daily since 1927, VIX since 1990, KOSPI ~1997 …). All stored
    # under the US namespace (Yahoo-global symbols; KR's 6-digit normalization would mangle
    # ^KS11). Depth = whatever the upstream returns; earlier is a DRAWN GAP, never fabricated.
    history_universe: str = "^GSPC,^IXIC,^DJI,^VIX,^KS11,^KQ11,^TNX,^N225,^HSI,GC=F,CL=F,KRW=X"
    # first-ingest start for universe symbols ("max" in practice; Yahoo clips to its first bar)
    history_backfill_start: str = "1920-01-01"
    # how many recent filings filing_search fetches+indexes when a never-seen ticker is queried
    # on-demand (bounded so the first call stays responsive).
    filing_search_ingest_limit: int = 2
    # HI-7: after an on-demand ingest still finds no filing text for a ticker, skip re-ingesting it
    # (a ticker with genuinely no matching filings — e.g. an ETF — otherwise re-ingests every search).
    filing_neg_cache_ttl_seconds: int = 900  # FILING_NEG_CACHE_TTL_SECONDS

    # --- RAG news-ingestion pipeline (PH-2b) ------------------------------
    # The RAG service the news pipeline indexes into.
    rag_url: str = "http://rag:8002"
    # Headlines fetched per ticker per news-ingest run.
    news_ingest_limit: int = 8

    # --- evidence: the in-app filing viewer -------------------------------
    # Where sanitized filing HTML is cached (shared by the viewer + filing-text RAG ingest),
    # on the datasets data volume.
    evidence_docs_dir: str = "/data/evidence_docs"

    @property
    def accepted_api_keys(self) -> set[str]:
        return {k.strip() for k in self.datasets_api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def assert_production_secrets() -> None:
    """SC-0/AUTH-1: ENV=production에서 데이터 플레인이 무인증이거나 기본 pg 비번이면 기동 거부.
    데이터셋은 8000에 공개되므로 프로덕션에선 반드시 키가 있어야 하고(미설정이면 아무 키나 통과,
    CR-10), auth_disabled=true로 열려 있어선 안 된다."""
    if settings.env.lower() not in ("production", "prod"):
        return
    leaked: list[str] = []
    if settings.auth_disabled:
        leaked.append("AUTH_DISABLED(=true는 프로덕션 금지)")
    elif not settings.datasets_api_keys.strip():
        leaked.append("DATASETS_API_KEYS(미설정이면 아무 키나 통과)")
    if "rag:rag@" in (settings.database_url or ""):
        leaked.append("DATABASE_URL(pg 기본 비밀번호 rag:rag)")
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")
