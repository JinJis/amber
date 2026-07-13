"""REF-1(코어) — 추천 코드 발급 + 가입 귀속.

코드는 8자(대문자+숫자, 헷갈리는 문자 제외), 유저당 1개를 lazy 발급. 귀속은 가입 시
``vg_ref`` 쿠키(공유 페이지 ``?ref=`` — GUEST-4)에서 오는 ``X-Referral-Code``로 1회만;
자기추천·미존재 코드는 조용히 무시한다. 크레딧 원장·킥백·소급 입력(REF-2/3/4)은 BILL
트랙과 함께 온다 — referred_by 귀속만 먼저 깔아두면 그때의 정산 대상이 소급 가능하다.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from studioapi.db import SessionLocal
from studioapi.models import User

logger = logging.getLogger(__name__)

# 헷갈리는 문자(I·L·O·U·0·1) 제외 — 카톡으로 불러줘도 틀리지 않는 코드
_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def _gen() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def referral_code_of(email: str) -> str | None:
    """ME-6: READ-ONLY 추천 코드 조회 — 절대 쓰지 않는다. 공개 공유 읽기(고트래픽·비로그인)가
    매 뷰마다 코드를 lazy 발급(write-on-read)하면 read replica로 못 넘기고 write 부하가 는다.
    발급은 create_share(작성자 인증 요청)에서 1회 하고, 읽기는 이걸로 조회만 한다."""
    if not email or email.endswith("@guest.local"):
        return None
    with SessionLocal() as db:
        return db.scalar(select(User.referral_code).where(User.email == email))


def ensure_referral_code(email: str) -> str | None:
    """유저의 추천 코드 — 없으면 발급(유니크 충돌 시 재시도). 게스트/미존재 유저는 None."""
    if not email or email.endswith("@guest.local"):
        return None
    with SessionLocal() as db:
        u = db.get(User, email)
        if u is None:
            return None
        if u.referral_code:
            return u.referral_code
        for _ in range(5):
            u.referral_code = _gen()
            try:
                db.commit()
                return u.referral_code
            except IntegrityError:  # 유니크 충돌 — 다른 코드로 재시도
                db.rollback()
                u = db.get(User, email)
                if u is None:
                    return None
                if u.referral_code:
                    return u.referral_code
        logger.error("referral code generation kept colliding for %s", email)
        return None


def attribute_signup(user_email: str, code: str | None) -> bool:
    """추천 코드 귀속 — referred_by가 비어 있을 때 1회만. 자기추천·미존재 코드는 무시."""
    if not code:
        return False
    code = code.strip().upper()
    with SessionLocal() as db:
        u = db.get(User, user_email)
        if u is None or u.referred_by:
            return False
        referrer = db.execute(select(User).where(User.referral_code == code)).scalar_one_or_none()
        if referrer is None or referrer.email == user_email:
            return False
        u.referred_by = referrer.email
        db.commit()
        logger.info("referral: %s ← %s (%s)", user_email, referrer.email, code)
        return True
