"""AUTH-4 — 소셜 identity 매핑 + 이메일 연결 승격(리네임) 테스트."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from studioapi.authcodes import _hash
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.identity import rename_user_email
from studioapi.main import app
from studioapi.models import Conversation, EmailOtp, TurnUsage, User, UserIdentity

client = TestClient(app)
SVC = {"X-Service-Token": "dev-service-token"}


def setup_module(_module):
    init_db()


def _mock_cp(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    respx.post("http://cp.test/admin/provision").mock(
        return_value=httpx.Response(200, json={"project_id": "prjI", "api_key": "vgk_i"}))
    respx.post("http://cp.test/admin/projects").mock(return_value=httpx.Response(200, json={"id": "prjI"}))
    respx.post("http://cp.test/admin/projects/prjI/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_i"}))
    respx.post("http://cp.test/admin/projects/prjI/activations").mock(return_value=httpx.Response(200, json={}))


@respx.mock
def test_identity_resolution_matrix(monkeypatch):
    _mock_cp(monkeypatch)
    # ① 이메일 있는 구글 → 그 이메일로 매핑 생성
    r = client.post("/auth/identity", headers=SVC, json={
        "provider": "google", "provider_account_id": "g-1", "email": "id1@u.com"})
    assert r.json()["email"] == "id1@u.com"
    # ② 같은 identity 재로그인 → 프로바이더가 다른 이메일을 줘도 매핑이 진실
    r2 = client.post("/auth/identity", headers=SVC, json={
        "provider": "google", "provider_account_id": "g-1", "email": "changed@u.com"})
    assert r2.json()["email"] == "id1@u.com"
    # ③ 이메일 없는 카카오 → 결정적 센티널 + email_verified=false
    r3 = client.post("/auth/identity", headers=SVC, json={
        "provider": "kakao", "provider_account_id": "k-77"})
    sentinel = r3.json()["email"]
    assert sentinel == "kakao_k-77@noemail.local"
    with SessionLocal() as db:
        assert db.get(User, sentinel).email_verified is False   # 메일러 게이트


@respx.mock
def test_link_email_renames_everywhere(monkeypatch):
    _mock_cp(monkeypatch)
    sentinel = client.post("/auth/identity", headers=SVC, json={
        "provider": "kakao", "provider_account_id": "k-88"}).json()["email"]
    # 센티널 계정에 데이터가 있는 상태에서 연결(리네임)
    with SessionLocal() as db:
        db.add(Conversation(user_email=sentinel, title="이어질 대화"))
        db.add(TurnUsage(user_email=sentinel, day="2026-07-11", month="2026-07"))
        db.commit()
    # OTP 심기 (평문 미저장 — 해시 주입)
    client.post("/auth/otp/request", headers=SVC, json={"email": "linked88@u.com"})
    with SessionLocal() as db:
        row = db.execute(select(EmailOtp).where(EmailOtp.email == "linked88@u.com")
                         .order_by(EmailOtp.id.desc())).scalars().first()
        row.code_hash = _hash("654321")
        db.commit()
    r = client.post("/auth/identity/link-email",
                    headers={**SVC, "X-User-Email": sentinel},
                    json={"email": "linked88@u.com", "code": "654321"})
    assert r.status_code == 200 and r.json()["email"] == "linked88@u.com"
    with SessionLocal() as db:
        assert db.get(User, sentinel) is None                    # 옛 PK 소멸
        u = db.get(User, "linked88@u.com")
        assert u is not None and u.email_verified is True        # 승격
        # user_email을 가진 테이블 전부 이관 (메타데이터 순회)
        assert db.query(Conversation).filter(Conversation.user_email == "linked88@u.com").count() == 1
        assert db.query(TurnUsage).filter(TurnUsage.user_email == "linked88@u.com").count() == 1
        # identity 재지정 → 다음 카카오 로그인은 새 이메일로 착지
        ident = db.execute(select(UserIdentity).where(
            UserIdentity.provider == "kakao",
            UserIdentity.provider_account_id == "k-88")).scalars().one()
        assert ident.user_email == "linked88@u.com"
    # 이미 연결된 계정이 또 연결 시도 → 409
    r2 = client.post("/auth/identity/link-email",
                     headers={**SVC, "X-User-Email": "linked88@u.com"},
                     json={"email": "other@u.com", "code": "000000"})
    assert r2.status_code == 409


def test_rename_refuses_collision():
    import pytest

    with SessionLocal() as db:
        db.merge(User(email="col_a@noemail.local", project_id="p", api_key="k"))
        db.merge(User(email="col_b@u.com", project_id="p", api_key="k"))
        db.commit()
    with pytest.raises(ValueError):
        rename_user_email("col_a@noemail.local", "col_b@u.com")   # 기존 계정과 병합 금지
