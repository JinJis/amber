"""Technical indicators (PH-DATA-6) — descriptive overlays computed from prices.

Market-scoped via the prices provider (Yahoo US+KR). Descriptive only: these are not
trading signals or recommendations — the agent guardrail still refuses advice.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep, MarketParam
from app.routers._common import validate_interval
from app.store.technical import technical_indicators
from app.symbols import Market

router = APIRouter(tags=["Market Data"])


@router.get("/technical-indicators", dependencies=[ApiKeyDep],
            summary="Descriptive technical indicators (SMA/EMA/RSI/MACD/Bollinger/volatility) computed from prices")
async def get_technical_indicators(
    ticker: str = Query(..., description="The stock ticker symbol."),
    indicators: str | None = Query(None, description="Comma list, e.g. sma_50,ema_20,rsi_14,macd,bbands_20,volatility_20. Omit for a default set."),
    interval: str = Query("day", description="day | week | month | year"),
    start_date: date | None = Query(None, description="Start date (default: ~1y back)."),
    end_date: date | None = Query(None, description="End date (default: today)."),
    market: MarketParam = Market.US,
) -> dict:
    validate_interval(interval)
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=400))
    return await technical_indicators(market.value, ticker, indicators, interval, start, end)
