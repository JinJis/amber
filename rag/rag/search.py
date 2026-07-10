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

# --- RQ-3: multi-query expansion ------------------------------------------------------------
_MQ_PROMPT = ("검색 쿼리 변형 생성. 원쿼리와 같은 의미의 검색용 변형 2개를 한 줄씩만 출력:\n"
              "1) 반대 언어 번역(한국어면 영어로, 영어면 한국어로) 2) 핵심 키워드 나열형.\n"
              "설명·번호 없이 변형 텍스트만 두 줄. 원쿼리: {q}")
_mq_cache: dict[str, list[str]] = {}


async def expand_queries(query: str) -> list[str]:
    """쿼리 변형 ≤2개 (실패/미설정 시 빈 리스트 — 원쿼리 단독으로 무해 강등). LRU 캐시로
    반복 쿼리(피드 갱신 등)에 LLM 재호출 없음."""
    q = (query or "").strip()
    if not settings.multi_query or len(q) < 8:
        return []
    if q in _mq_cache:
        return _mq_cache[q]
    try:
        import asyncio

        from google import genai
        from google.genai import types
        client = genai.Client()
        resp = await asyncio.wait_for(asyncio.to_thread(
            client.models.generate_content, model=settings.multi_query_model,
            contents=_MQ_PROMPT.format(q=q[:300]),
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=120)),
            timeout=6.0)
        lines = [ln.strip(" -•1234567890.)") for ln in (getattr(resp, "text", "") or "").splitlines()]
        out = [ln for ln in lines if 3 <= len(ln) <= 200 and ln.lower() != q.lower()][:2]
    except Exception as exc:  # noqa: BLE001 — 확장은 보너스, 검색을 절대 막지 않음
        logger.info("multi-query expansion unavailable [%s]", type(exc).__name__)
        out = []
    if len(_mq_cache) > 256:
        _mq_cache.clear()
    _mq_cache[q] = out
    return out


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

    # RQ-3: 원쿼리 + 변형(한↔영·키워드형)마다 dense+렉시컬 레그를 만들어 전부 RRF 융합.
    queries = [query] + await expand_queries(query)
    rankings: list[list] = []
    for q in queries:
        qvec = await get_embedder().embed_query(q)   # asymmetric query embedding (RETRIEVAL_QUERY)
        rankings.append(await store.search(qvec, candidate_k, filters or None))
        try:
            lex = await store.lexical(q, candidate_k, filters or None)
            if lex:
                rankings.append(lex)
        except Exception as exc:  # noqa: BLE001 — the lexical leg is an upgrade, never an outage
            logger.warning("lexical leg failed [%s], dense-only: %s", type(exc).__name__, exc)
    hits = _rrf_fuse(*rankings) if len(rankings) > 1 else (rankings[0] if rankings else [])
    if not hits:
        return []

    # RQ-6: 신선도 부스트 — 호출자가 명시적으로 doc_type=news를 필터한 검색만(키워드 추론
    # 없음, 인바리언트 준수). RRF 점수에 as_of 지수감쇠 가점을 블렌드: 오늘=+0.5·30일 반감.
    if (filters or {}).get("doc_type") == "news":
        import math
        from datetime import datetime

        def _recency(chunk) -> float:
            try:
                d = datetime.fromisoformat(str(chunk.provenance().get("as_of") or "")[:10])
                age = max(0.0, float((datetime.utcnow() - d).days))
                return 0.5 * math.exp(-age / 30.0)
            except Exception:  # noqa: BLE001 — as_of 없으면 가점 0 (불이익 아님)
                return 0.0
        hits = sorted(((c, sc + _recency(c)) for c, sc in hits), key=lambda t: -t[1])

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
