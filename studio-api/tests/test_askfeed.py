"""ASK-5 — ask-feed cache: refresher upsert (signature skip · honesty on empty), scope union
across users, and the one-DB-read assembly the entry screen depends on."""

from __future__ import annotations

import asyncio
import json

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.askfeed import _assemble, _scope_key, _watched_scopes, refresh_once
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import AskFeedCache, User, Watchlist, WatchlistItem

client = TestClient(app)
SVC = "dev-service-token"

CARDS = {"cards": [{"kind": "filing_deep", "question": "새 공시 위험요소 보여줘",
                    "hook": "새 공시 접수", "citations": [{"source": "SEC EDGAR"}]}],
         "signature": "sig-1", "unchanged": False, "generated_at": "2026-07-05T00:00:00+00:00"}


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


def test_watched_scopes_unions_across_users():
    with SessionLocal() as db:
        _mk_user(db, "a@u.com"); _mk_user(db, "b@u.com")
        _mk_watch(db, "a@u.com", "AAPL", name="Apple")
        _mk_watch(db, "b@u.com", "AAPL")           # same ticker → ONE scope (shared pool)
        _mk_watch(db, "b@u.com", "005930", market="KR", name="삼성전자")
        scopes = {s["scope"]: s for s in _watched_scopes(db)}
    assert set(scopes) >= {"ticker:US:AAPL", "ticker:KR:005930"}
    assert scopes["ticker:US:AAPL"]["api_key"]      # a watcher's key rides along


@respx.mock
def test_refresh_once_upserts_and_signature_skips(monkeypatch):
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "r@u.com")
        _mk_watch(db, "r@u.com", "NVDA", name="NVIDIA")

    route = respx.post("http://ae.test/agent/ask-feed").mock(
        return_value=httpx.Response(200, json=CARDS))
    out = asyncio.run(refresh_once())
    assert out["refreshed"] >= 1
    with SessionLocal() as db:
        row = db.get(AskFeedCache, "ticker:US:NVDA")
        assert row is not None and row.signature == "sig-1"
        assert json.loads(row.payload)["cards"][0]["kind"] == "filing_deep"

    # second tick: engine says unchanged → cards kept, row NOT overwritten
    route.mock(return_value=httpx.Response(200, json={"cards": [], "signature": "sig-1",
                                                      "unchanged": True}))
    out2 = asyncio.run(refresh_once())
    assert out2["refreshed"] == 0
    with SessionLocal() as db:
        assert db.get(AskFeedCache, "ticker:US:NVDA").signature == "sig-1"

    # engine fails (empty cards, no signature) → previous generation preserved (honesty rule)
    route.mock(return_value=httpx.Response(200, json={"cards": [], "signature": None,
                                                      "unchanged": False}))
    asyncio.run(refresh_once())
    with SessionLocal() as db:
        assert json.loads(db.get(AskFeedCache, "ticker:US:NVDA").payload)["cards"]


def test_assemble_reads_cache_and_lists_pending():
    with SessionLocal() as db:
        _mk_user(db, "asm@u.com")
        _mk_watch(db, "asm@u.com", "MSFT", name="Microsoft")     # no cache yet → pending
        _mk_watch(db, "asm@u.com", "TSLA", name="Tesla")
        db.merge(AskFeedCache(scope=_scope_key("US", "TSLA"),
                              payload=json.dumps({"cards": CARDS["cards"],
                                                  "generated_at": "2026-07-05T00:00:00+00:00"}),
                              signature="s"))
        db.merge(AskFeedCache(scope="hot_trend",
                              payload=json.dumps({"cards": [{"kind": "macro", "question": "CPI 추이?",
                                                             "hook": "미 CPI 3.1%",
                                                             "citations": [{"source": "FRED"}]}],
                                                  "generated_at": "2026-07-05T00:05:00+00:00"})))
        db.commit()
        out = _assemble(db, "asm@u.com")
    assert [p["ticker"] for p in out["pending"]] == ["MSFT"]
    assert out["tickers"][0]["ticker"] == "TSLA" and out["tickers"][0]["cards"]
    assert out["hot_trend"][0]["kind"] == "macro"


def test_ask_feed_endpoint_zero_llm(monkeypatch):
    """GET /ask-feed is a pure cache read — no engine call happens at request time."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("engine must not be called at request time")
    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    r = client.get("/ask-feed", headers=_hdr("asm@u.com"))
    assert r.status_code == 200 and called["n"] == 0
    body = r.json()
    assert "tickers" in body and "hot_trend" in body and "pending" in body
