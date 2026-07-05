"""Era-news endpoints (HL-5) — GDELT DOC 2.0 coverage/tone over a window (keyless, 2017+).

Descriptive coverage of the RECORD (volume + tone), never a forecast. Pre-2017 windows return
an empty series + a coverage note (the gap is drawn, not fabricated); NYT Archive covers the
older regimes when NYT_API_KEY is set (era_news_nyt, key-gated).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep
from app.providers.us.gdelt import GdeltProvider
from app.providers.us.nyt_archive import NytArchiveProvider

router = APIRouter(prefix="/era-news", tags=["Era News (History Lab)"])


@router.get("/timeline", dependencies=[ApiKeyDep],
            summary="시대 뉴스 커버리지 — 일별 기사량·평균 톤 (GDELT, 2017+)")
async def news_timeline(
    query: str = Query(..., description="검색어 (예: 'stock market crash', 'Silicon Valley Bank')."),
    from_date: date = Query(..., alias="from", description="시작일 (YYYY-MM-DD)."),
    to_date: date = Query(..., alias="to", description="종료일 (YYYY-MM-DD)."),
) -> dict:
    return await GdeltProvider().news_timeline(query, from_date, to_date)


@router.get("/search", dependencies=[ApiKeyDep],
            summary="시대 뉴스 기사 목록 — 제목·매체·날짜·톤 (GDELT, 2017+)")
async def news_search(
    query: str = Query(..., description="검색어."),
    from_date: date = Query(..., alias="from", description="시작일 (YYYY-MM-DD)."),
    to_date: date = Query(..., alias="to", description="종료일 (YYYY-MM-DD)."),
    limit: int = Query(20, ge=1, le=75, description="최대 기사 수 (기본 20, 최대 75)."),
) -> dict:
    return await GdeltProvider().news_search(query, from_date, to_date, limit)


@router.get("/archive", dependencies=[ApiKeyDep],
            summary="시대 뉴스 — NYT Archive 헤드라인/초록 (1851~, NYT_API_KEY 필요)")
async def era_archive(
    query: str = Query("", description="필터 검색어(공백 구분; 비우면 그 달 전체)."),
    from_date: date = Query(..., alias="from", description="시작일 (YYYY-MM-DD)."),
    to_date: date = Query(..., alias="to", description="종료일 (YYYY-MM-DD)."),
    limit: int = Query(40, ge=1, le=100, description="최대 기사 수 (기본 40)."),
) -> dict:
    """Pre-2017 regimes (dot-com, GFC). 501 with an honest gap when NYT_API_KEY is unset."""
    return await NytArchiveProvider().era_news(query, from_date, to_date, limit)
