"""AUTH-2 — 이메일 6자리 OTP 로그인 (매직링크 아님).

이유: 카톡/인스타 인앱 브라우저에서 매직링크는 **다른 브라우저**로 열려 세션과 대기 중인
질문(/?q=)이 유실된다. OTP는 같은 탭에서 완결되고 NextAuth adapter도 필요 없다(web의
Credentials 프로바이더가 verify를 이 API로 프록시).

보안: 코드는 sha256만 저장 · 10분 만료 · 시도 5회 · 발송 스로틀(이메일 3/시간, IP 10/시간)
· 일회용 이메일 도메인 차단. 라우터는 service 토큰만 요구(아직 유저가 없는 단계).
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from studioapi.db import SessionLocal
from studioapi.deps import require_service
from studioapi.guest import ip_hash
from studioapi.mailer import send_otp
from studioapi.models import EmailOtp

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_service)])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 대표적 일회용 도메인 — 레퍼럴 어뷰즈(가짜 가입) 1차 차단. 완전한 목록이 아니라 마찰 장치.
_DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com", "temp-mail.org",
    "yopmail.com", "sharklasers.com", "trashmail.com", "getnada.com", "dispostable.com",
}
_TTL_MINUTES = 10
_MAX_ATTEMPTS = 5
_PER_EMAIL_HOURLY = 3
_PER_IP_HOURLY = 10


class OtpRequestIn(BaseModel):
    email: str


class OtpVerifyIn(BaseModel):
    email: str
    code: str


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/auth/otp/request", summary="AUTH-2: 로그인 코드 발송")
async def otp_request(body: OtpRequestIn,
                      x_guest_ip: str | None = Header(default=None, alias="X-Guest-Ip")) -> dict:
    email = _norm(body.email)
    if not _EMAIL_RE.match(email):
        raise HTTPException(422, "이메일 주소를 확인해 주세요.")
    if email.split("@", 1)[1] in _DISPOSABLE:
        raise HTTPException(422, "일회용 이메일로는 가입할 수 없어요.")
    iph = ip_hash(x_guest_ip)
    hour_ago = datetime.utcnow() - timedelta(hours=1)
    with SessionLocal() as db:
        sent_email = db.execute(select(func.count()).select_from(EmailOtp).where(
            EmailOtp.email == email, EmailOtp.created_at >= hour_ago)).scalar() or 0
        if sent_email >= _PER_EMAIL_HOURLY:
            raise HTTPException(429, "코드를 너무 자주 요청했어요. 잠시 후 다시 시도해 주세요.")
        if iph:
            sent_ip = db.execute(select(func.count()).select_from(EmailOtp).where(
                EmailOtp.ip_hash == iph, EmailOtp.created_at >= hour_ago)).scalar() or 0
            if sent_ip >= _PER_IP_HOURLY:
                raise HTTPException(429, "코드를 너무 자주 요청했어요. 잠시 후 다시 시도해 주세요.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        db.add(EmailOtp(email=email, code_hash=_hash(code),
                        expires_at=datetime.utcnow() + timedelta(minutes=_TTL_MINUTES),
                        ip_hash=iph))
        db.commit()
    await send_otp(email, code)
    return {"sent": True, "message": "이메일로 6자리 코드를 보냈어요. 10분 안에 입력해 주세요."}


@router.post("/auth/otp/verify", summary="AUTH-2: 로그인 코드 검증 (web Credentials가 호출)")
async def otp_verify(body: OtpVerifyIn) -> dict:
    email = _norm(body.email)
    code = (body.code or "").strip()
    if not (_EMAIL_RE.match(email) and re.fullmatch(r"\d{6}", code)):
        raise HTTPException(401, "코드를 확인해 주세요.")
    now = datetime.utcnow()
    with SessionLocal() as db:
        row = db.execute(select(EmailOtp).where(
            EmailOtp.email == email, EmailOtp.consumed.is_(False), EmailOtp.expires_at >= now,
        ).order_by(EmailOtp.id.desc())).scalars().first()
        if row is None:
            raise HTTPException(401, "코드가 만료됐어요. 다시 요청해 주세요.")
        if row.attempts >= _MAX_ATTEMPTS:
            raise HTTPException(429, "시도 횟수를 넘겼어요. 코드를 다시 요청해 주세요.")
        row.attempts += 1
        if row.code_hash != _hash(code):
            db.commit()
            raise HTTPException(401, "코드가 맞지 않아요. 다시 확인해 주세요.")
        row.consumed = True
        db.commit()
    return {"verified": True, "email": email}
