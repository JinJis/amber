"""SEC / DART Filings endpoints."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Query

from app.config import settings
from app.deps import ApiKeyDep, MarketParam
from app.models.generated import CiksResponse, FilingsResponse, TickersResponse
from app.providers.registry import get_company_provider, get_filings_provider
from app.routers._common import tickers_response
from app.symbols import Market, build_ref

router = APIRouter(tags=["SEC Filings"])

# HI-7: on-demand filing-text ingest is expensive (fetch filings HTML + chunk + embed into RAG).
# Single-flight it per (market, ticker) so N concurrent cold searches share ONE ingest instead of
# each racing the same download + replace_scope; a short negative cache skips re-ingesting a ticker
# that genuinely has no matching filing text (e.g. an ETF) on every subsequent cold search.
_ingest_flight: dict[str, asyncio.Task] = {}
_ingest_negcache: dict[str, float] = {}   # (market:ticker) → expires (skip re-ingest until then)


async def _ingest_once(mkt: str, ticker: str) -> None:
    """Await THE ingest task for this (market, ticker) — creating it only if none is in flight, so
    concurrent cold callers don't each launch a duplicate download + embed."""
    from app.store.filing_ingest import ingest_filing_text_for_ticker

    key = f"{mkt}:{ticker.upper()}"
    task = _ingest_flight.get(key)
    if task is None or task.done():   # no await between here and the store → race-free under asyncio
        task = asyncio.ensure_future(
            ingest_filing_text_for_ticker(mkt, ticker, limit=settings.filing_search_ingest_limit))
        _ingest_flight[key] = task
    try:
        await task
    finally:
        if _ingest_flight.get(key) is task and task.done():
            _ingest_flight.pop(key, None)

_FILING_TYPES = {
    Market.US: ["10-K", "10-Q", "8-K", "20-F", "6-K"],
    Market.KR: ["사업보고서", "반기보고서", "분기보고서", "주요사항보고서"],
}


@router.get("/filings", response_model=FilingsResponse, dependencies=[ApiKeyDep])
async def get_filings(
    ticker: str | None = Query(None),
    cik: str | None = Query(None, description="US SEC CIK / KR OpenDART corp_code."),
    filing_type: list[str] | None = Query(None, description="Repeatable filter, e.g. filing_type=10-K."),
    limit: int = Query(10, ge=1),
    market: MarketParam = Market.US,
) -> FilingsResponse:
    ref = build_ref(market, ticker, cik)
    filings = await get_filings_provider(market).filings(ref, filing_type, limit)
    return FilingsResponse(filings=filings)


_IR_TYPES = {Market.US: ["8-K"], Market.KR: ["주요사항보고서"]}  # the IR/news vehicle per market


@router.get("/filings/ir", response_model=FilingsResponse, dependencies=[ApiKeyDep],
            summary="IR 자료실 — IR/실적 관련 공시 (US: 8-K · KR: 주요사항보고서)")
async def ir_materials(
    ticker: str = Query(..., description="Company ticker / KR 6-digit code."),
    limit: int = Query(10, ge=1, le=50),
    market: MarketParam = Market.US,
) -> FilingsResponse:
    ref = build_ref(market, ticker)
    filings = await get_filings_provider(market).filings(ref, _IR_TYPES.get(market), limit)
    return FilingsResponse(filings=filings)


@router.get("/filings/search", dependencies=[ApiKeyDep])
async def filing_search(
    ticker: str = Query(..., description="Company ticker (US) or KR 6-digit code."),
    query: str = Query(..., description="What to find in the filings, e.g. '공급망 리스크', 'AI 수요'."),
    top_k: int = Query(6, ge=1, le=20),
    market: MarketParam = Market.US,
) -> dict:
    """Semantic search over a company's FILING TEXT (위험요소·사업의 내용·MD&A·notes), returning
    passages each cited to the real filing (source, accession, section). On-demand: if the corpus
    has nothing for this ticker yet, its recent filings are fetched + indexed live, then searched —
    so any company works without a pre-run pipeline. Returns the RAG `{hits}` shape so each passage
    is cited + evidence-highlighted like any RAG result."""
    from app.store.news_ingest import _search_rag

    mkt = market.value
    hits = await _search_rag(settings.rag_url, query, ticker, mkt, top_k)
    ingested = False
    key = f"{mkt}:{ticker.upper()}"
    # never seen this company's filings → ingest recent reports (single-flight), then retry — unless
    # we recently ingested it and still found nothing (negative cache), in which case skip the churn.
    if not hits and _ingest_negcache.get(key, 0.0) <= time.monotonic():
        await _ingest_once(mkt, ticker)
        ingested = True
        hits = await _search_rag(settings.rag_url, query, ticker, mkt, top_k)
        if hits:
            _ingest_negcache.pop(key, None)
        else:                    # genuinely nothing → don't re-ingest on every cold search
            _ingest_negcache[key] = time.monotonic() + settings.filing_neg_cache_ttl_seconds
            if len(_ingest_negcache) > 4000:   # opportunistic compaction
                now = time.monotonic()
                for k in [k for k, exp in _ingest_negcache.items() if exp <= now]:
                    _ingest_negcache.pop(k, None)
    return {"resource": "filing_search", "ticker": ticker.upper(), "query": query,
            "market": mkt, "ingested": ingested, "hits": hits}


@router.get("/filings/types")
async def get_filing_types(market: MarketParam = Market.US) -> dict:
    return {"resource": "filings", "filing_types": _FILING_TYPES[market]}


@router.get("/filings/tickers", response_model=TickersResponse)
async def get_filings_tickers(market: MarketParam = Market.US) -> TickersResponse:
    """Tickers in the filing universe (US: SEC company_tickers; KR: DART corp list)."""
    return await tickers_response(market, "filings")


@router.get("/filings/ciks", response_model=CiksResponse)
async def get_filings_ciks(market: MarketParam = Market.US) -> CiksResponse:
    """Filer ids in the filing universe (US: SEC CIK; KR: OpenDART corp_code)."""
    ciks = await get_company_provider(market).list_ciks()
    return CiksResponse(resource="filings", ciks=ciks)
