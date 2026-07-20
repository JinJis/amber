"""M-DESK / DK-3 — desk-feed cache, invalidation, last_seen_at (agent-engine respx-mocked)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.models import DeskFeedCache, User
from studioapi.main import app

client = TestClient(app)
SVC = "dev-service-token"

FEED = {"cards": [{"kind": "market_pulse", "question": "오늘 시황?", "hook": "S&P +0.4%",
                   "citations": [{"tool": "yahoo__asset_classes", "source": "Yahoo Finance"}]}],
        "generated_at": "2026-07-03T00:00:00+00:00", "used_tools": ["yahoo__asset_classes"]}


def setup_module(_module):
    init_db()


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")


def _mock_control_plane():
    respx.post("http://cp.test/admin/provision").mock(
        return_value=httpx.Response(200, json={"project_id": "prj1", "api_key": "vgk_demo"}))
    respx.post("http://cp.test/admin/projects").mock(return_value=httpx.Response(200, json={"id": "prj1"}))
    respx.post("http://cp.test/admin/projects/prj1/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_demo"}))
    respx.post("http://cp.test/admin/projects/prj1/activations").mock(return_value=httpx.Response(200, json={}))


def _hdr(email: str) -> dict:
    return {"X-Service-Token": SVC, "X-User-Email": email}


@respx.mock
def test_desk_feed_caches_within_ttl_and_sets_last_seen(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk1@u.com"
    engine = respx.post("http://ae.test/agent/desk-feed").mock(
        return_value=httpx.Response(200, json=FEED))

    r1 = client.get("/desk-feed", headers=_hdr(email))
    assert r1.status_code == 200 and r1.json()["cached"] is False
    assert r1.json()["cards"][0]["question"] == "오늘 시황?"
    assert engine.call_count == 1
    with SessionLocal() as db:  # the visit marker advanced
        assert db.get(User, email).last_seen_at is not None

    # second load within TTL → cache, NO second engine call
    r2 = client.get("/desk-feed", headers=_hdr(email))
    assert r2.json()["cached"] is True
    assert engine.call_count == 1


@respx.mock
def test_desk_feed_regenerates_past_ttl_and_sends_since(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk2@u.com"
    seen_bodies: list[dict] = []

    def _capture(request):
        seen_bodies.append(json.loads(request.content))
        return httpx.Response(200, json=FEED)

    respx.post("http://ae.test/agent/desk-feed").mock(side_effect=_capture)

    client.get("/desk-feed", headers=_hdr(email))
    with SessionLocal() as db:  # age the cache past the TTL
        row = db.get(DeskFeedCache, email)
        row.generated_at = datetime.utcnow() - timedelta(seconds=settings.desk_feed_ttl_seconds + 5)
        db.commit()

    r = client.get("/desk-feed", headers=_hdr(email))
    assert r.json()["cached"] is False
    assert len(seen_bodies) == 2
    assert seen_bodies[0]["since"] is None          # first visit — no window
    assert seen_bodies[1]["since"] is not None      # second regen carries the previous visit


@respx.mock
def test_watchlist_edit_invalidates_desk_feed(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk3@u.com"
    engine = respx.post("http://ae.test/agent/desk-feed").mock(
        return_value=httpx.Response(200, json=FEED))

    client.get("/desk-feed", headers=_hdr(email))
    assert engine.call_count == 1

    wl = client.post("/watchlists", headers=_hdr(email), json={"name": "반도체"}).json()
    client.post(f"/watchlists/{wl['id']}/items", headers=_hdr(email),
                json={"market": "KR", "ticker": "005930", "name": "삼성전자"})

    # cache was dropped by the edits → next load regenerates with the new watchlist in context
    r = client.get("/desk-feed", headers=_hdr(email))
    assert r.json()["cached"] is False
    assert engine.call_count == 2
    body = json.loads(engine.calls[-1].request.content)
    assert body["watchlists"] and body["watchlists"][0]["name"] == "반도체"
    assert body["watchlists"][0]["items"][0]["ticker"] == "005930"


@respx.mock
def test_desk_feed_degrades_when_engine_down(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk4@u.com"
    respx.post("http://ae.test/agent/desk-feed").mock(return_value=httpx.Response(503))

    r = client.get("/desk-feed", headers=_hdr(email))
    assert r.status_code == 200                      # never a 500 — the zero state degrades
    assert r.json()["degraded"] is True and r.json()["cards"] == []


@respx.mock
def test_desk_feed_serves_stale_copy_when_engine_down_but_cache_exists(monkeypatch):
    """엔진이 죽어도 이전 캐시가 있으면 stale 사본을 서빙(stale:true) — degraded 공백이 아니라."""
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk6@u.com"
    with SessionLocal() as db:
        db.merge(User(email=email, project_id="p", api_key="vgk_x",
                      connectors_reconciled_ver=settings.connectors_reconcile_ver))
        db.merge(DeskFeedCache(user_email=email, payload=json.dumps(FEED),
                               generated_at=datetime.utcnow()
                               - timedelta(seconds=settings.desk_feed_ttl_seconds + 60)))
        db.commit()
    respx.post("http://ae.test/agent/desk-feed").mock(return_value=httpx.Response(503))
    r = client.get("/desk-feed", headers=_hdr(email))
    assert r.status_code == 200
    body = r.json()
    assert body.get("stale") is True and body.get("degraded") is None
    assert body["cards"][0]["kind"] == "market_pulse"        # 공백이 아니라 이전 카드


@respx.mock
def test_desk_feed_skips_cache_write_when_watchlist_changed_mid_generation(monkeypatch):
    """IMP-10: an edit during an in-flight generate must not poison the cache with a stale feed."""
    _cfg(monkeypatch); _mock_control_plane()
    email = "desk5@u.com"

    def _mutate_then_feed(request):
        # simulate a watchlist edit landing WHILE agent-engine is composing
        client.post("/watchlists", headers=_hdr(email), json={"name": "레이스"})
        return httpx.Response(200, json=FEED)

    respx.post("http://ae.test/agent/desk-feed").mock(side_effect=_mutate_then_feed)
    r = client.get("/desk-feed", headers=_hdr(email))
    assert r.status_code == 200 and r.json()["cached"] is False   # served best-effort
    with SessionLocal() as db:                                     # …but never cached
        assert db.get(DeskFeedCache, email) is None


def test_run_event_buffer_capped_and_tail_resumes(monkeypatch):
    """IMP-1: a chatty run trims its oldest events (bounded memory) while absolute-index tails
    still resume correctly from the retained head."""
    import asyncio

    from studioapi.runs import Run, RunManager

    async def go():
        m = RunManager()
        monkeypatch.setattr(RunManager, "_MAX_LIVE_EVENTS", 50)
        monkeypatch.setattr(RunManager, "_KEEP_FINISHED", 10)
        run = Run(id="r1", conversation_id="c1", cond=asyncio.Condition())
        for k in range(120):
            await m.append(run, {"type": "token", "k": k})
        assert len(run.events) == 50 and run.base == 70          # live cap holds

        got = []
        async def consume():
            async for ev in m.tail(run, from_index=100):          # absolute resume inside window
                got.append(ev)
        t = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await m.finish(run, "done")
        await asyncio.wait_for(t, 2)
        assert got and got[0]["k"] == 100 and got[-1]["k"] == 119  # no dupes, no gaps in-window
        assert len(run.events) == 10 and run.base == 110           # finished-run tail only

    asyncio.run(go())
