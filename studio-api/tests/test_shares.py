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
    respx.post("http://cp.test/admin/projects").mock(return_value=httpx.Response(200, json={"id": "prj1"}))
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
def test_share_whole_answer_snapshot(monkeypatch):
    """A whole chat answer shares as kind=answer — pure content snapshot, NO user identity."""
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "public_base_url", "https://vg.example")
    _cp()
    payload = {
        "content": "삼성전자 영업이익은 6.5조였어요 [1]. {{figure:1}}",
        "artifacts": [{"kind": "table", "title": "실적", "table": [["항목", "값"], ["영업이익", "6.5조"]]}],
        "citations": [{"index": 1, "source": "DART", "as_of": "2026-05-15", "used": True, "kind": "filing"}],
        "audit": {"checked": 1, "supported": 1, "unsupported": [], "ledger": []},
    }
    r = client.post("/shares", headers=_hdr("ans@u.com"), json={
        "kind": "answer", "title": "삼성전자 이번 분기 실적 정리", "payload": payload,
        "audit": payload["audit"]})
    assert r.status_code == 200
    tok = r.json()["token"]
    pub = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC})
    assert pub.status_code == 200
    body = pub.json()
    assert body["kind"] == "answer"
    # the content + provenance travel; NO user identity in the public payload
    assert body["payload"]["content"] == payload["content"]
    assert body["payload"]["citations"][0]["source"] == "DART"
    dumped = str(body)
    assert "ans@u.com" not in dumped and "conversation" not in dumped and "user_email" not in dumped


@respx.mock
def test_share_answer_refused_below_trust_floor(monkeypatch):
    """An answer carrying an unsupported number is refused, same trust floor as artifacts."""
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    r = client.post("/shares", headers=_hdr("ans2@u.com"), json={
        "kind": "answer", "title": "t", "payload": {"content": "매출 99조 [1]"},
        "audit": {"checked": 1, "unsupported": ["99조"]}})
    assert r.status_code == 422 and "99조" in r.json()["detail"]


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


@respx.mock
def test_share_expiry_410(monkeypatch):
    from datetime import datetime, timedelta

    from studioapi.config import settings
    from studioapi.db import SessionLocal
    from studioapi.models import ShareLink
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    tok = client.post("/shares", headers=_hdr("sh5@u.com"), json={
        "kind": "artifact", "title": "t", "payload": {}}).json()["token"]
    with SessionLocal() as db:  # age it past the TTL
        db.get(ShareLink, tok).expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()
    r = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC})
    assert r.status_code == 410 and "만료" in r.json()["detail"]


# --- SH-2b: OG card image attach + public serve ---------------------------------------
# a minimal valid 1x1 PNG (the endpoint only checks the PNG signature + size)
_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@respx.mock
def test_share_image_attach_and_public_serve(monkeypatch):
    import base64

    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "public_base_url", "https://vg.example")
    _cp()
    tok = client.post("/shares", headers=_hdr("img@u.com"), json={
        "kind": "artifact", "title": "카드", "payload": ART}).json()["token"]

    # no image yet → 404, and the public read reports has_image=false
    assert client.get(f"/shares/{tok}/image", headers={"X-Service-Token": SVC}).status_code == 404
    assert client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).json()["has_image"] is False

    data_url = "data:image/png;base64," + base64.b64encode(_PNG_1x1).decode()
    r = client.put(f"/shares/{tok}/image", headers=_hdr("img@u.com"), json={"data_url": data_url})
    assert r.status_code == 200 and r.json()["image_url"] == f"/shares/{tok}/image"

    # public serve returns the exact PNG bytes, cacheable
    img = client.get(f"/shares/{tok}/image", headers={"X-Service-Token": SVC})
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    assert img.content == _PNG_1x1 and "max-age" in img.headers.get("cache-control", "")
    assert client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).json()["has_image"] is True

    # non-owner cannot attach; non-PNG is rejected; revoked hides the image
    assert client.put(f"/shares/{tok}/image", headers=_hdr("other@u.com"),
                      json={"data_url": data_url}).status_code == 404
    assert client.put(f"/shares/{tok}/image", headers=_hdr("img@u.com"),
                      json={"data_url": "data:image/png;base64,Zm9v"}).status_code == 422  # 'foo' → not PNG
    client.delete(f"/shares/{tok}", headers=_hdr("img@u.com"))
    assert client.get(f"/shares/{tok}/image", headers={"X-Service-Token": SVC}).status_code == 404


@respx.mock
def test_view_beacon_counts_and_skips_revoked(monkeypatch):
    """V-4: 공개 뷰 비콘 — +1 실측, revoked/만료엔 안 셈. 서비스 토큰만(비로그인 뷰어)."""
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    tok = client.post("/shares", headers=_hdr("vw@u.com"), json={
        "kind": "artifact", "title": "t", "payload": ART}).json()["token"]
    assert client.post(f"/shares/{tok}/view", headers={"X-Service-Token": SVC}).json()["views"] == 1
    client.post(f"/shares/{tok}/view", headers={"X-Service-Token": SVC})
    pub = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).json()
    assert pub["views"] == 2
    mine = client.get("/shares", headers=_hdr("vw@u.com")).json()["shares"]
    assert next(s for s in mine if s["token"] == tok)["views"] == 2
    client.delete(f"/shares/{tok}", headers=_hdr("vw@u.com"))
    assert client.post(f"/shares/{tok}/view", headers={"X-Service-Token": SVC}).json()["ok"] is False


@respx.mock
def test_share_read_path_is_write_free(monkeypatch):
    """ME-6: a public share view must NOT perform a write. The sharer's referral code is ensured at
    CREATE (an authenticated request); the high-traffic public read looks it up read-only and never
    mints one — so the read can be served by a replica."""
    from studioapi.config import settings
    from studioapi.db import SessionLocal
    from studioapi.models import User
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    tok = client.post("/shares", headers=_hdr("refshare@u.com"),
                      json={"kind": "artifact", "title": "t", "payload": ART,
                            "audit": {"checked": 1, "unsupported": []}}).json()["token"]
    with SessionLocal() as db:
        code = db.get(User, "refshare@u.com").referral_code
    assert code   # ensured at CREATE, not on read

    pub = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).json()
    assert pub["referral_code"] == code

    # a read for a user whose code is missing must NOT mint one
    with SessionLocal() as db:
        u = db.get(User, "refshare@u.com")
        u.referral_code = None
        db.commit()
    pub2 = client.get(f"/shares/{tok}", headers={"X-Service-Token": SVC}).json()
    assert pub2["referral_code"] is None
    with SessionLocal() as db:
        assert db.get(User, "refshare@u.com").referral_code is None   # the read wrote nothing
