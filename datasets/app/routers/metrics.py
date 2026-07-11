"""Financial Metrics endpoints.

The real-time snapshot is backed by live providers (KR: pykrx fundamentals,
US: XBRL + EOD price). Historical metrics (`/financial-metrics`) derive ratios
across periods from the point-in-time ingestion store (PH-6).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep, MarketParam
from app.errors import bad_request, not_found
from app.models.generated import (
    FinancialMetricsHistoryResponse,
    FinancialMetricSnapshotResponse,
)
from app.derivation import calc_row, computation
from app.providers.registry import get_metrics_provider
from app.routers._common import gather_best_effort
from app.store.metrics_history import metrics_history_models
from app.symbols import Market, build_ref, normalize_ticker

router = APIRouter(tags=["Financial Metrics"])


@router.get(
    "/financial-metrics/snapshot",
    response_model=FinancialMetricSnapshotResponse,
    dependencies=[ApiKeyDep],
)
async def get_financial_metrics_snapshot(
    ticker: str | None = Query(None),
    cik: str | None = Query(None),
    market: MarketParam = Market.US,
) -> FinancialMetricSnapshotResponse:
    ref = build_ref(market, ticker, cik)
    snap = await get_metrics_provider(market).metrics_snapshot(ref)
    return FinancialMetricSnapshotResponse(snapshot=snap)


@router.get(
    "/financial-metrics",
    response_model=FinancialMetricsHistoryResponse,
    dependencies=[ApiKeyDep],
    summary="Historical derived financial metrics (ratios across periods)",
    description=(
        "Derives margins / returns / leverage / liquidity / YoY-growth ratios across periods from the "
        "point-in-time ingestion store. Store-backed: returns what's been ingested for the ticker (empty "
        "if it hasn't been backfilled). Gaps stay null — never fabricated."
    ),
)
async def get_financial_metrics(
    ticker: str = Query(..., description="The ticker symbol."),
    period: str = Query("annual", description="annual | quarterly | ttm"),
    limit: int = Query(8, ge=1, le=40, description="Number of recent periods."),
    market: MarketParam = Market.US,
) -> FinancialMetricsHistoryResponse:
    ref = build_ref(market, ticker)
    metrics = await asyncio.to_thread(metrics_history_models, market.value, ref.ticker, period, limit)
    return FinancialMetricsHistoryResponse(ticker=ref.ticker, period=period, metrics=metrics)


@router.get(
    "/comparables",
    dependencies=[ApiKeyDep],
    summary="Peer valuation comparables — multiples side by side (PH-DATA-2)",
    description=(
        "Given a set of tickers (the caller / agent picks the peer set), returns each company's "
        "valuation multiples + margins/returns side by side — descriptive, derived from filings + "
        "price (never a forecast). Per-ticker failures are skipped, not faked."
    ),
)
async def get_comparables(
    tickers: str = Query(..., description="Comma-separated peers, e.g. AAPL,MSFT,GOOGL"),
    market: MarketParam = Market.US,
) -> dict:
    syms = [t.strip() for t in tickers.split(",") if t.strip()][:12]
    if not syms:
        raise bad_request("Provide at least one ticker in `tickers`.")
    prov = get_metrics_provider(market)
    snaps = await gather_best_effort(
        syms, lambda sym: prov.metrics_snapshot(build_ref(market, normalize_ticker(market, sym)))
    )
    if not snaps:
        raise not_found("No comparables data for the given tickers.")
    # M-DERIV (DRV-1): the comparables table is our arithmetic (per-ticker snapshot metrics
    # laid side by side) — say how, and which peers survived (failures skipped, not faked).
    deriv = computation(
        "동종그룹 멀티플 비교 (종목별 스냅샷 파생)",
        "종목별로 시가총액 = P × S · PER = P ÷ EPS · PBR = 시가총액 ÷ E → 나란히 비교",
        inputs=[calc_row("종목", ", ".join(s.ticker or "?" for s in snaps), source="요청 피어셋 중 데이터 확보분"),
                calc_row("재무 라인아이템", "최신 보고값", source="SEC EDGAR" if market is Market.US else "OpenDART/KRX"),
                calc_row("주가", "지연 시세", source="가격 체인")],
        note="피어셋은 호출자가 지정 — 실패 종목은 표에서 제외(가짜값 없음)")
    return {"market": market.value, "tickers": [s.ticker for s in snaps], "comparables": snaps,
            "computation": deriv}
