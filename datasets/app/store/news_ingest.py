"""News → RAG ingestion pipeline (PH-2b).

Pulls Google News headlines for a set of tickers and indexes them into the RAG
service so ``rag__search`` returns real, recent context instead of nothing.

News is *public* and identical for every tenant, so it's indexed as a **global
(unscoped) corpus** — visible to all tenants via PH-2a's "own-tenant OR global"
search rule — rather than copied per tenant. Each run is recorded as an
``IngestionJob`` (kind ``news``) so the admin ops console shows what was pulled,
when, and any error — same observability as the financial-facts backfill.

A headline carries only title + publisher + url + date (no body), which is exactly
the Live Context Feed's "context only, no forecast" shape.
"""

from __future__ import annotations

import asyncio

import httpx

from app.config import settings
from app.models.generated import News
from app.providers.registry import get_news_provider
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.symbols import Market


def _news_to_doc(market: str, article: News) -> dict | None:
    """Map one headline → a RAG IngestDoc (global; no tenant). None if it has no title."""
    title = (article.title or "").strip()
    if not title:
        return None
    url = str(article.url) if article.url else None
    return {
        "text": title,
        # stable per article (url, else ticker+title) → re-ingest UPSERTs instead of duplicating
        "doc_id": url or f"{article.ticker or ''}:{title}",
        "source": article.source or "Google News",  # publisher (Reuters/연합뉴스/…) when present
        "doc_type": "news",
        "ticker": article.ticker,
        "market": market,
        "as_of": str(article.date) if article.date else None,
        "url": url,
    }


# Non-replace feeds (news, era_news) have no scope to swap atomically, so they still go in
# bounded batches so one huge run never sits in a single request.
_RAG_INGEST_BATCH = 40


def _ingest_timeout(docs: int) -> "httpx.Timeout":
    """Read budget scales with doc count — the client's best proxy for the server's embed work.
    Connect/write/pool stay small; only the read (embedding wall-time) grows (ING-1)."""
    read = min(settings.rag_ingest_timeout_base_seconds + settings.rag_ingest_timeout_per_doc_seconds * docs,
               settings.rag_ingest_timeout_max_seconds)
    return httpx.Timeout(connect=10.0, read=read, write=60.0, pool=10.0)


# Retry only CONNECTION-level failures (the request never reached a working server) + 5xx.
# NOT ReadTimeout: a read timeout means the server DID receive the request and is still embedding/
# inserting — retrying then just piles a second ingest behind the first (it blocks on the rag
# advisory lock, then re-does the insert). The atomic swap makes that safe but wasteful; the next
# delta run heals a genuinely-dropped ingest instead (live finding, ING-1 Phase 7).
_RETRY_TRANSPORT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError)


async def _post_ingest(client: "httpx.AsyncClient", url: str, body: dict) -> int:
    """POST one /rag/ingest request with ONE retry on a connection-level failure or 5xx. Safe:
    the server-side swap is atomic + idempotent (a retry re-embeds nothing, re-confirms content).
    A ReadTimeout (server busy) or 4xx (client error) fails immediately — no retry."""
    for attempt in range(2):
        try:
            resp = await client.post(url, json=body)
        except _RETRY_TRANSPORT:
            if attempt == 1:
                raise
            await asyncio.sleep(15.0)
            continue
        if resp.status_code >= 500 and attempt == 0:
            await asyncio.sleep(15.0)
            continue
        resp.raise_for_status()
        return int((resp.json() or {}).get("chunks", 0))
    return 0  # unreachable — loop always returns or raises


async def _ingest_to_rag(rag_url: str, docs: list[dict], replace: dict | None = None) -> int:
    """POST the docs to the RAG service (global corpus) and return the chunk count.

    ING-1: a ``replace``-scoped ingest (a filing / transcript / deck — one accession's docs)
    goes in ONE request so the server can compute the full new chunk-id set and do the atomic
    prune-swap; the client budget scales with doc count and one transient retry is safe. Feeds
    without a scope (news / era_news) still batch, since there's nothing to swap atomically."""
    if not docs:
        return 0
    url = f"{rag_url.rstrip('/')}/rag/ingest"
    if replace:
        async with httpx.AsyncClient(timeout=_ingest_timeout(len(docs))) as client:
            return await _post_ingest(client, url, {"documents": docs, "replace": replace})
    total = 0
    async with httpx.AsyncClient(timeout=_ingest_timeout(_RAG_INGEST_BATCH)) as client:
        for i in range(0, len(docs), _RAG_INGEST_BATCH):
            total += await _post_ingest(client, url, {"documents": docs[i:i + _RAG_INGEST_BATCH]})
    return total


async def _search_rag(rag_url: str, query: str, ticker: str | None, market: str | None,
                      top_k: int) -> list[dict]:
    """Query the RAG service and return its passage hits (each carries its own provenance)."""
    body = {"query": query, "top_k": top_k}
    if ticker:
        body["ticker"] = ticker
    if market:
        body["market"] = market
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.post(f"{rag_url.rstrip('/')}/rag/search", json=body)
        resp.raise_for_status()
        return (resp.json() or {}).get("hits") or []


async def run_news_ingest(
    market: str, tickers: list[str] | None, limit: int | None = None, rag_url: str | None = None,
) -> dict:
    """Pull news for ``tickers`` and index it into RAG, recorded as an IngestionJob.

    An empty/None ticker list pulls broad market news. Concurrency is serialized by the
    Procrastinate queue's per-pipeline lock (``pipe:news:<market>``), not a self-guard.
    """
    market = (market or "US").upper()
    try:
        mkt = Market(market)
    except ValueError:
        return {"status": "error", "error": f"Unknown market '{market}'."}
    # None entry => broad market news (the provider treats ticker=None that way).
    syms: list[str | None] = [t for t in (tickers or []) if t] or [None]

    limit = limit or settings.news_ingest_limit
    rag_url = rag_url or settings.rag_url
    spec = ",".join(t for t in syms if t) or "(market)"
    job_id = start_job("news", market, f"news:{spec}"[:256], total=len(syms))
    log_activity("news", market, f"▶ 뉴스 수집 시작 · {len(syms)}종목 · Google News → RAG", job_id)
    provider = get_news_provider(mkt)
    docs: list[dict] = []
    try:
        for i, sym in enumerate(syms):
            got = 0
            for article in await provider.news(mkt, sym, limit):
                doc = _news_to_doc(market, article)
                if doc:
                    docs.append(doc)
                    got += 1
            await asyncio.to_thread(log_activity, "news", market,
                                    f"[{sym}] 뉴스 {got}건 ({i + 1}/{len(syms)})", job_id)
            update_progress(job_id, i + 1)
        chunks = await _ingest_to_rag(rag_url, docs)
        finish_job(job_id, "success", rows=chunks)
        await asyncio.to_thread(log_activity, "news", market, f"✓ 완료 · {len(docs)}건 → RAG {chunks} chunks", job_id)
        return {"job_id": job_id, "status": "success", "rows": chunks, "docs": len(docs)}
    except Exception as exc:  # noqa: BLE001 — record the failure, don't crash the worker
        finish_job(job_id, "error", error=str(exc))
        await asyncio.to_thread(log_activity, "news", market, f"✗ 실패 — {exc}", job_id, "error")
        return {"job_id": job_id, "status": "error", "error": str(exc)}
