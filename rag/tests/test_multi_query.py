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


# --- COST: 멀티쿼리 확장이 실제 usage_metadata를 원가 스토어로 보고한다(estimated=False) ---
class _FakeUM:
    prompt_token_count = 40
    candidates_token_count = 12
    thoughts_token_count = 3


class _FakeResp:
    text = "supply chain risk drivers\n공급망 리스크 핵심 키워드"
    usage_metadata = _FakeUM()


class _FakeModels:
    def generate_content(self, **kw):
        return _FakeResp()


class _FakeClient:
    models = _FakeModels()


@pytest.mark.asyncio
async def test_multi_query_reports_real_usage(monkeypatch):
    monkeypatch.setattr(settings, "multi_query", True)
    S._mq_cache.clear()
    from google import genai
    monkeypatch.setattr(genai, "Client", lambda *a, **k: _FakeClient())
    captured: list[dict] = []
    monkeypatch.setattr("rag.telemetry.report_usage", lambda p: captured.append(p))

    out = await S.expand_queries("삼성전자 공급망 리스크 정리해줘")
    assert len(out) >= 1                       # 변형이 파싱됐고
    assert len(captured) == 1                  # 텔레메트리 POST가 정확히 1회
    p = captured[0]
    assert p["service"] == "rag" and p["kind"] == "multi_query"
    assert p["input_tokens"] == 40 and p["output_tokens"] == 15   # candidates + thoughts
    assert p["estimated"] is False and p["calls"] == 1


@pytest.mark.asyncio
async def test_multi_query_no_usage_metadata_skips_report(monkeypatch):
    monkeypatch.setattr(settings, "multi_query", True)
    S._mq_cache.clear()

    class _NoUM(_FakeResp):
        usage_metadata = None

    class _Models:
        def generate_content(self, **kw):
            return _NoUM()

    class _Client:
        models = _Models()

    from google import genai
    monkeypatch.setattr(genai, "Client", lambda *a, **k: _Client())
    captured: list[dict] = []
    monkeypatch.setattr("rag.telemetry.report_usage", lambda p: captured.append(p))
    await S.expand_queries("엔비디아 데이터센터 매출 성장 요인 알려줘")
    assert captured == []   # usage_metadata 없으면 아무것도 보고하지 않음 (날조 금지)
