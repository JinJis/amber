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
    # Alpha Vantage — earnings-call transcripts (free key works; rate-limited). US coverage.
    alphavantage_api_key: str = ""
    transcript_ingest_limit: int = 4   # recent quarters of transcripts to index per ticker

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
    ecos_api_key: str = ""
    # CE-12: Korea Investment & Securities (KIS) — KR realtime rankings + investor flows.
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_domain: str = "https://openapi.koreainvestment.com:9443"  # 실전; 모의 = openapivts:29443
    krx_api_key: str = ""

    # --- per-domain provider selection (override the free defaults) --------
    prices_provider_us: str = "yahoo"  # yahoo | stooq | polygon | tiingo | fmp
    prices_provider_kr: str = "yahoo"  # yahoo | pykrx | krx | kis
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
    http_timeout_seconds: float = 30.0
    log_level: str = "INFO"  # app log verbosity (DEBUG|INFO|WARNING|…) → docker logs

    # --- periodic ingestion: the Procrastinate queue (app/queue.py) -------
    # The cron sweeps live in app/queue.py (@app.periodic); the `worker` compose service runs them.
    # This is the universe each sweep refreshes — DYNAMIC source ids (see app/store/universes.py),
    # fetched fresh every sweep: "us_sp500,kr_kospi200,kr_kosdaq150" (also us_all / kr_kospi_all /
    # kr_kosdaq_all), and/or the legacy explicit form "US:AAPL,MSFT;KR:005930". Empty → sweeps no-op.
    scheduler_universe: str = "us_sp500,kr_kospi200,kr_kosdaq150"
    # CE-0: how many years of daily OHLCV the prices pipeline stores. Deep enough for the
    # store-backed screener / quant / backtest (the chart fetches its own history live).
    prices_backfill_years: int = 5
    # how many recent filings filing_search fetches+indexes when a never-seen ticker is queried
    # on-demand (bounded so the first call stays responsive).
    filing_search_ingest_limit: int = 2

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
