"""M-DESK / DK-1 — desk-feed composition tests (no network, no real LLM).

The autouse fixture clears the Gemini key, so the synthesis pass degrades to the
deterministic fallback — which is exactly the path we can assert precisely; the
citation-drop rule is tested by stubbing the synthesis output directly.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import agentengine.deskfeed as DF
from agentengine.config import settings
from agentengine.main import app

client = TestClient(app)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


CATALOG = {"connectors": [
    {"id": "yahoo", "name": "Yahoo Finance", "resources": [
        {"name": "price_snapshot", "method": "GET", "path": "/prices/snapshot",
         "params": [{"name": "ticker", "required": True}, {"name": "market"}],
         "cadence": "intraday", "category": "금융시장 현황", "provenance": {"source": "Yahoo Finance"}},
        {"name": "asset_classes", "method": "GET", "path": "/market/asset-classes",
         "params": [], "cadence": "intraday", "category": "금융시장 현황",
         "provenance": {"source": "Yahoo Finance"}},
    ]},
    {"id": "sec_edgar", "name": "SEC EDGAR", "resources": [
        {"name": "filings", "method": "GET", "path": "/filings",
         "params": [{"name": "ticker", "required": True}, {"name": "market"}],
         "cadence": "event", "category": "공시", "provenance": {"source": "SEC EDGAR"}},
    ]},
    {"id": "google_news", "name": "Google News", "resources": [
        {"name": "news", "method": "GET", "path": "/news",
         "params": [{"name": "ticker", "required": True}, {"name": "market"}],
         "cadence": "intraday", "category": "뉴스룸", "provenance": {"source": "Google News"}},
    ]},
]}


def _gw(monkeypatch):
    monkeypatch.setattr(settings, "gateway_url", "http://gw.test")


def _mock_gateway():
    respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(200, json=CATALOG))
    respx.route(method="GET", url__regex=r"http://gw\.test/prices/snapshot.*").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "close": 185.6, "change_pct": -3.2,
                                               "as_of": "2026-07-03"},
                                    headers={"x-connector": "yahoo"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/market/asset-classes.*").mock(
        return_value=httpx.Response(200, json={"classes": [{"name": "S&P500", "change_pct": 0.4}],
                                               "as_of": "2026-07-03"},
                                    headers={"x-connector": "yahoo"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/filings.*").mock(
        return_value=httpx.Response(200, json={"filings": [{"form": "8-K", "filing_date": "2026-07-02",
                                                            "filing_url": "https://sec.gov/8k"}]},
                                    headers={"x-connector": "sec_edgar"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/news.*").mock(
        return_value=httpx.Response(200, json={"articles": [{"title": "AAPL supply news",
                                                             "source_url": "https://news.example/1"}]},
                                    headers={"x-connector": "google_news"}))


WATCHLISTS = [{"name": "빅테크", "items": [
    {"market": "US", "ticker": "AAPL", "name": "Apple"},
    {"market": "US", "ticker": "MSFT", "name": "Microsoft"},
]}]


@respx.mock
async def test_desk_feed_with_watchlist_returns_sourced_cards(monkeypatch):
    _gw(monkeypatch)
    _mock_gateway()
    r = client.post("/agent/desk-feed", json={"watchlists": WATCHLISTS}, headers={"X-API-KEY": "vgk_x"})
    assert r.status_code == 200
    body = r.json()
    cards = body["cards"]
    assert len(cards) >= 4
    # a user WITH a watchlist gets no nudge
    assert all(c["kind"] != "watchlist_nudge" for c in cards)
    # every data card carries at least one citation with a real source
    for c in cards:
        if c["kind"] not in ("watchlist_nudge", "continue_thread"):
            assert c["citations"] and c["citations"][0]["source"]
            assert c["question"] and c["hook"]
    assert "yahoo__price_snapshot" in body["used_tools"]


@respx.mock
async def test_desk_feed_without_watchlist_leads_with_nudge(monkeypatch):
    _gw(monkeypatch)
    _mock_gateway()
    r = client.post("/agent/desk-feed", json={"watchlists": [], "markets": ["US"]},
                    headers={"X-API-KEY": "vgk_x"})
    assert r.status_code == 200
    cards = r.json()["cards"]
    assert cards and cards[0]["kind"] == "watchlist_nudge"
    # market-wide cards still fill the screen (asset classes / news), all sourced
    data = [c for c in cards if c["kind"] not in ("watchlist_nudge", "continue_thread")]
    assert data and all(c["citations"] for c in data)


@respx.mock
async def test_desk_feed_drops_uncited_and_foreign_kind_cards(monkeypatch):
    _gw(monkeypatch)
    _mock_gateway()

    async def fake_synth(req, gathered):
        return [
            {"kind": "price_move", "question": "AAPL 가격?", "hook": "실제 근거", "sources": [1]},
            {"kind": "price_move", "question": "유령 카드", "hook": "근거 없음", "sources": [99]},
            {"kind": "price_move", "question": "빈 근거", "hook": "근거 없음", "sources": []},
            {"kind": "watchlist_nudge", "question": "LLM이 만든 넛지", "hook": "금지", "sources": [1]},
            {"kind": "buy_signal", "question": "이상한 종류", "hook": "금지", "sources": [1]},
        ]

    monkeypatch.setattr(DF, "_synthesize", fake_synth)
    r = client.post("/agent/desk-feed", json={"watchlists": WATCHLISTS}, headers={"X-API-KEY": "vgk_x"})
    cards = r.json()["cards"]
    questions = [c["question"] for c in cards]
    assert "AAPL 가격?" in questions          # the properly-cited card ships
    assert "유령 카드" not in questions        # invalid source index → dropped
    assert "빈 근거" not in questions          # no sources → dropped
    assert "LLM이 만든 넛지" not in questions  # state kinds are server-minted only
    assert "이상한 종류" not in questions      # unknown kind → dropped


@respx.mock
async def test_desk_feed_continue_thread_from_recent_conversation(monkeypatch):
    _gw(monkeypatch)
    _mock_gateway()
    r = client.post("/agent/desk-feed",
                    json={"watchlists": WATCHLISTS, "recent_conversations": ["TSLA 마진 분석"]},
                    headers={"X-API-KEY": "vgk_x"})
    cards = r.json()["cards"]
    ct = next((c for c in cards if c["kind"] == "continue_thread"), None)
    assert ct is not None and "TSLA 마진 분석" in ct["question"]


@respx.mock
async def test_desk_feed_gateway_down_degrades_to_state_cards(monkeypatch):
    _gw(monkeypatch)
    respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(503))
    r = client.post("/agent/desk-feed", json={"watchlists": []}, headers={"X-API-KEY": "vgk_x"})
    assert r.status_code == 200                       # never a 500 — the feed degrades
    cards = r.json()["cards"]
    assert cards and cards[0]["kind"] == "watchlist_nudge"
    assert r.json()["used_tools"] == []


async def test_gather_filings_citation_uses_shared_producer():
    # DK gather citations now come from the SHARED _citations producer: a filings listing yields
    # kind="filing" with the row's url + descriptive snippet + as_of — the hand-rolled minimal
    # Citation rendered an empty '추출 데이터' panel (kind="data", no url) in the SourceViewer.
    tools = {"sec_edgar__filings": {
        "name": "sec_edgar__filings", "connector": "sec_edgar", "source": "SEC EDGAR",
        "cadence": "event", "category": "공시", "method": "GET", "path": "/filings", "params": []}}

    class _Client:
        async def call_tool(self, tool, args):
            return {"status": 200, "data": {"filings": [{
                "form": "8-K", "filing_date": "2026-07-02",
                "description": "항목 5.02 임원·이사 변동",
                "filing_url": "https://sec.gov/8k"}]}}

    out = await DF._gather(_Client(), tools,
                           [("sec_edgar__filings", {"ticker": "AAPL", "market": "US"}, "AAPL 최근 공시")])
    assert len(out) == 1 and out[0]["idx"] == 1
    cite = out[0]["citation"]
    assert cite.kind == "filing"                              # not the old generic "data" card
    assert cite.url == "https://sec.gov/8k" and "임원" in cite.snippet
    assert cite.as_of == "2026-07-02" and cite.freshness is not None
    assert cite.used is True                                  # gathered evidence is always used
    assert cite.ticker == "AAPL"                              # backfilled from the call args
    assert cite.cadence == "event" and cite.category == "공시"


async def test_gather_drops_failed_calls():
    # a non-200 source is skipped entirely — no citation, no snippet in the synthesis prompt.
    tools = {"sec_edgar__filings": {"name": "sec_edgar__filings", "connector": "sec_edgar",
                                    "source": "SEC EDGAR", "method": "GET", "path": "/filings",
                                    "params": []}}

    class _Client:
        async def call_tool(self, tool, args):
            return {"status": 502, "data": None}

    assert await DF._gather(_Client(), tools,
                            [("sec_edgar__filings", {"ticker": "AAPL"}, "x")]) == []


# --- M1 / HL-7: History Lab artifact builders ------------------------------
# (in this file to keep test_agent.py's already-large module focused; these need no fixtures
# beyond the builders themselves)

def _hist_env(data: dict, params: dict | None = None) -> dict:
    return {"source": "derived: ingested prices (close) via market_history", "method": "x",
            "params": params or {"ticker": "^GSPC"}, "as_of": "2026-07-02", "freshness": "fresh",
            "cadence": "daily", "label": "과거 기록 · 전망 아님",
            "history_span": {"from": "1927-12-30", "to": "2026-07-02"}, "data": data}


def test_artifact_base_rates_carries_label_and_shape():
    from agentengine.artifacts import _build_artifacts

    env = _hist_env({
        "n": 60, "raw_n": 87,
        "event_dates": ["1929-10-28", "1987-10-19", "2020-03-16"],
        "horizons": [{"h": 20, "n": 60, "median": 2.81, "p25": -3.2, "p75": 8.9,
                      "min": -22.0, "max": 24.0, "pos_share": 60.0}],
        "histogram": {"h_ref": 20, "bins": [{"lo": -22.0, "hi": -17.4, "count": 2}]},
    }, params={"ticker": "^GSPC", "event": {"daily_return_lte": -5.0}})
    arts = _build_artifacts({"name": "market_history__base_rates", "source": "derived"}, {"data": env})
    assert len(arts) == 1
    a = arts[0]
    assert a.kind == "base_rates" and a.label == "과거 기록 · 전망 아님"   # mandatory (invariant §2)
    assert "일간 수익률 ≤ -5.0%" in a.title
    assert a.base_rates["n"] == 60 and a.base_rates["raw_n"] == 87
    assert a.base_rates["horizons"][0]["pos_share"] == 60.0
    assert a.base_rates["event_dates"][0] == "1929-10-28"                 # enumerable events
    assert a.ticker == "^GSPC" and a.tool == "market_history__base_rates"


def test_artifact_analogue_paths_and_no_average():
    from agentengine.artifacts import _build_artifacts

    env = _hist_env({
        "window": 120, "anchor": "now",
        "current": {"label": "현재", "path": [100.0, 98.0, 95.0]},
        "matches": [{"ticker": "^GSPC", "start_date": "2008-09-01", "end_date": "2009-02-20",
                     "score": 0.91, "path": [100.0, 97.0, 94.0], "aftermath": [94.0, 96.0]}],
    })
    arts = _build_artifacts({"name": "market_history__analogues", "source": "derived"}, {"data": env})
    a = arts[0]
    assert a.kind == "analogue" and a.label == "과거 기록 · 전망 아님"
    assert a.analogue["current"]["path"][0] == 100.0
    assert a.analogue["matches"][0]["score"] == 0.91
    assert a.analogue["matches"][0]["aftermath"] == [94.0, 96.0]          # per-match history, never averaged


def test_artifact_regime_compare_reuses_analogue_kind():
    from agentengine.artifacts import _build_artifacts

    env = _hist_env({
        "regime": {"slug": "gfc-2008", "name_kr": "글로벌 금융위기", "start_date": "2007-10-09",
                   "end_date": "2009-03-09"},
        "then": {"path": [100.0, 70.0, 43.2], "dates": [], "depth_pct": -56.78},
        "now": {"path": [100.0, 99.0, 98.3], "dates": [], "depth_pct": -1.66, "days_since_peak": 3},
    }, params={"ticker": "^GSPC", "slug": "gfc-2008"})
    arts = _build_artifacts({"name": "market_history__regime_compare", "source": "derived"}, {"data": env})
    a = arts[0]
    assert a.kind == "analogue" and a.label == "과거 기록 · 전망 아님"
    assert a.analogue["matches"][0]["ticker"] == "글로벌 금융위기"
    assert a.table and a.table[1][0] == "최대 낙폭" and "-56.78%" in a.table[1][1]


def test_artifact_episodes_and_vol_context_tables():
    from agentengine.artifacts import _build_artifacts

    eps = _hist_env({"threshold_pct": 20.0, "n": 1, "episodes": [
        {"peak_date": "2007-10-09", "trough_date": "2009-03-09", "depth_pct": -56.78,
         "decline_days": 517, "recovery_date": "2013-03-28", "recovery_days": 1480, "is_open": False}]})
    a = _build_artifacts({"name": "market_history__episodes", "source": "d"}, {"data": eps})[0]
    assert a.kind == "table" and a.label and a.table[1][2] == "-56.78%"

    vol = _hist_env({"windows": {"20": {"realized_vol_pct": 14.2, "percentile": 62.5}},
                     "level": {"current": 16.4, "percentile": 41.0}})
    v = _build_artifacts({"name": "market_history__vol_context", "source": "d"}, {"data": vol})[0]
    assert v.kind == "table" and v.label and v.table[1][1] == "14.2%"
    assert v.table[-1][0] == "레벨(현재)"


def test_artifact_history_handlers_guard_malformed():
    from agentengine.artifacts import _build_artifacts

    bad = _hist_env({"nothing": True})
    for tool in ("market_history__base_rates", "market_history__analogues",
                 "market_history__regime_compare", "market_history__drawdowns",
                 "market_history__episodes", "market_history__vol_context", "market_history__regimes"):
        assert _build_artifacts({"name": tool, "source": "d"}, {"data": bad}) == []
