"""Reranker, selected by RAG_RERANKER_BACKEND: ``none`` (default) or ``gcp`` (Vertex Ranking API).

``rerank(query, docs, top_n) -> [(original_index, score)]`` sorted best-first. The GCP reranker is
the only real backend (the oss/tei backends were removed with their deps); it reorders the top-k
embedding hits with Google's semantic ranker — a precision boost on top of gemini embeddings.
"""

from __future__ import annotations

import asyncio
from functools import cache
from typing import Protocol

from rag.config import settings


class Reranker(Protocol):
    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]: ...


class NoneReranker:
    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
        return [(i, 0.0) for i in range(min(len(docs), top_n))]


class VertexReranker:
    """gcp — Vertex AI Ranking API (Discovery Engine RankService). Auth is Application Default
    Credentials: set GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON (mounted into the
    container). Needs the Discovery Engine API enabled on the project."""

    def __init__(self, project: str, location: str, ranking_config: str, model: str) -> None:
        from google.cloud import discoveryengine_v1 as de

        self._de = de
        self._client = de.RankServiceClient()
        self._config = self._client.ranking_config_path(project, location, ranking_config)
        self._model = model or "semantic-ranker-default@latest"

    async def rerank(self, query: str, docs: list[str], top_n: int) -> list[tuple[int, float]]:
        def _run() -> list[tuple[int, float]]:
            records = [self._de.RankingRecord(id=str(i), content=d) for i, d in enumerate(docs)]
            req = self._de.RankRequest(
                ranking_config=self._config, model=self._model, query=query,
                records=records, top_n=top_n)
            resp = self._client.rank(request=req)
            return [(int(r.id), float(r.score)) for r in resp.records][:top_n]

        out = await asyncio.to_thread(_run)
        _report_rerank_usage(self._model)   # METER-3: 콜당 과금되는 Vertex Ranking 계측
        return out


def _report_rerank_usage(model: str) -> None:
    """METER-3: Vertex Ranking은 요청당 과금인데 지금까지 원가 대시보드에 보이지 않았다 —
    calls=1(토큰 없음, estimated)로 llm_usage에 남긴다. 임베딩 텔레메트리와 동일하게
    fire-and-forget; 실패해도 검색을 절대 방해하지 않는다. HI-5: 공유 텔레메트리 클라 경유."""
    from rag.telemetry import report_usage

    report_usage({"service": "rag", "kind": "rerank", "model": model or "vertex-ranking",
                  "input_tokens": 0, "output_tokens": 0, "calls": 1, "estimated": True})


@cache
def get_reranker() -> Reranker:
    backend = settings.reranker_backend
    if backend == "gcp":
        return VertexReranker(settings.gcp_project, settings.gcp_location,
                              settings.gcp_ranking_config, settings.reranker_model)
    return NoneReranker()  # default / unknown → no reranking
