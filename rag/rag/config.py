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
    top_k: int = 8
    rerank_top_n: int = 5
    http_timeout_seconds: float = 60.0


settings = Settings()
