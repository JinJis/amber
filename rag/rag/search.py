"""Query → embed → HYBRID retrieve (dense ∪ lexical, RRF-fused) → rerank funnel → hits.

RQ-1: dense-only retrieval misses exact identifiers (tickers, accession numbers, Korean company
names, figures); a lexical-only search misses paraphrase. We run both legs over a WIDE candidate
pool (``candidate_k``), fuse with Reciprocal Rank Fusion, then let the reranker pick the final
``top_k`` — fixing the old degenerate funnel (retrieve 8 → rerank 5) where the reranker could
never recover anything dense retrieval hadn't already surfaced.
"""

from __future__ import annotations

import logging

from rag.config import settings
from rag.embeddings import get_embedder
from rag.models import Chunk, SearchHit
from rag.rerank import get_reranker
from rag.store import get_store

logger = logging.getLogger(__name__)

_RRF_K = 60  # standard RRF constant — rank 0 scores 1/60, decays gently


def _rrf_fuse(*rankings: list[tuple[Chunk, float]]) -> list[tuple[Chunk, float]]:
    """Reciprocal Rank Fusion: score(chunk) = Σ_legs 1/(K + rank). Rank-based, so the two legs\'
    incomparable score scales (cosine vs ts_rank) never need calibration."""
    scores: dict[str, float] = {}
    chunk_by_id: dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, (chunk, _s) in enumerate(ranking):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
            chunk_by_id.setdefault(chunk.id, chunk)
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    return [(chunk_by_id[cid], s) for cid, s in ordered]


async def search(query: str, top_k: int | None = None, filters: dict | None = None) -> list[SearchHit]:
    top_k = top_k or settings.top_k
    candidate_k = max(settings.candidate_k, top_k)
    store = get_store()

    qvec = await get_embedder().embed_query(query)  # asymmetric query embedding (RETRIEVAL_QUERY)
    dense = await store.search(qvec, candidate_k, filters or None)
    try:
        lex = await store.lexical(query, candidate_k, filters or None)
    except Exception as exc:  # noqa: BLE001 — the lexical leg is an upgrade, never an outage
        logger.warning("lexical leg failed [%s], dense-only: %s", type(exc).__name__, exc)
        lex = []
    hits = _rrf_fuse(dense, lex) if lex else dense
    if not hits:
        return []

    if settings.reranker_backend != "none":
        # Reranking is a precision boost ON TOP of the fused order — never let a reranker
        # outage (API not enabled, quota, transient 5xx) break search. On failure, keep the
        # fused hits so retrieval still works; the reranker re-engages once it recovers.
        try:
            docs = [c.text for c, _ in hits]
            ranked = await get_reranker().rerank(query, docs, min(top_k, len(docs)))
            hits = [(hits[i][0], score) for i, score in ranked]
        except Exception as exc:  # noqa: BLE001 — degrade gracefully, don\'t fail the query
            # name the exception TYPE so ops can tell a config/auth error (always fails) from a
            # transient API/quota error (self-heals) without spelunking the message (RF-17).
            logger.warning("reranker (%s) failed [%s], falling back to fused order: %s",
                           settings.reranker_backend, type(exc).__name__, exc)
    return [SearchHit(text=c.text, score=round(s, 4), provenance=c.provenance())
            for c, s in hits[:top_k]]
