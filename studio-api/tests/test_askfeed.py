"""ASK-6 — ask-feed: news_feed background refresh (signature skip · honesty on empty),
the one-DB-read assembly, and the on-demand per-ticker pool (cache TTL · no LLM when fresh)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.askfeed import _assemble, _scope_key, refresh_once
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import AskFeedCache, User, Watchlist, WatchlistItem

client = TestClient(app)
SVC = "dev-service-token"

CARDS = {"cards": [{"kind": "macro", "question": "금리 동결 이후 흐름 같이 볼까요?",
                    "hook": "Fed holds rates", "citations": [{"source": "Google News"}]}],
         "signature": "sig-1", "unchanged": False, "generated_at": "2026-07-05T00:00:00+00:00"}

TICKER_CARDS = {"cards": [{"kind": "filing_deep", "question": "새 공시 위험요소 들여다볼까요?",
                           "hook": "새 공시 접수", "citations": [{"source": "SEC EDGAR"}]}],
                "signature": "sig-t1", "unchanged": False,
                "generated_at": "2026-07-05T00:00:00+00:00"}


def setup_module(_module):
    init_db()


def _mk_user(db, email: str, key: str = "vgk_x") -> User:
    u = db.get(User, email)
    if u is None:
        u = User(email=email, tenant_id="t1", project_id="p1", api_key=key)
        db.add(u)
        db.commit()
    # the user already exists → ensure_user() short-circuits, but still reconciles default
    # connector activations over the network once — mark done so tests stay offline.
    import studioapi.provision as prov
    prov._reconciled.add(email)
    return u


def _mk_watch(db, email: str, ticker: str, market: str = "US", name: str | None = None) -> None:
    wl = Watchlist(user_email=email, name=f"g-{email}-{ticker}")
    db.add(wl)
    db.commit()
    db.add(WatchlistItem(watchlist_id=wl.id, market=market, ticker=ticker, name=name))
    db.commit()


def _hdr(email: str) -> dict:
    return {"X-Service-Token": SVC, "X-User-Email": email}


@respx.mock
def test_refresh_once_is_news_feed_only(monkeypatch):
    """The background refresher touches ONE scope (news_feed) — never a per-ticker sweep."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "r@u.com")
        _mk_watch(db, "r@u.com", "NVDA", name="NVIDIA")   # watched, but NOT swept

    route = respx.post("http://ae.test/agent/ask-feed").mock(
        return_value=httpx.Response(200, json=CARDS))
    out = asyncio.run(refresh_once())
    assert out == {"scopes": 1, "refreshed": 1}
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content)["scope"] == "news_feed"
    with SessionLocal() as db:
        row = db.get(AskFeedCache, "news_feed")
        assert row is not None and row.signature == "sig-1"
        assert db.get(AskFeedCache, "ticker:US:NVDA") is None   # no background ticker rows

    # second tick: engine says unchanged → cards kept, row NOT overwritten
    route.mock(return_value=httpx.Response(200, json={"cards": [], "signature": "sig-1",
                                                      "unchanged": True}))
    out2 = asyncio.run(refresh_once())
    assert out2["refreshed"] == 0
    with SessionLocal() as db:
        assert db.get(AskFeedCache, "news_feed").signature == "sig-1"

    # engine fails (empty cards, no signature) → previous generation preserved (honesty rule)
    route.mock(return_value=httpx.Response(200, json={"cards": [], "signature": None,
                                                      "unchanged": False}))
    asyncio.run(refresh_once())
    with SessionLocal() as db:
        assert json.loads(db.get(AskFeedCache, "news_feed").payload)["cards"]


def test_assemble_lists_tickers_without_cards_plus_news():
    with SessionLocal() as db:
        _mk_user(db, "asm@u.com")
        _mk_watch(db, "asm@u.com", "TSLA", name="Tesla")
        db.merge(AskFeedCache(scope="news_feed",
                              payload=json.dumps({"cards": CARDS["cards"],
                                                  "generated_at": "2026-07-05T00:05:00+00:00"})))
        db.commit()
        out = _assemble(db, "asm@u.com")
    t = next(x for x in out["tickers"] if x["ticker"] == "TSLA")
    assert t["name"] == "Tesla" and t["groups"] and "cards" not in t
    assert out["news_feed"][0]["kind"] == "macro"
    assert out["news_generated_at"] == "2026-07-05T00:05:00+00:00"


def test_ask_feed_endpoint_zero_llm(monkeypatch):
    """GET /ask-feed is a pure cache read — no engine call happens at request time."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("engine must not be called at request time")
    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    with SessionLocal() as db:
        _mk_user(db, "zero@u.com")
        # 신선한 캐시를 심어 read-through 킥도 일어나지 않는 평상시 상태로 (순서 독립)
        db.merge(AskFeedCache(scope="news_feed", generated_at=datetime.utcnow(),
                              payload=json.dumps({"cards": CARDS["cards"]})))
        db.commit()
    r = client.get("/ask-feed", headers=_hdr("zero@u.com"))
    assert r.status_code == 200 and called["n"] == 0
    body = r.json()
    assert "tickers" in body and "news_feed" in body and "groups" in body


@respx.mock
def test_ticker_on_demand_generates_then_serves_cache(monkeypatch):
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "od@u.com")

    route = respx.post("http://ae.test/agent/ask-feed").mock(
        return_value=httpx.Response(200, json=TICKER_CARDS))
    r = client.get("/ask-feed/ticker", params={"market": "US", "ticker": "AAPL", "name": "Apple"},
                   headers=_hdr("od@u.com"))
    assert r.status_code == 200
    body = r.json()
    assert body["cached"] is False and body["cards"][0]["kind"] == "filing_deep"
    sent = json.loads(route.calls[0].request.content)
    assert sent["scope"] == "ticker" and sent["ticker"] == "AAPL" and sent["limit"] == 5

    # fresh cache → served without another engine call
    r2 = client.get("/ask-feed/ticker", params={"market": "US", "ticker": "AAPL"},
                    headers=_hdr("od@u.com"))
    assert r2.status_code == 200 and r2.json()["cached"] is True
    assert route.call_count == 1


@respx.mock
def test_ticker_stale_cache_regenerates_and_unchanged_bumps_ttl(monkeypatch):
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    scope = _scope_key("US", "MSFT")
    stale = datetime.utcnow() - timedelta(seconds=settings.ask_feed_ticker_ttl_seconds + 60)
    with SessionLocal() as db:
        _mk_user(db, "st@u.com")
        db.merge(AskFeedCache(scope=scope, signature="sig-old", generated_at=stale,
                              payload=json.dumps({"cards": TICKER_CARDS["cards"],
                                                  "generated_at": "2026-07-01T00:00:00+00:00"})))
        db.commit()

    # engine says unchanged → same cards return, and generated_at is bumped (TTL reset)
    route = respx.post("http://ae.test/agent/ask-feed").mock(
        return_value=httpx.Response(200, json={"cards": [], "signature": "sig-old",
                                               "unchanged": True}))
    r = client.get("/ask-feed/ticker", params={"market": "US", "ticker": "MSFT"},
                   headers=_hdr("st@u.com"))
    assert r.status_code == 200 and r.json()["cards"]
    assert json.loads(route.calls[0].request.content)["prev_signature"] == "sig-old"
    with SessionLocal() as db:
        assert db.get(AskFeedCache, scope).generated_at > stale

    # now fresh again → no engine call
    client.get("/ask-feed/ticker", params={"market": "US", "ticker": "MSFT"},
               headers=_hdr("st@u.com"))
    assert route.call_count == 1


@respx.mock
def test_ticker_generation_failure_returns_honest_gap(monkeypatch):
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "gap@u.com")
    respx.post("http://ae.test/agent/ask-feed").mock(return_value=httpx.Response(500))
    r = client.get("/ask-feed/ticker", params={"market": "KR", "ticker": "005930"},
                   headers=_hdr("gap@u.com"))
    assert r.status_code == 200
    assert r.json()["cards"] == []          # a gap, never fabricated content


@respx.mock
def test_manual_refresh_endpoint_service_guarded(monkeypatch):
    """POST /ask-feed/refresh — admin ops 수동 갱신: 서비스 토큰 필수, refresh_once 1회 실행."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "adm@u.com")
    respx.post("http://ae.test/agent/ask-feed").mock(return_value=httpx.Response(200, json=CARDS))

    r = client.post("/ask-feed/refresh")                    # no token → rejected
    assert r.status_code == 401
    r = client.post("/ask-feed/refresh", headers={"X-Service-Token": SVC})
    assert r.status_code == 200
    body = r.json()
    assert body["refreshed"] == 1 and body["cards"] == 1 and body["generated_at"]


def test_read_through_kicks_refresh_once_on_stale_cache(monkeypatch):
    """GET /ask-feed — 캐시가 없거나 오래되면 백그라운드 갱신을 1회만 킥(single-flight),
    신선하면 킥하지 않는다. 응답은 언제나 캐시만으로 즉시."""
    import studioapi.askfeed as SAF
    calls = {"n": 0}

    async def fake_refresh_once():
        calls["n"] += 1
        return {"scopes": 1, "refreshed": 0}
    monkeypatch.setattr(SAF, "refresh_once", fake_refresh_once)
    SAF._kick_task = None
    SAF._kick_at = None

    with SessionLocal() as db:
        _mk_user(db, "rt@u.com")
        row = db.get(AskFeedCache, "news_feed")
        if row:  # 이전 테스트가 심었을 수 있음 → 확실히 오래된 상태로
            row.generated_at = datetime.utcnow() - timedelta(hours=6)
            db.commit()

    r1 = client.get("/ask-feed", headers=_hdr("rt@u.com"))
    r2 = client.get("/ask-feed", headers=_hdr("rt@u.com"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert calls["n"] == 1                                  # single-flight: 두 접속에 킥 1회

    # 신선한 캐시 → 킥 없음
    with SessionLocal() as db:
        db.merge(AskFeedCache(scope="news_feed", generated_at=datetime.utcnow(),
                              payload=json.dumps({"cards": CARDS["cards"]})))
        db.commit()
    SAF._kick_task = None
    SAF._kick_at = None
    client.get("/ask-feed", headers=_hdr("rt@u.com"))
    assert calls["n"] == 1
