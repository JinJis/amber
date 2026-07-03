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
