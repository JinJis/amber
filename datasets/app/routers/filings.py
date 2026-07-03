"""SEC / DART Filings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.config import settings
from app.deps import ApiKeyDep, MarketParam
from app.models.generated import CiksResponse, FilingsResponse, TickersResponse
from app.providers.registry import get_company_provider, get_filings_provider
from app.routers._common import tickers_response
from app.symbols import Market, build_ref

router = APIRouter(tags=["SEC Filings"])

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
    from app.store.filing_ingest import ingest_filing_text_for_ticker
    from app.store.news_ingest import _search_rag

    mkt = market.value
    hits = await _search_rag(settings.rag_url, query, ticker, mkt, top_k)
    ingested = False
    if not hits:  # never seen this company's filings → ingest its recent reports, then retry
        await ingest_filing_text_for_ticker(mkt, ticker, limit=settings.filing_search_ingest_limit)
        ingested = True
        hits = await _search_rag(settings.rag_url, query, ticker, mkt, top_k)
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
