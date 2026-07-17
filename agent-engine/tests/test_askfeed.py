"""ASK-5 — ask-feed composition tests (no network, no real LLM).

Covers the pieces the entry screen's trust depends on: the data signature (unchanged →
no LLM spend, cards kept), the citation-drop + QT-2 hook audit on synthesized cards, and
the kind allow-lists per scope.
"""

from __future__ import annotations

import json

import pytest

import agentengine.askfeed as AF
from agentengine.askfeed import AskFeedRequest, _signature, build_ask_feed
from agentengine.models import Citation

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _g(idx: int, tool: str, data: dict, source: str = "Yahoo Finance") -> dict:
    return {"idx": idx, "tool": tool, "args": {"ticker": "AAPL"}, "why": "테스트",
            "data": data,
            "citation": Citation(tool=tool, source=source, as_of="2026-07-05", kind="data",
                                 ticker="AAPL", used=True)}


def test_signature_stable_and_sensitive():
    a = [_g(1, "yahoo__price_snapshot", {"price": 210.5, "as_of": "2026-07-05"})]
    same = [_g(1, "yahoo__price_snapshot", {"price": 210.5, "as_of": "2026-07-05"})]
    moved = [_g(1, "yahoo__price_snapshot", {"price": 211.9, "as_of": "2026-07-06"})]
    assert _signature(a) == _signature(same)          # same records → same digest
    assert _signature(a) != _signature(moved)         # new data → new digest


def test_signature_detects_change_past_400_chars():
    """Regression: 서명은 페이로드 앞 400자만 보지 않는다 — 긴 리스트(뉴스 10건·섹션 수십 종목)
    깊숙이 새 레코드가 들어와도(as_of 동일) 잡아낸다. 예전엔 [:400] 절단으로 놓쳤다."""
    # 앞부분은 동일하고 400자 이후에서만 갈라지는 두 페이로드 (같은 as_of)
    base = [{"title": f"headline number {i} about the market and rates", "date": "2026-07-05"}
            for i in range(20)]
    changed = [dict(x) for x in base]
    changed[-1]["title"] = "a brand new late-breaking headline that landed deep in the list"
    g_base = [_g(1, "google_news__news", {"items": base, "as_of": "2026-07-05"})]
    g_changed = [_g(1, "google_news__news", {"items": changed, "as_of": "2026-07-05"})]
    assert len(json.dumps(base, ensure_ascii=False)) > 400   # 실제로 400자를 넘는 페이로드
    assert _signature(g_base) != _signature(g_changed)


class _FakeClient:
    def __init__(self, tools, gathered):
        self._tools, self._gathered = tools, gathered

    async def fetch_tools(self):
        return self._tools


async def test_unchanged_signature_skips_llm(monkeypatch):
    gathered = [_g(1, "yahoo__price_snapshot", {"price": 210.5})]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"yahoo__price_snapshot": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("yahoo__price_snapshot", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    called = {"synth": 0}

    async def fake_synth(prompt):
        called["synth"] += 1
        return []

    async def fake_curated(prompt):
        called["synth"] += 1
        return {}
    monkeypatch.setattr(AF, "_synthesize", fake_synth)
    monkeypatch.setattr(AF, "_synthesize_curated", fake_curated)

    sig = _signature(gathered)
    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL",
                                              prev_signature=sig), api_key="k")
    assert out["unchanged"] is True and out["signature"] == sig
    assert called["synth"] == 0                        # no LLM spend when nothing changed


async def test_citation_drop_and_kind_filter(monkeypatch):
    gathered = [_g(1, "sec_edgar__filings", {"filings": [{"accession": "1", "date": "2026-07-03"}]},
                   source="SEC EDGAR")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"sec_edgar__filings": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("sec_edgar__filings", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_curated(prompt):
        return {"candidates": [
            {"kind": "filing_deep", "question": "새 공시에서 위험요소 바뀐 것 보여줘",
             "hook": "새 공시 접수 (2026-07-03)", "sources": [1]},          # ships
            {"kind": "filing_deep", "question": "근거 없는 카드", "hook": "허공", "sources": []},  # dropped: no citation
            {"kind": "macro", "question": "티커 스코프에 거시 카드", "hook": "x", "sources": [1]},  # dropped: kind not allowed
            {"kind": "price_context", "question": "숫자 지어냄", "hook": "주가 999.99달러",
             "sources": [1]},                                               # dropped: QT-2 unsupported figure
        ], "picks": [0, 1, 2, 3]}
    monkeypatch.setattr(AF, "_synthesize_curated", fake_curated)

    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL"), api_key="k")
    qs = [c["question"] for c in out["cards"]]
    assert qs == ["새 공시에서 위험요소 바뀐 것 보여줘"]
    assert out["cards"][0]["citations"][0]["source"] == "SEC EDGAR"
    assert out["signature"]                                             # new signature recorded


async def test_synthesis_failure_keeps_previous_generation(monkeypatch):
    gathered = [_g(1, "yahoo__price_snapshot", {"price": 210.5})]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"yahoo__price_snapshot": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("yahoo__price_snapshot", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_curated(prompt):
        raise RuntimeError("no key")
    monkeypatch.setattr(AF, "_synthesize_curated", fake_curated)

    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL"), api_key="k")
    # no fabricated fallback for the entry screen: empty cards + no signature → caller keeps old rows
    assert out["cards"] == [] and out["signature"] is None and out["unchanged"] is False


def test_news_plan_is_news_first_and_marketwide():
    tools = {"yahoo__asset_classes": {}, "fred__macro_panel": {}, "google_news__news": {}}
    plan = AF._news_plan(tools)
    names = [p[0] for p in plan]
    news_calls = [p for p in plan if p[0] == "google_news__news"]
    assert {a["market"] for _, a, _ in news_calls} == {"US", "KR"}   # both markets' headlines
    assert all("ticker" not in a for _, a, _ in news_calls)          # market-wide, not per-ticker
    # /news 라우트 상한은 10(le=10) — 넘기면 400 → 소스 통째 드랍. 상한에 맞춰야 한다.
    assert all(a["limit"] <= 10 for _, a, _ in news_calls)
    assert "yahoo__asset_classes" in names                           # price context rides along
    # Macro Trends: 실제 거시지표 최신값(FRED 패널)도 US·KR 모두 엮는다
    assert [a["region"] for n, a, _ in plan if n == "fred__macro_panel"] == ["US", "KR"]


async def test_news_feed_scope_uses_news_kinds(monkeypatch):
    gathered = [_g(1, "google_news__news", {"items": [{"title": "Fed holds rates",
                                                       "date": "2026-07-05"}]}, source="Google News")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"google_news__news": {}}, gathered))
    monkeypatch.setattr(AF, "_news_plan", lambda tools: [("google_news__news", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_synth(prompt):
        return [
            {"kind": "macro", "question": "금리 동결 이후 흐름을 같이 볼까요?",
             "hook": "Fed holds rates (2026-07-05)", "sources": [1]},            # ships
            {"kind": "filing_deep", "question": "뉴스 스코프에 티커 카드", "hook": "x",
             "sources": [1]},                                                     # dropped: ticker kind
        ]
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    out = await build_ask_feed(AskFeedRequest(scope="news_feed"), api_key="k")
    assert [c["kind"] for c in out["cards"]] == ["macro"]


def test_ticker_plan_covers_diverse_angles():
    # ASK-9: gather goes far beyond 가격/공시/뉴스 — valuation, insiders/flows, consensus,
    # history — and adapts per market (KR gets 수급, US gets consensus/earnings).
    us_tools = {t: {} for t in [
        "yahoo__price_snapshot", "sec_edgar__filings", "google_news__news",
        "sec_edgar__metrics_snapshot", "sec_edgar__insider_trades",
        "fmp__consensus_estimates", "fmp__earnings_calendar",
        "market_history__drawdowns", "market_history__vol_context"]}
    plan = AF._ticker_plan(us_tools, AskFeedRequest(scope="ticker", market="US", ticker="AAPL"))
    names = [p[0] for p in plan]
    assert {"sec_edgar__metrics_snapshot", "sec_edgar__insider_trades",
            "fmp__consensus_estimates", "market_history__vol_context"} <= set(names)

    kr_tools = {t: {} for t in [
        "yahoo__price_snapshot", "opendart__filings", "google_news__news",
        "opendart__metrics_snapshot", "opendart__insider_trades", "kis__investor_flow",
        "market_history__drawdowns"]}
    plan_kr = AF._ticker_plan(kr_tools, AskFeedRequest(scope="ticker", market="KR", ticker="005930"))
    names_kr = [p[0] for p in plan_kr]
    assert "kis__investor_flow" in names_kr                  # KR 수급
    assert "fmp__consensus_estimates" not in names_kr        # US 전용은 제외


async def test_news_scope_allows_twenty_cards_ticker_stays_curated(monkeypatch):
    """Macro Trends 마키: 뉴스 스코프는 20장까지 내보내고, 티커 스코프는 여전히 3~6장 큐레이션."""
    gathered = [_g(1, "google_news__news", {"items": [{"title": "Fed holds rates",
                                                       "date": "2026-07-05"}]}, source="Google News")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"google_news__news": {}}, gathered))
    monkeypatch.setattr(AF, "_news_plan", lambda tools: [("google_news__news", {}, "x")])
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("google_news__news", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_synth(prompt):
        return [{"kind": "macro", "question": f"주제 {i}번을 같이 볼까요?", "hook": "Fed holds rates",
                 "sources": [1]} for i in range(25)]
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    out = await build_ask_feed(AskFeedRequest(scope="news_feed", limit=20), api_key="k")
    assert len(out["cards"]) == 20                     # 25 생성 → 상한 20에서 자름

    kinds = ["filing_deep", "price_context", "news_probe", "history_echo",
             "fundamental_shift", "valuation", "ownership", "earnings"]

    async def fake_curated(prompt):
        return {"candidates": [{"kind": k, "question": f"{k} 카드 볼까요?", "hook": "Fed holds rates",
                                "sources": [1]} for k in kinds],
                "picks": list(range(len(kinds)))}
    monkeypatch.setattr(AF, "_synthesize_curated", fake_curated)

    out_t = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL",
                                                limit=20), api_key="k")
    assert len(out_t["cards"]) == 6                    # 티커 스코프 상한은 그대로 6


def test_curate_respects_picks_and_kind_diversity():
    cands = [
        {"kind": "price_context", "question": "q0"}, {"kind": "price_context", "question": "q1"},
        {"kind": "price_context", "question": "q2"}, {"kind": "valuation", "question": "q3"},
        {"kind": "ownership", "question": "q4"},
    ]
    # picks order wins, but a third card of the SAME kind is skipped (diversity guard)
    out = AF._curate({"candidates": cands, "picks": [0, 1, 2, 3, 4]}, limit=5)
    assert [c["question"] for c in out] == ["q0", "q1", "q3", "q4"]
    # invalid/missing picks → fall back to the candidate list (still kind-guarded, limited)
    out2 = AF._curate({"candidates": cands, "picks": [99]}, limit=3)
    assert [c["question"] for c in out2] == ["q0", "q1", "q3"]


def test_section_plans_are_marketwide_not_a_ticker_sweep():
    # 홈 마키 섹션 3종은 시장 전체 앵커(대표주·거장·지수)만 모은다 — 특정 유저 티커 스윕 아님.
    # 어닝: 미국 대표주 실적 캘린더 + 일부 컨센서스
    ep = AF._earnings_plan({t: {} for t in ["fmp__earnings_calendar", "fmp__consensus_estimates"]})
    cal = [a["ticker"] for n, a, _ in ep if n == "fmp__earnings_calendar"]
    assert "NVDA" in cal and "AAPL" in cal and len(cal) >= 6
    assert all(a.get("market") == "US" for _, a, _ in ep)
    assert AF._earnings_plan({}) == []                       # 커넥터 결측 → 그 소스만 빠짐

    # 거장·수급: 공통 보유 + 유명 거장 매매(미국) + 한국 movers(KIS)
    gp = AF._guru_plan({t: {} for t in ["sec_edgar__guru_common", "sec_edgar__guru_trades",
                                        "kis__volume_rank", "kis__fluctuation_rank"]})
    assert "sec_edgar__guru_common" in [n for n, _, _ in gp]
    assert "buffett" in [a["slug"] for n, a, _ in gp if n == "sec_edgar__guru_trades"]
    assert {a.get("direction") for n, a, _ in gp if n == "kis__fluctuation_rank"} == {"up", "down"}
    # KIS 키 없으면 한국 수급만 빠지고 미국 거장은 남는다(정직한 축소)
    gp_us = AF._guru_plan({"sec_edgar__guru_common": {}, "sec_edgar__guru_trades": {}})
    assert [n for n, _, _ in gp_us] and all(not n.startswith("kis__") for n, _, _ in gp_us)

    # 히스토리: 시장 전체 지수(^GSPC·^KS11·^VIX)의 결정론적 과거 기록
    hp = AF._history_plan({t: {} for t in ["market_history__drawdowns", "market_history__vol_context",
                                           "market_history__base_rates", "market_history__episodes",
                                           "market_history__regimes"]})
    hticks = {a.get("ticker") for _, a, _ in hp if a.get("ticker")}
    assert {"^GSPC", "^KS11", "^VIX"} <= hticks
    ev = next(a["event"] for n, a, _ in hp if n == "market_history__base_rates")
    assert "daily_return_lte" in ev                          # base_rates event = JSON 문자열


def test_section_plan_dispatch():
    assert AF._section_plan("earnings_radar", {"fmp__earnings_calendar": {}}) \
        == AF._earnings_plan({"fmp__earnings_calendar": {}})
    assert AF._section_plan("guru_flows", {"sec_edgar__guru_common": {}}) \
        == AF._guru_plan({"sec_edgar__guru_common": {}})
    assert AF._section_plan("history_lab", {"market_history__regimes": {}}) \
        == AF._history_plan({"market_history__regimes": {}})
    # 모르는 스코프는 news_plan으로 폴백
    assert AF._section_plan("news_feed", {"google_news__news": {}}) \
        == AF._news_plan({"google_news__news": {}})


async def test_section_scope_uses_its_kinds(monkeypatch):
    """어닝 스코프는 자기 kind만 내보내고 다른 섹션/티커 kind는 드랍한다(스코프별 allow-list)."""
    gathered = [_g(1, "fmp__earnings_calendar",
                   {"events": [{"date": "2026-08-28", "eps_actual": None}]}, source="API Ninjas / FMP")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"fmp__earnings_calendar": {}}, gathered))
    monkeypatch.setattr(AF, "_earnings_plan", lambda tools: [("fmp__earnings_calendar", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_synth(prompt):
        return [
            {"kind": "earnings_upcoming", "question": "엔비디아 실적 전에 서프라이즈 흐름 볼까요?",
             "hook": "다음 발표 2026-08-28", "sources": [1]},              # ships
            {"kind": "macro", "question": "어닝 스코프에 거시 카드", "hook": "x", "sources": [1]},  # dropped
            {"kind": "guru_move", "question": "어닝 스코프에 거장 카드", "hook": "x", "sources": [1]},  # dropped
        ]
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    out = await build_ask_feed(AskFeedRequest(scope="earnings_radar", limit=14), api_key="k")
    assert [c["kind"] for c in out["cards"]] == ["earnings_upcoming"]


async def test_query_field_subject_injection(monkeypatch):
    # F3: 카드의 실행용 query — 종목 주체가 없으면 서버가 "이름(티커) " 프리픽스를 주입하고,
    # 이미 있으면 그대로, query 누락이면 question으로 폴백(역시 주입).
    gathered = [_g(1, "sec_edgar__filings", {"filings": [{"accession": "1", "date": "2026-07-03"}]},
                   source="SEC EDGAR")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"sec_edgar__filings": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("sec_edgar__filings", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_curated(prompt):
        return {"candidates": [
            {"kind": "filing_deep", "question": "정정 공시에서 바뀐 내용 함께 살펴볼까요?",
             "query": "정정 유상증자 공시에서 바뀐 내용을 원문과 함께 살펴봐",
             "hook": "새 공시 접수 (2026-07-03)", "sources": [1]},
            {"kind": "price_context", "question": "삼성전자 오늘 급등 이유 같이 알아볼까요?",
             "query": "삼성전자 오늘 급등 배경을 공시·뉴스로 살펴봐",
             "hook": "새 공시 접수 (2026-07-03)", "sources": [1]},
            {"kind": "news_probe", "question": "이 뉴스 사실인지 볼까요?",
             "hook": "새 공시 접수 (2026-07-03)", "sources": [1]},   # query 누락 → question 폴백
        ], "picks": [0, 1, 2]}
    monkeypatch.setattr(AF, "_synthesize_curated", fake_curated)

    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="KR", ticker="005930",
                                              name="삼성전자"), api_key="k")
    qs = {c["kind"]: c["query"] for c in out["cards"]}
    assert qs["filing_deep"] == "삼성전자(005930) 정정 유상증자 공시에서 바뀐 내용을 원문과 함께 살펴봐"
    assert qs["price_context"] == "삼성전자 오늘 급등 배경을 공시·뉴스로 살펴봐"   # 이미 포함 → 주입 없음
    assert qs["news_probe"] == "삼성전자(005930) 이 뉴스 사실인지 볼까요?"          # 폴백 + 주입
