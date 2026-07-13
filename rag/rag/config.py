"""RAG settings (RAG_* in the shared .env).

Embeddings are Gemini-only: ``gemini-embedding-2`` (latest) via the Gemini API with GOOGLE_API_KEY
(the same key the agent uses). Vector store is in-memory (dev) or pgvector (prod).
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAG_", env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # App log verbosity (DEBUG|INFO|WARNING|…); a bare shared `LOG_LEVEL` env overrides it.
    log_level: str = "INFO"

    # --- embeddings (Gemini only) -----------------------------------------
    # Google Gemini embeddings via the Gemini API (GOOGLE_API_KEY). output dim is MRL-truncated to
    # embedding_dim then re-normalized.
    embedding_model: str = "gemini-embedding-2"   # latest (multimodal); or gemini-embedding-001
    embedding_dim: int = 1536                # 768 | 1536 | 3072 (1536 = strong + pgvector-indexable)

    # COST-1: embedding token usage telemetry → control-plane admin API (best-effort).
    # Env (RAG_ prefix): RAG_CONTROL_PLANE_URL / RAG_ADMIN_TOKEN.
    control_plane_url: str = "http://control-plane:8001"
    admin_token: str = "dev-admin-token"

    # --- reranker ----------------------------------------------------------
    reranker_backend: str = "none"           # none | gcp (Vertex Ranking API)
    reranker_model: str = "semantic-ranker-default-004"
    reranker_endpoint: str = ""

    # --- vector store ------------------------------------------------------
    vector_store: str = "memory"             # memory | pgvector
    database_url: str = ""                    # pgvector (postgresql://...)
    # CR-7/SC-1.4: a connection POOL (one per process) replaces the per-query psycopg.connect +
    # register_vector — a single search fans out to ~6 legs, so a pool amortizes the handshakes.
    pg_pool_min_size: int = 2                 # RAG_PG_POOL_MIN_SIZE (warm connections)
    pg_pool_max_size: int = 16               # RAG_PG_POOL_MAX_SIZE (fan-out ceiling)
    # CR-8: HNSW search tuning, applied per search txn via set_config(..., is_local=true) so it never
    # leaks to a pooled connection's next borrower. ef_search 0 = auto (max(candidate_k*2, 100)) — the
    # pgvector default 40 under-returns below LIMIT when a post-filter (tenant/doc_type) prunes the ANN
    # candidates. iterative_scan '' disables it (needs pgvector >= 0.8). statement_timeout caps a
    # pathological scan at the DB (strictly larger than the app-side embed/rerank budgets).
    hnsw_ef_search: int = 0                   # RAG_HNSW_EF_SEARCH (0 = auto)
    hnsw_iterative_scan: str = "relaxed_order"  # RAG_HNSW_ITERATIVE_SCAN ('' = off)
    search_statement_timeout_ms: int = 15000  # RAG_SEARCH_STATEMENT_TIMEOUT_MS
    # CR-8: the pg_trgm lexical leg measured 22s cold / 0 rows on short (≤3-token) queries — the most
    # common shape — so it's OFF by default (the dense leg carries name-like recall). Flip on to restore.
    lexical_trgm_enabled: bool = False        # RAG_LEXICAL_TRGM_ENABLED

    # --- google cloud (only for the optional gcp Vertex Ranking reranker) -----
    gcp_project: str = ""
    gcp_location: str = "global"  # the Vertex Ranking API (semantic ranker) is global-only
    gcp_ranking_config: str = "default_ranking_config"

    # --- retrieval ---------------------------------------------------------
    top_k: int = 8            # hits returned to the caller
    candidate_k: int = 64     # RQ-1: wide hybrid candidate pool (dense+lexical) fed to the reranker
    # RQ-3: multi-query expansion — 쿼리를 한↔영·키워드형 변형 2개로 확장해 모든 레그를 RRF 융합
    # (recall 상승; 변형 생성 실패는 원쿼리 단독으로 무해 강등). RAG_MULTI_QUERY=false로 끔.
    multi_query: bool = True
    multi_query_model: str = "gemini-flash-lite-latest"
    rerank_top_n: int = 5     # (legacy — the funnel now reranks candidates down to top_k)
    # Budget on the query-embedding call (an external model API). Past it the dense leg is
    # skipped and the LEXICAL leg still answers — search never blows the gateway timeout.
    embed_query_timeout_seconds: float = 10.0
    # ING-1: embed sub-batches concurrently (bounded) so a large filing's many 64-text calls
    # don't run back-to-back. `embed_concurrency=1` restores the sequential path (rollback knob).
    embed_concurrency: int = 4
    embed_batch: int = 64        # texts per embed_content request
    # RQ-10: cap on rows the lexical OR-pass ranks (ts_rank re-parses each row's text; an OR of
    # common tokens can match ~10% of the corpus → seconds/leg). AND-pass runs first unbounded.
    lexical_candidate_limit: int = 4000
    http_timeout_seconds: float = 60.0


settings = Settings()


def assert_production_secrets() -> None:
    """SC-0/AUTH-1: ENV=production에서 dev 기본 텔레메트리 토큰·기본 pg 비번이 남아 있으면 기동 거부.
    (RAG_ 프리픽스 설정이라 배포 공통 플래그 ENV는 os.environ에서 직접 읽는다.)"""
    if os.environ.get("ENV", "dev").lower() not in ("production", "prod"):
        return
    leaked: list[str] = []
    if settings.admin_token == "dev-admin-token":
        leaked.append("RAG_ADMIN_TOKEN")
    if "rag:rag@" in (settings.database_url or ""):
        leaked.append("RAG_DATABASE_URL(pg 기본 비밀번호 rag:rag)")
    if leaked:
        raise RuntimeError(f"production requires real secrets for: {', '.join(leaked)}")
