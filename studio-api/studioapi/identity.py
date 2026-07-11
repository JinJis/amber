"""AUTH-4 — 소셜 계정 매핑 + 이메일 연결 승격.

두 가지를 한다:
1. ``POST /auth/identity`` — 로그인마다 web(jwt 콜백)이 호출: (provider, account_id)로
   캐노니컬 이메일을 해석한다. 프로바이더가 이메일을 안 주면(카카오 비즈앱 심사 전) 결정적
   센티널 ``{provider}_{id}@noemail.local``을 쓰고, 프로바이더 이메일이 나중에 바뀌어도
   계정은 갈라지지 않는다(매핑이 진실).
2. ``POST /auth/identity/link-email`` — 센티널 계정에 실제 이메일을 연결: OTP 검증 →
   **단일 트랜잭션 리네임**(users PK + user_email을 가진 모든 테이블을 메타데이터 기반으로
   UPDATE — 새 테이블이 생겨도 자동 커버) → 매핑 재지정 → email_verified 승격.
   이후 로그인은 새 이메일로 착지; 웹은 세션 재발급(재로그인)을 안내한다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from studioapi.db import Base, SessionLocal, engine
from studioapi.deps import current_user, require_service
from studioapi.models import User, UserIdentity
from studioapi.provision import ensure_user

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_service)])

SENTINEL_DOMAIN = "@noemail.local"


def sentinel_email(provider: str, account_id: str) -> str:
    return f"{provider}_{account_id}{SENTINEL_DOMAIN}"


def rename_user_email(old: str, new: str) -> int:
    """유저 이메일(=PK) 리네임 — user_email 컬럼을 가진 **모든** 테이블 + users.referred_by +
    user_identities를 한 트랜잭션에서 UPDATE. 메타데이터 순회라 새 테이블이 생겨도 자동 커버.
    반환: 갱신한 테이블 수. new가 이미 존재하면 ValueError(계정 병합은 별개 문제)."""
    with SessionLocal() as db:
        if db.get(User, new) is not None:
            raise ValueError("이미 사용 중인 이메일이에요.")
        if db.get(User, old) is None:
            raise ValueError("연결할 계정을 찾지 못했어요.")
    touched = 0
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if "user_email" in table.c:
                conn.execute(table.update().where(table.c.user_email == old)
                             .values(user_email=new))
                touched += 1
        users = Base.metadata.tables["users"]
        conn.execute(users.update().where(users.c.email == old)
                     .values(email=new, email_verified=True))
        conn.execute(users.update().where(users.c.referred_by == old)
                     .values(referred_by=new))
        guest = Base.metadata.tables.get("guest_sessions")
        if guest is not None:
            conn.execute(guest.update().where(guest.c.claimed_by == old)
                         .values(claimed_by=new))
    logger.info("identity: renamed %s → %s (%d tables)", old, new, touched)
    return touched


class IdentityIn(BaseModel):
    provider: str
    provider_account_id: str
    email: str | None = None
    name: str | None = None
    image: str | None = None


@router.post("/auth/identity", summary="AUTH-4: 소셜 로그인 → 캐노니컬 이메일 해석")
async def resolve_identity(body: IdentityIn) -> dict:
    provider = (body.provider or "").strip().lower()
    account_id = (body.provider_account_id or "").strip()
    if not provider or not account_id:
        raise HTTPException(422, "provider/account id가 필요해요.")
    with SessionLocal() as db:
        row = db.execute(select(UserIdentity).where(
            UserIdentity.provider == provider,
            UserIdentity.provider_account_id == account_id)).scalars().first()
        if row is not None:
            return {"email": row.user_email}   # 매핑이 진실 — 프로바이더 이메일 변경 무시
    email = (body.email or "").strip().lower() or sentinel_email(provider, account_id)
    await ensure_user(email, name=body.name, image=body.image)
    with SessionLocal() as db:
        db.add(UserIdentity(provider=provider, provider_account_id=account_id, user_email=email))
        db.commit()
    return {"email": email}


class LinkEmailIn(BaseModel):
    email: str
    code: str


@router.post("/auth/identity/link-email", summary="AUTH-4: 센티널 계정에 실제 이메일 연결 (OTP)")
async def link_email(body: LinkEmailIn, user: User = Depends(current_user)) -> dict:
    if not user.email.endswith(SENTINEL_DOMAIN):
        raise HTTPException(409, "이미 이메일이 연결된 계정이에요.")
    # OTP 검증 재사용 — 실패 시 그대로 401/429가 올라간다
    from studioapi.authcodes import OtpVerifyIn, otp_verify
    verified = await otp_verify(OtpVerifyIn(email=body.email, code=body.code))
    new_email = verified["email"]
    try:
        rename_user_email(user.email, new_email)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "email": new_email,
            "message": "이메일이 연결됐어요. 보안을 위해 다시 로그인해 주세요."}
