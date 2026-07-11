"""ONB-LIVE — 온보딩 쇼케이스: 카드→근거 3종 선별·후속질문 결합·실패 시 빈 번들."""

from __future__ import annotations

import pytest

from agentengine import askfeed as A


@pytest.mark.asyncio
async def test_showcase_bundles_cards_evidence_and_followups(monkeypatch):
    cards = [
        {"kind": "valuation", "question": "q1", "hook": "PER 12.3배로 5년 평균 아래예요",
         "citations": [{"source": "DART", "table": [["항목", "값"]], "freshness": "fresh"},
                       {"source": "Google News", "kind": "news", "snippet": "감산 뉴스", "freshness": "fresh"}]},
        {"kind": "filing_deep", "question": "q2", "hook": "자사주 3조 취득 결정 공시가 나왔어요",
         "citations": [{"source": "OpenDART (FSS)", "kind": "filing", "doc_type": "주요사항보고서"}]},
    ]

    async def fake_feed(req, api_key):
        assert req.ticker == "005930" and req.market == "KR"
        return {"cards": cards, "signature": "sig1"}

    async def fake_followups(task, answer, model, backend, **kw):
        assert "PER 12.3배" in answer   # 훅(실데이터)이 답변 컨텍스트로 들어감
        return ["감산이 4분기 마진에 미친 영향은?", "자사주 취득 뒤 주주환원 여력은?", "HBM 경쟁 구도는?"]

    monkeypatch.setattr(A, "build_ask_feed", fake_feed)
    import agentengine.enrichment as E
    monkeypatch.setattr(E, "suggest_followups", fake_followups)

    out = await A.build_onboarding_showcase(None)
    assert out["name"] == "삼성전자" and len(out["cards"]) == 2
    kinds = [("table" if c.get("table") else c.get("kind")) for c in out["evidence"]]
    assert "table" in kinds and "news" in kinds and len(out["evidence"]) == 3  # 표·뉴스·공시 3종
    assert len(out["followups"]) == 3 and out["question"]


@pytest.mark.asyncio
async def test_showcase_empty_when_feed_empty(monkeypatch):
    async def fake_feed(req, api_key):
        return {"cards": []}
    monkeypatch.setattr(A, "build_ask_feed", fake_feed)
    out = await A.build_onboarding_showcase(None)
    assert out["cards"] == [] and "generated_at" in out   # 정직: 빈 번들 → studio가 이전 캐시 유지
