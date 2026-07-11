"""RAG settings (RAG_* in the shared .env).

Embeddings are Gemini-only: ``gemini-embedding-2`` (latest) via the Gemini API with GOOGLE_API_KEY
(the same key the agent uses). Vector store is in-memory (dev) or pgvector (prod).
"""

from __future__ import annotations

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
    # RQ-10: cap on rows the lexical OR-pass ranks (ts_rank re-parses each row's text; an OR of
    # common tokens can match ~10% of the corpus → seconds/leg). AND-pass runs first unbounded.
    lexical_candidate_limit: int = 4000
    http_timeout_seconds: float = 60.0


settings = Settings()
