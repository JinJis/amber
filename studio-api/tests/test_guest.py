"""GUEST-1 — 익명 게스트 배관 테스트 (control-plane admin은 respx 목)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.guest import ensure_guest, gid_of, guest_email, is_guest_email
from studioapi.main import app
from studioapi.models import GuestSession, ServiceState, TurnUsage, User

client = TestClient(app)
GID_A, GID_B = "a" * 32, "b" * 32


def setup_module(_module):
    init_db()


def _reset_guest_state():
    from studioapi.models import Conversation

    with SessionLocal() as db:
        db.query(ServiceState).delete()
        db.query(GuestSession).delete()
        db.query(User).filter(User.email.like("guest_%@guest.local")).delete(synchronize_session=False)
        db.query(TurnUsage).filter(TurnUsage.user_email.like("guest_%@guest.local")).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.user_email.like("guest_%@guest.local")).delete(synchronize_session=False)
        db.commit()


def _mock_cp():
    respx.post("http://cp.test/admin/projects").mock(return_value=httpx.Response(200, json={"id": "prjG"}))
    respx.post("http://cp.test/admin/projects/prjG/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_guest"}))
    respx.post("http://cp.test/admin/projects/prjG/activations").mock(return_value=httpx.Response(200, json={}))
    respx.patch("http://cp.test/admin/projects/prjG").mock(return_value=httpx.Response(200, json={"plan": "guest"}))


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")
    monkeypatch.setattr(settings, "feature_guest", True)


def test_guest_email_helpers():
    e = guest_email(GID_A)
    assert is_guest_email(e) and gid_of(e) == GID_A
    assert not is_guest_email("user@x.com") and gid_of("user@x.com") is None


@respx.mock
async def test_ensure_guest_shares_one_project(monkeypatch):
    _cfg(monkeypatch)
    _reset_guest_state()
    _mock_cp()
    u1 = await ensure_guest(GID_A, "1.2.3.4")
    u2 = await ensure_guest(GID_B, "1.2.3.4")
    # 게스트마다 키를 만들지 않는다 — 공유 게스트 프로젝트/키 1개 (테넌트 생성은 1회)
    assert u1.project_id == u2.project_id == "prjG" and u1.api_key == "vgk_guest"
    assert u1.plan == "guest" and u1.email == guest_email(GID_A)
    assert respx.calls.call_count and respx.post("http://cp.test/admin/projects").call_count == 1


@respx.mock
async def test_ensure_guest_rejects_bad_or_claimed(monkeypatch):
    from fastapi import HTTPException

    _cfg(monkeypatch)
    _reset_guest_state()
    _mock_cp()
    with pytest.raises(HTTPException):          # 형식 불량 id
        await ensure_guest("<script>", None)
    monkeypatch.setattr(settings, "feature_guest", False)
    with pytest.raises(HTTPException):          # 기능 꺼짐 → 401
        await ensure_guest(GID_A, None)
    monkeypatch.setattr(settings, "feature_guest", True)
    await ensure_guest(GID_A, None)
    with SessionLocal() as db:                   # 가입으로 이어진(claimed) 세션은 재사용 불가
        db.get(GuestSession, GID_A).claimed_by = "real@u.com"
        db.commit()
    with pytest.raises(HTTPException):
        await ensure_guest(GID_A, None)


@respx.mock
def test_guest_chat_stream_and_lifetime_cap(monkeypatch):
    """게스트가 X-Guest-Id만으로 채팅하고, 평생 캡 도달 시 quota 이벤트로 가입 유도."""
    _cfg(monkeypatch)
    _reset_guest_state()
    _mock_cp()
    monkeypatch.setenv("GUEST_TURNS_MAX", "1")
    sse = b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n'
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse))
    hdr = {"X-Service-Token": "dev-service-token", "X-Guest-Id": GID_A, "X-Guest-Ip": "9.9.9.9"}

    r = client.post("/chat/stream", headers=hdr, json={"messages": [{"role": "user", "content": "삼성전자 어때"}]})
    assert r.status_code == 200 and '"token"' in r.text
    # 대화는 게스트 소유로 남는다 (GUEST-3 claim의 대상)
    convs = client.get("/conversations", headers=hdr).json()["conversations"]
    assert len(convs) == 1

    r2 = client.post("/chat/stream", headers=hdr, json={"messages": [{"role": "user", "content": "더"}]})
    assert '"quota"' in r2.text and "가입하면" in r2.text and '"token"' not in r2.text


@respx.mock
def test_guest_ip_cap_across_sessions(monkeypatch):
    """쿠키를 지워 새 세션을 파도 같은 IP면 하루 캡에 걸린다 (어뷰즈 백스톱)."""
    _cfg(monkeypatch)
    _reset_guest_state()
    _mock_cp()
    monkeypatch.setenv("GUEST_TURNS_MAX", "10")
    monkeypatch.setattr(settings, "guest_turns_per_ip_day", 1)
    sse = b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n'
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse))
    base = {"X-Service-Token": "dev-service-token", "X-Guest-Ip": "8.8.8.8"}

    r1 = client.post("/chat/stream", headers={**base, "X-Guest-Id": GID_A},
                     json={"messages": [{"role": "user", "content": "q1"}]})
    assert '"token"' in r1.text
    r2 = client.post("/chat/stream", headers={**base, "X-Guest-Id": GID_B},   # 새 쿠키, 같은 IP
                     json={"messages": [{"role": "user", "content": "q2"}]})
    assert '"quota"' in r2.text and '"token"' not in r2.text


def test_guest_requires_feature_flag(monkeypatch):
    monkeypatch.setattr(settings, "feature_guest", False)
    r = client.get("/users/me", headers={"X-Service-Token": "dev-service-token", "X-Guest-Id": GID_A})
    assert r.status_code == 401


@respx.mock
def test_claim_guest_moves_conversations(monkeypatch):
    """GUEST-3: 가입 직후 게스트 대화가 새 계정으로 넘어가고, 세션은 claimed로 잠긴다."""
    _cfg(monkeypatch)
    _reset_guest_state()
    _mock_cp()
    monkeypatch.setenv("GUEST_TURNS_MAX", "5")
    sse = b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n'
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse))
    ghdr = {"X-Service-Token": "dev-service-token", "X-Guest-Id": GID_A}
    client.post("/chat/stream", headers=ghdr, json={"messages": [{"role": "user", "content": "체험 질문"}]})
    assert len(client.get("/conversations", headers=ghdr).json()["conversations"]) == 1

    # 새(기존) 계정 — 프로비저닝 왕복 없이 직접 심는다
    with SessionLocal() as db:
        db.merge(User(email="joined@u.com", project_id="p", api_key="k"))
        db.commit()
    uhdr = {"X-Service-Token": "dev-service-token", "X-User-Email": "joined@u.com"}
    r = client.post("/users/claim-guest", headers={**uhdr, "X-Guest-Id": GID_A})
    assert r.status_code == 200 and r.json()["claimed"] is True and r.json()["conversations"] == 1
    # 대화가 새 계정 소유로 — "다시 원래 하던거부터"
    assert len(client.get("/conversations", headers=uhdr).json()["conversations"]) == 1
    # 게스트 User 행은 삭제, 세션은 잠김 → 같은 쿠키 재사용 401
    with SessionLocal() as db:
        assert db.get(User, guest_email(GID_A)) is None
        assert db.get(GuestSession, GID_A).claimed_by == "joined@u.com"
    r2 = client.post("/chat/stream", headers=ghdr, json={"messages": [{"role": "user", "content": "또"}]})
    assert r2.status_code == 401
    # 멱등: 재호출 no-op / 다른 계정의 가로채기 409
    assert client.post("/users/claim-guest", headers={**uhdr, "X-Guest-Id": GID_A}).json()["claimed"] is True
    with SessionLocal() as db:
        db.merge(User(email="thief@u.com", project_id="p", api_key="k"))
        db.commit()
    r3 = client.post("/users/claim-guest",
                     headers={"X-Service-Token": "dev-service-token", "X-User-Email": "thief@u.com",
                              "X-Guest-Id": GID_A})
    assert r3.status_code == 409
