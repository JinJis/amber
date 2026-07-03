"""Query -> embed -> vector search -> (optional) rerank -> hits with provenance."""

from __future__ import annotations

import logging

from rag.config import settings
from rag.embeddings import get_embedder
from rag.models import SearchHit
from rag.rerank import get_reranker
from rag.store import get_store

logger = logging.getLogger(__name__)


async def search(query: str, top_k: int | None = None, filters: dict | None = None) -> list[SearchHit]:
    top_k = top_k or settings.top_k
    qvec = await get_embedder().embed_query(query)  # asymmetric query embedding (RETRIEVAL_QUERY)
    hits = await get_store().search(qvec, top_k, filters or None)
    if not hits:
        return []
    if settings.reranker_backend != "none":
        # Reranking is a precision boost ON TOP of the embedding order — never let a reranker
        # outage (API not enabled, quota, transient 5xx) break search. On failure, keep the
        # embedding-ranked hits so retrieval still works; the reranker re-engages once it recovers.
        try:
            docs = [c.text for c, _ in hits]
            ranked = await get_reranker().rerank(query, docs, min(settings.rerank_top_n, len(docs)))
            hits = [(hits[i][0], score) for i, score in ranked]
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, don't fail the query
            # name the exception TYPE so ops can tell a config/auth error (always fails) from a
            # transient API/quota error (self-heals) without spelunking the message (RF-17).
            logger.warning("reranker (%s) failed [%s], falling back to embedding order: %s",
                           settings.reranker_backend, type(exc).__name__, exc)
    return [SearchHit(text=c.text, score=round(s, 4), provenance=c.provenance()) for c, s in hits]
