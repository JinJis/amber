"""Notification-alert tests — alerts/channels/templates CRUD, dispatch (simulated + real),
scheduler tick. External messenger HTTP is respx-mocked; with no credentials a delivery is
"simulated". Users are provisioned via the same control-plane mock as test_studio.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi import scheduler
from studioapi.alerts import compute_next_fire, render_message
from studioapi.channels import format_text
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import NotificationAlert
from studioapi.templates import seed_dashboard_templates

client = TestClient(app)
SVC = "dev-service-token"


def setup_module(_module):
    init_db()
    seed_dashboard_templates()


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")


def _mock_control_plane():
    respx.post("http://cp.test/admin/tenants").mock(return_value=httpx.Response(200, json={"id": "ten1"}))
    respx.post("http://cp.test/admin/tenants/ten1/projects").mock(return_value=httpx.Response(200, json={"id": "prj1"}))
    respx.post("http://cp.test/admin/projects/prj1/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_demo"}))
    respx.post("http://cp.test/admin/projects/prj1/activations").mock(return_value=httpx.Response(200, json={}))


def _hdr(email: str) -> dict:
    return {"X-Service-Token": SVC, "X-User-Email": email}


@respx.mock
def test_alert_crud_pause_resume(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt1@u.com"
    created = client.post("/alerts", headers=_hdr(email), json={
        "name": "FOMC 금리 워치", "scope": "board", "board_id": "brd_x", "trigger_type": "rate",
        "params": {"target": "@미국매크로"}, "schedule": {"freq": "daily", "time": "07:30"},
        "channels": ["telegram", "slack"], "source_spec": {"deeplink": "/?board=brd_x"},
    })
    assert created.status_code == 200, created.text
    a = created.json()
    aid = a["id"]
    assert aid.startswith("alt_") and a["status"] == "active" and a["trigger_label"] == "미 연준/한은 금리"
    assert a["scope"] == "board" and a["next_fire_at"]

    assert aid in {x["id"] for x in client.get("/alerts", headers=_hdr(email)).json()["alerts"]}
    assert client.get("/alerts", headers=_hdr("other@u.com")).json()["alerts"] == []  # tenant isolation
    # scope filter
    assert client.get("/alerts?scope=widget", headers=_hdr(email)).json()["alerts"] == []

    assert client.patch(f"/alerts/{aid}", headers=_hdr(email), json={"channels": ["telegram"]}).json()["channels"] == ["telegram"]
    assert client.post(f"/alerts/{aid}/pause", headers=_hdr(email)).json()["status"] == "paused"
    assert client.post(f"/alerts/{aid}/resume", headers=_hdr(email)).json()["status"] == "active"
    assert client.delete(f"/alerts/{aid}", headers=_hdr(email)).status_code == 200
    assert client.get(f"/alerts/{aid}", headers=_hdr(email)).status_code == 404


@respx.mock
def test_widget_scope_alert(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt2@u.com"
    a = client.post("/alerts", headers=_hdr(email), json={
        "name": "삼성 임계치", "scope": "widget", "pin_id": "pin_1", "trigger_type": "price_threshold",
        "params": {"target": "005930.KS", "threshold": "₩90,000"}, "schedule": {"freq": "realtime"},
        "channels": ["telegram"],
    }).json()
    assert a["scope"] == "widget" and a["pin_id"] == "pin_1"
    assert a["next_fire_at"] is None  # realtime → continuously checked, no scheduled time
    assert [x["id"] for x in client.get("/alerts?scope=widget", headers=_hdr(email)).json()["alerts"]] == [a["id"]]


@respx.mock
def test_bad_trigger_scope_channel_rejected(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt3@u.com"
    assert client.post("/alerts", headers=_hdr(email), json={"name": "x", "trigger_type": "nope", "channels": []}).status_code == 422
    assert client.post("/alerts", headers=_hdr(email), json={"name": "x", "trigger_type": "digest", "scope": "galaxy", "channels": []}).status_code == 422
    assert client.post("/alerts", headers=_hdr(email), json={"name": "x", "trigger_type": "digest", "channels": ["pigeon"]}).status_code == 422


@respx.mock
def test_fire_simulated_without_credentials(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt4@u.com"
    aid = client.post("/alerts", headers=_hdr(email), json={
        "name": "CPI 워치", "trigger_type": "macro_indicator", "params": {"target": "미 CPI"},
        "schedule": {"freq": "daily"}, "channels": ["telegram", "email"],
    }).json()["id"]
    fired = client.post(f"/alerts/{aid}/fire", headers=_hdr(email))
    assert fired.status_code == 200, fired.text
    ds = fired.json()["deliveries"]
    assert len(ds) == 2 and all(d["status"] == "simulated" for d in ds)
    for d in ds:
        p = d["payload"]
        assert p["source"] and p["as_of"] and p["deeplink"]
        assert "대시보드에서 보기" in format_text(p)
    # feeds
    assert len(client.get("/deliveries", headers=_hdr(email)).json()["deliveries"]) == 2
    assert len(client.get(f"/alerts/{aid}/deliveries", headers=_hdr(email)).json()["deliveries"]) == 2


@respx.mock
def test_channels_link_verify_and_real_send(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt5@u.com"
    tg = respx.post("https://api.telegram.org/botTKN/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))

    assert client.post("/channels", headers=_hdr(email), json={"channel": "telegram", "config": {"bot_token": "TKN", "chat_id": "42"}}).json()["connected"]
    chans = {c["channel"]: c for c in client.get("/channels", headers=_hdr(email)).json()["channels"]}
    assert chans["telegram"]["connected"] and chans["slack"]["connected"] is False
    # verify sends a test message and marks verified (never leaks the token)
    v = client.post("/channels/telegram/verify", headers=_hdr(email)).json()
    assert v["status"] == "sent" and v["verified"]

    aid = client.post("/alerts", headers=_hdr(email), json={
        "name": "삼성 실적", "trigger_type": "earnings", "params": {"target": "005930.KS"},
        "schedule": {"freq": "weekly"}, "channels": ["telegram"],
    }).json()["id"]
    d = client.post(f"/alerts/{aid}/fire", headers=_hdr(email)).json()["deliveries"][0]
    assert d["status"] == "sent" and tg.called


def test_render_message_factual_and_sourced():
    a = NotificationAlert(id="alt_x", user_email="u@u.com", name="t", scope="board",
                          trigger_type="rate", params=json.dumps({"target": "@미국매크로"}),
                          schedule="{}", channels="[]", source_spec=json.dumps({"deeplink": "/?b=1"}))
    p = render_message(a, now=datetime(2026, 6, 23))
    assert p["as_of"] == "2026-06-23" and p["deeplink"] == "/?b=1"
    assert p["source"] == "FOMC · CME FedWatch · 한은"
    assert not any(tok in p["body"] for tok in ("매수", "매도", "목표가"))


def test_compute_next_fire():
    now = datetime(2026, 6, 23, 9, 0)
    assert compute_next_fire({"freq": "interval", "every_minutes": 30}, now=now) == now + timedelta(minutes=30)
    assert compute_next_fire({"freq": "realtime"}, now=now) is None
    d = compute_next_fire({"freq": "daily", "time": "07:30"}, now=now)
    assert d.hour == 7 and d.minute == 30 and d > now


@respx.mock
def test_scheduler_tick_fires_due_active_alerts(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt6@u.com"
    aid = client.post("/alerts", headers=_hdr(email), json={
        "name": "주간 요약", "trigger_type": "digest", "params": {"target": "@반도체"},
        "schedule": {"freq": "weekly", "time": "08:00"}, "channels": ["slack"],
    }).json()["id"]
    with SessionLocal() as db:
        db.get(NotificationAlert, aid).next_fire_at = datetime.utcnow() - timedelta(minutes=5)
        db.commit()
    assert scheduler.tick(now=datetime.utcnow()) >= 1
    feed = client.get(f"/deliveries?alert_id={aid}", headers=_hdr(email)).json()["deliveries"]
    assert len(feed) == 1 and feed[0]["channel"] == "slack"

    # paused alerts do not fire
    client.post(f"/alerts/{aid}/pause", headers=_hdr(email))
    with SessionLocal() as db:
        db.get(NotificationAlert, aid).next_fire_at = datetime.utcnow() - timedelta(minutes=5)
        db.commit()
    before = len(client.get(f"/deliveries?alert_id={aid}", headers=_hdr(email)).json()["deliveries"])
    scheduler.tick(now=datetime.utcnow())
    after = len(client.get(f"/deliveries?alert_id={aid}", headers=_hdr(email)).json()["deliveries"])
    assert after == before


@respx.mock
def test_onboarding_flag(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "onb@u.com"
    assert client.get("/users/me", headers=_hdr(email)).json()["onboarded"] is False
    assert client.post("/users/ensure", headers=_hdr(email)).json()["onboarded"] is False
    assert client.post("/users/onboarded", headers=_hdr(email)).json()["onboarded"] is True
    assert client.get("/users/me", headers=_hdr(email)).json()["onboarded"] is True


@respx.mock
def test_templates_and_from_template(monkeypatch):
    _cfg(monkeypatch); _mock_control_plane()
    email = "alt7@u.com"
    tpls = client.get("/templates", headers=_hdr(email)).json()["templates"]
    assert {t["id"] for t in tpls} >= {"dt_semi", "dt_macro"}
    semi = next(t for t in tpls if t["id"] == "dt_semi")
    assert len(semi["widgets"]) == 6

    # materialize onto the default board
    board = client.get("/boards", headers=_hdr(email)).json()["boards"][0]["id"]
    r = client.post("/board/from-template", headers=_hdr(email), json={"template_id": "dt_semi", "board_id": board})
    assert r.status_code == 200 and r.json()["created"] == 6
    pins = client.get(f"/board?board_id={board}", headers=_hdr(email)).json()["pinned"]
    assert len(pins) == 6
    assert any(p["spec"].get("tool") == "yahoo__prices" for p in pins)  # live-refreshable widget
    # idempotent: re-applying the same template adds nothing (no duplicate widgets)
    r2 = client.post("/board/from-template", headers=_hdr(email), json={"template_id": "dt_semi", "board_id": board})
    assert r2.status_code == 200 and r2.json()["created"] == 0
    assert len(client.get(f"/board?board_id={board}", headers=_hdr(email)).json()["pinned"]) == 6
    assert client.post("/board/from-template", headers=_hdr(email), json={"template_id": "nope"}).status_code == 404


@respx.mock
def test_board_digest_summarizes_periodic_widgets_only(monkeypatch):
    """The root dashboard alert (scope=board, digest) summarizes the board's PERIODIC widgets and
    excludes one-shot values — the periodicity gate carried on each pin's spec.cadence."""
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "digest@u.com"
    bid = client.post("/boards", headers=_hdr(email), json={"name": "거시 보드"}).json()["id"]
    # a periodic widget (scheduled macro release) + a one-shot value (DCF valuation)
    client.post("/board", headers=_hdr(email), json={
        "spec": {"kind": "timeseries", "title": "미 기준금리", "source": "FRED",
                 "as_of": "2024-03-01", "cadence": "scheduled", "category": "macro"},
        "board_ids": [bid]})
    client.post("/board", headers=_hdr(email), json={
        "spec": {"kind": "kpi", "title": "밸류에이션 DCF", "source": "재무제표 기반 모델",
                 "as_of": "2024-02-01", "cadence": "one_shot", "category": "valuation"},
        "board_ids": [bid]})
    aid = client.post("/alerts", headers=_hdr(email), json={
        "name": "보드 요약", "scope": "board", "board_id": bid, "trigger_type": "digest",
        "schedule": {"freq": "weekly", "time": "08:00"}, "channels": ["telegram"]}).json()["id"]
    fired = client.post(f"/alerts/{aid}/fire", headers=_hdr(email))
    assert fired.status_code == 200, fired.text
    body = fired.json()["deliveries"][0]["payload"]["body"]
    assert "미 기준금리" in body          # periodic widget IS summarized
    assert "FRED" in body                  # with its source
    assert "밸류에이션 DCF" not in body    # one-shot value is excluded from the digest


@respx.mock
def test_widget_alert_fetches_fresh_data_on_fire(monkeypatch):
    """A widget-backed alert RE-FETCHES its data via the widget's tool+args (the agent-engine
    refresh path, with the user's tenant key) and delivers the FRESH value — not a template."""
    _cfg(monkeypatch)
    _mock_control_plane()
    # the agent-engine artifact refresh returns a fresh price artifact (latest close 211.5)
    respx.post("http://ae.test/agent/artifact/refresh").mock(return_value=httpx.Response(200, json={
        "artifact": {
            "kind": "candlestick", "title": "AAPL 종가", "source": "Yahoo Finance", "as_of": "2026-06-25",
            "candles": [{"x": "2026-06-24", "close": 200.0}, {"x": "2026-06-25", "close": 211.5}],
        }}))
    email = "freshfetch@u.com"
    aid = client.post("/alerts", headers=_hdr(email), json={
        "name": "AAPL 가격", "scope": "widget", "pin_id": "pin_x", "trigger_type": "price_threshold",
        "schedule": {"freq": "interval", "every_minutes": 60}, "channels": ["telegram"],
        "source_spec": {"tool": "yahoo__prices", "args": {"ticker": "AAPL"}, "source": "Yahoo Finance"},
    }).json()["id"]
    fired = client.post(f"/alerts/{aid}/fire", headers=_hdr(email))
    assert fired.status_code == 200, fired.text
    payload = fired.json()["deliveries"][0]["payload"]
    assert "211.5" in payload["body"]              # the FRESHLY fetched close, not a static template
    assert payload["source"] == "Yahoo Finance" and payload["as_of"] == "2026-06-25"
