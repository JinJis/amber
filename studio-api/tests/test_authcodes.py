"""AUTH-2 — 이메일 OTP 테스트 (메일러는 dev 모드: 발송 없이 로그만)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from studioapi.authcodes import _hash
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import EmailOtp

client = TestClient(app)
SVC = {"X-Service-Token": "dev-service-token"}


def setup_module(_module):
    init_db()


def _latest(email: str) -> EmailOtp:
    with SessionLocal() as db:
        return db.execute(select(EmailOtp).where(EmailOtp.email == email)
                          .order_by(EmailOtp.id.desc())).scalars().first()


def _reset(email: str) -> None:
    with SessionLocal() as db:
        db.query(EmailOtp).filter(EmailOtp.email == email).delete()
        db.commit()


def test_otp_roundtrip_and_consume():
    email = "otp1@u.com"
    _reset(email)
    r = client.post("/auth/otp/request", headers=SVC, json={"email": email})
    assert r.status_code == 200 and "6자리 코드" in r.json()["message"]
    row = _latest(email)
    assert row is not None and len(row.code_hash) == 64          # sha256만 저장 — 평문 없음

    # 코드 평문은 저장되지 않으므로 테스트는 해시를 맞춰 심는다
    with SessionLocal() as db:
        db.get(EmailOtp, row.id).code_hash = _hash("123456")
        db.commit()
    bad = client.post("/auth/otp/verify", headers=SVC, json={"email": email, "code": "000000"})
    assert bad.status_code == 401
    ok = client.post("/auth/otp/verify", headers=SVC, json={"email": email, "code": "123456"})
    assert ok.status_code == 200 and ok.json()["email"] == email
    # 1회 소비 — 같은 코드 재사용 불가
    again = client.post("/auth/otp/verify", headers=SVC, json={"email": email, "code": "123456"})
    assert again.status_code == 401


def test_otp_guards():
    # 형식·일회용 도메인·서비스 토큰
    assert client.post("/auth/otp/request", headers=SVC, json={"email": "notanemail"}).status_code == 422
    assert client.post("/auth/otp/request", headers=SVC, json={"email": "x@mailinator.com"}).status_code == 422
    assert client.post("/auth/otp/request", json={"email": "a@b.com"}).status_code == 401  # no service token

    # 발송 스로틀: 이메일당 시간당 3회
    email = "otp2@u.com"
    _reset(email)
    for _ in range(3):
        assert client.post("/auth/otp/request", headers=SVC, json={"email": email}).status_code == 200
    assert client.post("/auth/otp/request", headers=SVC, json={"email": email}).status_code == 429

    # 만료 코드 거부
    email3 = "otp3@u.com"
    _reset(email3)
    client.post("/auth/otp/request", headers=SVC, json={"email": email3})
    with SessionLocal() as db:
        row = db.execute(select(EmailOtp).where(EmailOtp.email == email3)).scalars().first()
        row.expires_at = datetime.utcnow() - timedelta(minutes=1)
        row.code_hash = _hash("222222")
        db.commit()
    assert client.post("/auth/otp/verify", headers=SVC,
                       json={"email": email3, "code": "222222"}).status_code == 401

    # 시도 5회 초과 → 429
    email4 = "otp4@u.com"
    _reset(email4)
    client.post("/auth/otp/request", headers=SVC, json={"email": email4})
    with SessionLocal() as db:
        row = db.execute(select(EmailOtp).where(EmailOtp.email == email4)).scalars().first()
        row.code_hash = _hash("333333")
        db.commit()
    for _ in range(5):
        client.post("/auth/otp/verify", headers=SVC, json={"email": email4, "code": "999999"})
    assert client.post("/auth/otp/verify", headers=SVC,
                       json={"email": email4, "code": "333333"}).status_code == 429
