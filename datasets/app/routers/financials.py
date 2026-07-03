"""Financial Statements endpoints (income statement, balance sheet, cash flow)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.deps import ApiKeyDep, MarketParam
from app.filters import ReportPeriodFilters
from app.models.generated import (
    AsReportedResponse,
    BalanceSheetResponse,
    CashFlowStatementResponse,
    Financials,
    FinancialsResponse,
    IncomeStatementResponse,
)
from app.providers.registry import get_financials_provider
from app.routers._common import validate_period
from app.symbols import Market, build_ref

router = APIRouter(tags=["Financial Statements"])


@router.get(
    "/financials/income-statements",
    response_model=IncomeStatementResponse,
    dependencies=[ApiKeyDep],
)
async def get_income_statements(
    period: str = Query(..., description="annual | quarterly | ttm"),
    ticker: str | None = Query(None),
    cik: str | None = Query(None),
    limit: int = Query(4, ge=1),
    filters: ReportPeriodFilters = Depends(),
    market: MarketParam = Market.US,
) -> IncomeStatementResponse:
    validate_period(period)
    ref = build_ref(market, ticker, cik)
    rows = await get_financials_provider(market).income_statements(ref, period, filters.fetch_limit(limit))
    return IncomeStatementResponse(income_statements=filters.apply(rows, limit))


@router.get(
    "/financials/balance-sheets",
    response_model=BalanceSheetResponse,
    dependencies=[ApiKeyDep],
)
async def get_balance_sheets(
    period: str = Query(..., description="annual | quarterly | ttm"),
    ticker: str | None = Query(None),
    cik: str | None = Query(None),
    limit: int = Query(4, ge=1),
    filters: ReportPeriodFilters = Depends(),
    market: MarketParam = Market.US,
) -> BalanceSheetResponse:
    validate_period(period)
    ref = build_ref(market, ticker, cik)
    rows = await get_financials_provider(market).balance_sheets(ref, period, filters.fetch_limit(limit))
    return BalanceSheetResponse(balance_sheets=filters.apply(rows, limit))


@router.get(
    "/financials/cash-flow-statements",
    response_model=CashFlowStatementResponse,
    dependencies=[ApiKeyDep],
)
async def get_cash_flow_statements(
    period: str = Query(..., description="annual | quarterly | ttm"),
    ticker: str | None = Query(None),
    cik: str | None = Query(None),
    limit: int = Query(4, ge=1),
    filters: ReportPeriodFilters = Depends(),
    market: MarketParam = Market.US,
) -> CashFlowStatementResponse:
    validate_period(period)
    ref = build_ref(market, ticker, cik)
    rows = await get_financials_provider(market).cash_flow_statements(ref, period, filters.fetch_limit(limit))
    return CashFlowStatementResponse(cash_flow_statements=filters.apply(rows, limit))


@router.get(
    "/financials/as-reported",
    response_model=AsReportedResponse,
    dependencies=[ApiKeyDep],
    summary="Financials exactly as reported in XBRL (raw us-gaap concepts)",
    description=(
        "Returns every XBRL concept exactly as filed (not normalised to our schema), per period — the "
        "auditable 'as-reported' view. US (SEC XBRL) only for now; KR (DART XBRL) is PH-7b. Gaps stay "
        "absent, never fabricated."
    ),
)
async def get_financials_as_reported(
    ticker: str = Query(..., description="The ticker symbol."),
    period: str = Query("annual", description="annual | quarterly | ttm"),
    limit: int = Query(4, ge=1, le=20, description="Number of recent periods."),
    market: MarketParam = Market.US,
) -> AsReportedResponse:
    validate_period(period)
    ref = build_ref(market, ticker)
    periods = await get_financials_provider(market).as_reported(ref, period, limit)
    return AsReportedResponse(ticker=ref.ticker, period=period, periods=periods)


@router.get("/financials", response_model=FinancialsResponse, dependencies=[ApiKeyDep])
async def get_all_financial_statements(
    period: str = Query(..., description="annual | quarterly | ttm"),
    ticker: str | None = Query(None),
    cik: str | None = Query(None),
    limit: int = Query(4, ge=1),
    filters: ReportPeriodFilters = Depends(),
    market: MarketParam = Market.US,
) -> FinancialsResponse:
    validate_period(period)
    ref = build_ref(market, ticker, cik)
    provider = get_financials_provider(market)
    fetch = filters.fetch_limit(limit)
    return FinancialsResponse(
        financials=Financials(
            income_statements=filters.apply(await provider.income_statements(ref, period, fetch), limit),
            balance_sheets=filters.apply(await provider.balance_sheets(ref, period, fetch), limit),
            cash_flow_statements=filters.apply(await provider.cash_flow_statements(ref, period, fetch), limit),
        )
    )
