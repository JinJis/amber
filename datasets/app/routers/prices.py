"""Market Data (prices) endpoints."""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep, MarketParam
from app.models.generated import (
    PriceSnapshotMarketResponse,
    PriceSnapshotResponse,
    PricesResponse,
    TickersResponse,
)
from app.providers.registry import get_prices_provider
from app.routers._common import gather_best_effort, tickers_response, validate_interval
from app.store.screener import store_tickers
from app.symbols import Market, build_ref

router = APIRouter(tags=["Market Data"])


@router.get("/prices", response_model=PricesResponse, dependencies=[ApiKeyDep])
async def get_prices(
    ticker: str = Query(..., description="The stock ticker symbol."),
    interval: str = Query(..., description="day | week | month | year"),
    start_date: date = Query(..., description="Start date (YYYY-MM-DD)."),
    end_date: date = Query(..., description="End date (YYYY-MM-DD)."),
    market: MarketParam = Market.US,
) -> PricesResponse:
    validate_interval(interval)
    ref = build_ref(market, ticker)
    prices = await get_prices_provider(market).prices(ref, interval, start_date, end_date)
    return PricesResponse(ticker=ref.ticker, prices=prices)


@router.get("/prices/snapshot", response_model=PriceSnapshotResponse, dependencies=[ApiKeyDep])
async def get_price_snapshot(
    ticker: str = Query(..., description="The stock ticker symbol."),
    market: MarketParam = Market.US,
) -> PriceSnapshotResponse:
    ref = build_ref(market, ticker)
    snap = await get_prices_provider(market).snapshot(ref)
    return PriceSnapshotResponse(snapshot=snap)


@router.get("/prices/snapshot/market", response_model=PriceSnapshotMarketResponse, dependencies=[ApiKeyDep])
async def get_price_snapshot_market(
    market: MarketParam = Market.US,
    limit: int = Query(25, ge=1, le=100, description="Max tickers to snapshot."),
) -> PriceSnapshotMarketResponse:
    """Latest snapshot for the tickers we track in this market (the ingested universe,
    bounded by `limit`) — never fans out to the whole exchange. Per-ticker failures are
    skipped, not faked."""
    tickers = await asyncio.to_thread(store_tickers, market.value, limit)
    provider = get_prices_provider(market)
    snaps = await gather_best_effort(tickers, lambda tk: provider.snapshot(build_ref(market, tk)))
    return PriceSnapshotMarketResponse(snapshots=snaps)


@router.get("/prices/snapshot/tickers", response_model=TickersResponse)
async def get_price_snapshot_tickers(market: MarketParam = Market.US) -> TickersResponse:
    return await tickers_response(market, "prices")


@router.get("/prices/tickers", response_model=TickersResponse)
async def get_prices_tickers(market: MarketParam = Market.US) -> TickersResponse:
    return await tickers_response(market, "prices")
