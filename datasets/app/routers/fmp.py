"""CE-11: FMP — analyst consensus estimates + earnings calendar (third-party sourced data).

Forward financial ESTIMATES (revenue/EPS) and the earnings calendar, shown as licensed
third-party data (analyst consensus via FMP) — never our forecast/target. Price targets and
buy/sell ratings are deliberately NOT exposed (guardrail brand).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep
from app.config import settings
from app.providers.us import api_ninjas, fmp

router = APIRouter(tags=["Estimates / Calendar (FMP)"])


@router.get("/estimates", dependencies=[ApiKeyDep],
            summary="애널리스트 컨센서스 추정치 (매출·EPS) — 제3자 데이터(FMP), 우리 예측 아님")
async def get_estimates(
    ticker: str = Query(..., description="US ticker (e.g. AAPL)."),
    period: str = Query("annual", description="annual | quarter"),
    limit: int = Query(5, ge=1, le=30),
) -> dict:
    return await fmp.consensus_estimates(ticker, period, limit)


@router.get("/earnings-calendar", dependencies=[ApiKeyDep],
            summary="실적 캘린더 — 컨센서스 vs 실제 EPS/매출 + 서프라이즈% (API Ninjas 우선, FMP 폴백)")
async def get_earnings_calendar(
    ticker: str = Query(..., description="US ticker (e.g. AAPL)."),
    limit: int = Query(8, ge=1, le=40),
) -> dict:
    # V-8: API Ninjas가 ~50분기 + 서프라이즈% 프리컴퓨트로 더 깊다. 키 없거나 빈 응답이면 FMP.
    if settings.api_ninjas_key:
        try:
            got = await api_ninjas.earnings_calendar(ticker, limit)
            if got.get("events"):
                return got
        except Exception:  # noqa: BLE001 — 폴백이 있으니 조용히 FMP로
            pass
    return await fmp.earnings_calendar(ticker, limit)
