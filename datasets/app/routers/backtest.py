"""CE-7: portfolio backtester endpoint.

Descriptive historical performance of a buy-and-hold allocation over ingested daily closes —
equity curve + total return / CAGR / volatility / max drawdown, optionally vs a benchmark. Past
facts only; no forecast, no advice (the guardrail still refuses those at the agent boundary).
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import ApiKeyDep, MarketParam
from app.store.backtest import run_backtest
from app.symbols import Market


class Holding(BaseModel):
    ticker: str
    weight: float = 1.0


class BacktestRequest(BaseModel):
    # the LLM planner passes `holdings` as a JSON STRING (function-calling schemas are flat) —
    # accept both shapes so the tool is actually callable end-to-end.
    holdings: list[Holding] | str
    start_date: date | None = None
    end_date: date | None = None
    initial: float = 10000.0
    benchmark: str | None = None     # e.g. SPY — compared over the same window


router = APIRouter(tags=["Backtest"])


@router.post(
    "/backtest",
    dependencies=[ApiKeyDep],
    summary="포트폴리오 백테스트 — 매수후보유 과거 성과 (서술적, 조언 아님)",
    description=(
        "Computes the historical buy-and-hold performance of a weighted portfolio over ingested "
        "daily closes: equity curve + total return, CAGR, volatility, max drawdown, optionally vs a "
        "benchmark. Strictly descriptive (what WOULD have happened) — no forecast or recommendation. "
        "Needs the holdings' prices in the store; missing coverage → an honest note (never fabricated)."
    ),
)
async def backtest(body: BacktestRequest, market: MarketParam = Market.US) -> dict:
    import json as _json
    raw = body.holdings
    if isinstance(raw, str):
        try:
            raw = [Holding(**h) for h in _json.loads(raw)]
        except Exception:  # noqa: BLE001 — honest 422, not a silent empty backtest
            from fastapi import HTTPException
            raise HTTPException(422, 'holdings must be JSON like [{"ticker":"AAPL","weight":0.6}]')
    holdings = [{"ticker": h.ticker, "weight": h.weight} for h in raw]
    res = await asyncio.to_thread(
        run_backtest, market.value, holdings, start=body.start_date, end=body.end_date,
        initial=body.initial, benchmark=body.benchmark)
    return {"resource": "backtest", **res,
            "disclaimer": "과거 데이터 기반 서술적 성과입니다 — 미래 수익을 보장하지 않으며 투자 조언이 아닙니다."}
