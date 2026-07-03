"""Company Information endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep, MarketParam
from app.models.generated import (
    CiksResponse,
    CompanyFactsResponse,
    CompanySearchResponse,
    TickersResponse,
)
from app.providers.registry import get_company_provider
from app.routers._common import tickers_response
from app.symbols import Market, build_ref

router = APIRouter(tags=["Company Information"])


@router.get("/company/facts", response_model=CompanyFactsResponse, dependencies=[ApiKeyDep])
async def get_company_facts(
    market: MarketParam = Market.US,
    ticker: str | None = Query(None, description="The ticker symbol."),
    cik: str | None = Query(None, description="US SEC CIK / KR OpenDART corp_code."),
) -> CompanyFactsResponse:
    ref = build_ref(market, ticker, cik)
    facts = await get_company_provider(market).company_facts(ref)
    return CompanyFactsResponse(company_facts=facts)


@router.get("/company/facts/tickers", response_model=TickersResponse)
async def get_company_facts_tickers(market: MarketParam = Market.US) -> TickersResponse:
    return await tickers_response(market, "company_facts")


@router.get("/company/facts/ciks", response_model=CiksResponse)
async def get_company_facts_ciks(market: MarketParam = Market.US) -> CiksResponse:
    ciks = await get_company_provider(market).list_ciks()
    return CiksResponse(resource="company_facts", ciks=ciks)


_SEARCH_SOURCE = {Market.US: "SEC EDGAR", Market.KR: "OpenDART (FSS)"}


@router.get("/company/search", response_model=CompanySearchResponse, dependencies=[ApiKeyDep])
async def search_companies(
    market: MarketParam = Market.US,
    q: str = Query(..., min_length=1, description="Name or ticker query (e.g. '삼성', 'AAPL')."),
    limit: int = Query(10, ge=1, le=50, description="Max results."),
) -> CompanySearchResponse:
    results = await get_company_provider(market).search_companies(q, limit)
    return CompanySearchResponse(
        resource="company_search",
        source=_SEARCH_SOURCE.get(market),
        query=q,
        results=results,
    )
