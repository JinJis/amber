"""RQ-3 — 멀티쿼리 확장: fail-safe(빈 리스트 강등)·짧은 쿼리 스킵·캐시."""

import pytest

from rag import search as S
from rag.config import settings


@pytest.mark.asyncio
async def test_short_or_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(settings, "multi_query", True)
    assert await S.expand_queries("PER") == []          # <8자 스킵
    monkeypatch.setattr(settings, "multi_query", False)
    assert await S.expand_queries("삼성전자 공급망 리스크 정리") == []


@pytest.mark.asyncio
async def test_llm_failure_degrades_to_original_only(monkeypatch):
    monkeypatch.setattr(settings, "multi_query", True)
    S._mq_cache.clear()
    # GOOGLE_API_KEY 부재/오류 → 예외 → 빈 변형 (검색은 원쿼리로 계속)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    out = await S.expand_queries("엔비디아 데이터센터 매출 성장 요인")
    assert out == []
    # 결과(빈 것 포함) 캐시 → 같은 쿼리 재호출 시 LLM 접근 없음
    assert "엔비디아 데이터센터 매출 성장 요인" in S._mq_cache
