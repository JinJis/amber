"""Earnings endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep, MarketParam
from app.models.generated import EarningsResponse, TickersResponse
from app.providers.registry import get_earnings_provider
from app.routers._common import tickers_response
from app.symbols import Market, build_ref

router = APIRouter(tags=["Earnings"])


@router.get("/earnings", response_model=EarningsResponse, dependencies=[ApiKeyDep])
async def get_earnings(
    ticker: str = Query(..., description="The ticker symbol."),
    limit: int = Query(1, ge=1, le=40, description="Number of recent report periods (max 40)."),
    market: MarketParam = Market.US,
) -> EarningsResponse:
    ref = build_ref(market, ticker)
    records = await get_earnings_provider(market).earnings(ref, limit)
    return EarningsResponse(earnings=records)


@router.get("/earnings/tickers", response_model=TickersResponse)
async def get_earnings_tickers(market: MarketParam = Market.US) -> TickersResponse:
    """Tickers the earnings endpoint can serve (the public-company universe)."""
    return await tickers_response(market, "earnings")
