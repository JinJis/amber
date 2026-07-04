"""M-SHARE / SH-1 — share links: snapshot immutability, SNS intent URLs, QT-2 gate, revoke→410."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.db import init_db
from studioapi.main import app

client = TestClient(app)
SVC = "dev-service-token"


def setup_module(_m):
    init_db()


def _cp():
    respx.post("http://cp.test/admin/tenants").mock(return_value=httpx.Response(200, json={"id": "ten1"}))
    respx.post("http://cp.test/admin/tenants/ten1/projects").mock(return_value=httpx.Response(200, json={"id": "prj1"}))
    respx.post("http://cp.test/admin/projects/prj1/keys").mock(return_value=httpx.Response(200, json={"api_key": "k"}))
    respx.post("http://cp.test/admin/projects/prj1/activations").mock(return_value=httpx.Response(200, json={}))


def _hdr(email):
    return {"X-Service-Token": SVC, "X-User-Email": email}


ART = {"kind": "base_rates", "title": "베이스레이트", "label": "과거 기록 · 전망 아님"}


@respx.mock
def test_share_lifecycle_and_sns_urls(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "public_base_url", "https://vg.example")
    _cp()
    r = client.post("/shares", headers=_hdr("sh1@u.com"), json={
        "kind": "artifact", "title": "S&P −5% 이후 20일", "payload": ART,
        "audit": {"checked": 4, "unsupported": []}})
    assert r.status_code == 200
    s = r.json()
    tok = s["token"]
    # every SNS gets a ready-made link, all pointing at the public page
    u = s["share_urls"]
    assert u["page"] == f"https://vg.example/s/{tok}"
    assert tok in u["x"] and "twitter.com/intent" in u["x"]
    assert "threads.net/intent" in u["threads"] and "t.me/share" in u["telegram"]
    assert u["kakao"] == u["page"]

    # public read: NO user header — service token only (the web /s page calls server-side)
    pub = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC})
    assert pub.status_code == 200 and pub.json()["payload"]["label"] == "과거 기록 · 전망 아님"

    # snapshot immutability: stored payload is a copy, not a live ref
    assert pub.json()["payload"] == ART

    # revoke → public read 410 (게시자가 해제)
    assert client.delete(f"/shares/{tok}", headers=_hdr("sh1@u.com")).status_code == 200
    assert client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).status_code == 410
    assert client.get("/shares/nope", headers={"X-Service-Token": SVC}).status_code == 404


@respx.mock
def test_share_refused_below_trust_floor(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    # QT-2 gate: unsupported numbers never leave the app
    r = client.post("/shares", headers=_hdr("sh2@u.com"), json={
        "kind": "artifact", "title": "t", "payload": ART,
        "audit": {"checked": 3, "unsupported": ["47.3%"]}})
    assert r.status_code == 422 and "47.3%" in r.json()["detail"]


@respx.mock
def test_share_ownership_isolated(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    tok = client.post("/shares", headers=_hdr("sh3@u.com"), json={
        "kind": "quote", "title": "q", "payload": {}}).json()["token"]
    # another user cannot revoke it
    assert client.delete(f"/shares/{tok}", headers=_hdr("sh4@u.com")).status_code == 404
    mine = client.get("/shares", headers=_hdr("sh3@u.com")).json()["shares"]
    assert any(s["token"] == tok for s in mine)
