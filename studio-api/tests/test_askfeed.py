"""ASK-6 — ask-feed: news_feed background refresh (signature skip · honesty on empty),
the one-DB-read assembly, and the on-demand per-ticker pool (cache TTL · no LLM when fresh)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.askfeed import (
    _ONB_SCOPE, _SECTION_SCOPES, _assemble, _scope_key, refresh_once,
    refresh_onboarding_once, refresh_sections_once,
)
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
        # ME-2: stamp the current reconcile version so ensure_user() short-circuits without the
        # over-the-network default-connector reconcile — keeps these tests offline.
        u = User(email=email, tenant_id="t1", project_id="p1", api_key=key,
                 connectors_reconciled_ver=settings.connectors_reconcile_ver)
        db.add(u)
        db.commit()
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
    sent = json.loads(route.calls[0].request.content)
    assert sent["scope"] == "news_feed"
    assert sent["limit"] == 20            # Macro Trends 마키 — 심층·다양 질문 20장
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


@respx.mock
def test_refresh_sections_once_covers_each_section_scope(monkeypatch):
    """섹션 리프레셔는 시장 전체 스코프(어닝·거장·히스토리)를 각각 1회 생성 — 티커 스윕 아님."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "sec@u.com")

    seen: list[str] = []

    def _resp(request):
        scope = json.loads(request.content)["scope"]
        seen.append(scope)
        return httpx.Response(200, json={"cards": [{"kind": "earnings_upcoming",
                                                    "question": "실적 전에 볼까요?", "hook": "다음 발표 임박",
                                                    "citations": [{"source": "API Ninjas / FMP"}]}],
                                         "signature": f"sig-{scope}", "unchanged": False,
                                         "generated_at": "2026-07-05T00:00:00+00:00"})
    respx.post("http://ae.test/agent/ask-feed").mock(side_effect=_resp)

    out = asyncio.run(refresh_sections_once())
    want = [s["scope"] for s in _SECTION_SCOPES]
    assert set(seen) == set(want) and out["scopes"] == len(want) and out["refreshed"] == len(want)
    assert "news_feed" not in seen and "ticker" not in seen   # 뉴스·티커는 이 리프레셔의 몫이 아님
    with SessionLocal() as db:
        for scope in want:
            row = db.get(AskFeedCache, scope)
            assert row is not None and row.signature == f"sig-{scope}"


def test_assemble_includes_section_marquees():
    with SessionLocal() as db:
        _mk_user(db, "sx@u.com")
        # 섹션 캐시는 스코프 PK로 전역 공유 → 다른 테스트가 남긴 행을 지우고 이 테스트 상태로.
        for s in _SECTION_SCOPES:
            row = db.get(AskFeedCache, s["scope"])
            if row:
                db.delete(row)
        db.commit()
        db.merge(AskFeedCache(scope="news_feed",
                              payload=json.dumps({"cards": CARDS["cards"],
                                                  "generated_at": "2026-07-05T00:05:00+00:00"})))
        db.merge(AskFeedCache(scope="earnings_radar",
                              payload=json.dumps({"cards": [{"kind": "earnings_upcoming",
                                                             "question": "실적 전에 볼까요?", "hook": "발표 임박"}],
                                                  "generated_at": "2026-07-05T00:06:00+00:00"})))
        # guru_flows는 비어 있음 → sections에 안 실린다(빈 섹션은 프런트가 안 그리게 데이터 단계에서 제외)
        db.commit()
        out = _assemble(db, "sx@u.com")
    scopes = [s["scope"] for s in out["sections"]]
    assert scopes[0] == "news_feed"                            # Macro Trends 맨 앞
    assert "earnings_radar" in scopes and "guru_flows" not in scopes
    earn = next(s for s in out["sections"] if s["scope"] == "earnings_radar")
    assert earn["cards"][0]["kind"] == "earnings_upcoming"
    assert out["news_feed"][0]["kind"] == "macro"             # backward-compat 필드 유지


def test_assemble_lists_tickers_without_cards_plus_news():
    with SessionLocal() as db:
        _mk_user(db, "asm@u.com")
        _mk_watch(db, "asm@u.com", "TSLA", name="Tesla")
        db.add(Watchlist(user_email="asm@u.com", name="빈그룹"))   # 방금 만든 빈 그룹도 보인다
        db.commit()
        db.merge(AskFeedCache(scope="news_feed",
                              payload=json.dumps({"cards": CARDS["cards"],
                                                  "generated_at": "2026-07-05T00:05:00+00:00"})))
        db.commit()
        out = _assemble(db, "asm@u.com")
    t = next(x for x in out["tickers"] if x["ticker"] == "TSLA")
    assert t["name"] == "Tesla" and t["groups"] and "cards" not in t
    # 그룹은 {id, name} — 엔트리 아코디언의 1차 depth + 관심 페이지 딥링크(id)
    assert all(g["id"] and g["name"] for g in out["groups"])
    empty = next(g for g in out["groups"] if g["name"] == "빈그룹")
    assert empty["id"].startswith("wl")
    assert t["groups"][0] in {g["name"] for g in out["groups"]}
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


def test_ticker_in_proc_single_flight_collapses_concurrent_taps(monkeypatch):
    """ME-1: 같은 티커를 동시에 탭하면 in-proc 락이 생성을 1회로 합친다 — 두 번째는 첫 결과를
    서빙(중복 2단계 Gemini 방지). (교차노드 PG advisory lock은 SQLite no-op이라 여기선 미검증 —
    PG 통합 환경 필요.)"""
    import studioapi.askfeed as SAF
    with SessionLocal() as db:
        _mk_user(db, "sf@u.com")
    scope = _scope_key("US", "COIN")
    calls = {"n": 0}

    async def slow_refresh(client, db, *, scope, api_key, body, timeout):
        calls["n"] += 1
        await asyncio.sleep(0.2)                       # 첫 생성이 도는 동안 두 번째가 겹치게
        row = db.get(AskFeedCache, scope) or AskFeedCache(scope=scope)
        row.payload = json.dumps({"cards": TICKER_CARDS["cards"]})
        row.signature, row.generated_at = "s", datetime.utcnow()
        db.merge(row); db.commit()
        return True
    monkeypatch.setattr(SAF, "_refresh_scope", slow_refresh)
    SAF._ticker_flight.clear()

    async def _inner():
        u = User(email="sf@u.com", api_key="vgk_x")
        return await asyncio.gather(SAF.get_ticker_feed("US", "COIN", None, u),
                                    SAF.get_ticker_feed("US", "COIN", None, u))
    r1, r2 = asyncio.run(_inner())
    assert r1["cards"] and r2["cards"]                 # 둘 다 카드를 받는다
    assert calls["n"] == 1                             # 동시 탭 2회지만 생성은 1회


@respx.mock
def test_ticker_failure_serves_stale_pool_when_present(monkeypatch):
    """생성 실패라도 이전 풀이 있으면 그걸 서빙(정직한 공백은 캐시가 아예 없을 때만)."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    scope = _scope_key("US", "AMD")
    stale = datetime.utcnow() - timedelta(seconds=settings.ask_feed_ticker_ttl_seconds + 60)
    with SessionLocal() as db:
        _mk_user(db, "sp@u.com")
        db.merge(AskFeedCache(scope=scope, signature="sig-amd", generated_at=stale,
                              payload=json.dumps({"cards": TICKER_CARDS["cards"]})))
        db.commit()
    respx.post("http://ae.test/agent/ask-feed").mock(return_value=httpx.Response(500))
    r = client.get("/ask-feed/ticker", params={"market": "US", "ticker": "AMD"},
                   headers=_hdr("sp@u.com"))
    assert r.status_code == 200
    assert r.json()["cards"][0]["kind"] == "filing_deep"     # 실패해도 이전 풀 서빙(공백 아님)


@respx.mock
def test_onboarding_showcase_stores_then_keeps_on_empty(monkeypatch):
    """ONB-LIVE: refresh_onboarding_once가 라이브 번들을 캐시에 저장하고, 이후 빈/실패 응답엔
    이전 캐시를 유지(정직 규칙)."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "onb@u.com")
    bundle = {"question": "삼성전자 실적·수급 어때?", "name": "삼성전자", "ticker": "005930",
              "cards": [{"kind": "fundamental_shift", "question": "재무 변화 볼까요?", "hook": "PER 12.3배",
                         "citations": [{"source": "SEC EDGAR"}]}],
              "evidence": [], "followups": ["다음 실적은?"], "signature": "onb-1"}
    route = respx.post("http://ae.test/agent/onboarding-showcase").mock(
        return_value=httpx.Response(200, json=bundle))
    out = asyncio.run(refresh_onboarding_once())
    assert out["refreshed"] is True and out["cards"] == 1
    with SessionLocal() as db:
        assert json.loads(db.get(AskFeedCache, _ONB_SCOPE).payload)["cards"][0]["hook"] == "PER 12.3배"

    route.mock(return_value=httpx.Response(200, json={"cards": []}))   # 빈 응답 → 이전 유지
    out2 = asyncio.run(refresh_onboarding_once())
    assert out2["refreshed"] is False
    with SessionLocal() as db:
        assert json.loads(db.get(AskFeedCache, _ONB_SCOPE).payload)["cards"]   # 여전히 이전 카드


def test_onboarding_endpoint_serves_cache_and_kicks_when_stale(monkeypatch):
    """GET /ask-feed/onboarding — 캐시만 즉시 반환하고 24h 넘으면 백그라운드 갱신 1회 킥;
    신선하면 킥 없음."""
    import studioapi.askfeed as SAF
    calls = {"n": 0}

    async def fake_refresh():
        calls["n"] += 1
        return {"refreshed": False}
    monkeypatch.setattr(SAF, "refresh_onboarding_once", fake_refresh)
    SAF._onb_task = None
    with SessionLocal() as db:
        _mk_user(db, "onbe@u.com")
        db.merge(AskFeedCache(scope=_ONB_SCOPE,
                              generated_at=datetime.utcnow() - timedelta(hours=25),
                              payload=json.dumps({"cards": [{"kind": "x"}], "name": "삼성전자"})))
        db.commit()
    r = client.get("/ask-feed/onboarding", headers=_hdr("onbe@u.com"))
    assert r.status_code == 200 and r.json()["name"] == "삼성전자"    # 캐시 즉시 반환
    assert calls["n"] == 1                                            # stale → 킥 1회

    with SessionLocal() as db:                                        # 신선한 캐시 → 킥 없음
        db.merge(AskFeedCache(scope=_ONB_SCOPE, generated_at=datetime.utcnow(),
                              payload=json.dumps({"cards": [{"kind": "x"}], "name": "삼성전자"})))
        db.commit()
    SAF._onb_task = None
    client.get("/ask-feed/onboarding", headers=_hdr("onbe@u.com"))
    assert calls["n"] == 1


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


@respx.mock
def test_refresh_sections_endpoint_service_guarded(monkeypatch):
    """POST /ask-feed/refresh-sections — 어닝·거장·히스토리 수동 갱신: 서비스 토큰 필수."""
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    with SessionLocal() as db:
        _mk_user(db, "rs@u.com")
    respx.post("http://ae.test/agent/ask-feed").mock(return_value=httpx.Response(200, json=CARDS))

    r = client.post("/ask-feed/refresh-sections")                # no token → rejected
    assert r.status_code == 401
    r = client.post("/ask-feed/refresh-sections", headers={"X-Service-Token": SVC})
    assert r.status_code == 200
    body = r.json()
    assert body["scopes"] == len(_SECTION_SCOPES) and "cards_by_scope" in body


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
