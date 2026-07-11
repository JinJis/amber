"""Dependencies: trust the first-party web BFF (service token) + resolve the user."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException

from studioapi.config import settings
from studioapi.models import User
from studioapi.provision import ensure_user


async def require_service(x_service_token: Annotated[str | None, Header(alias="X-Service-Token")] = None) -> None:
    if not x_service_token or x_service_token != settings.service_token:
        raise HTTPException(401, "Invalid service token.")


async def require_admin(x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None) -> None:
    """BILL-5: 운영(admin 패널) 전용 액션 — control-plane과 같은 ADMIN_TOKEN을 공유한다."""
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "Invalid admin token.")


async def current_user(
    x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
    x_user_name: Annotated[str | None, Header(alias="X-User-Name")] = None,
    x_user_image: Annotated[str | None, Header(alias="X-User-Image")] = None,
    x_referral_code: Annotated[str | None, Header(alias="X-Referral-Code")] = None,
) -> User:
    if not x_user_email:
        raise HTTPException(401, "Missing authenticated user.")
    # name/image come from the OAuth session (via the web BFF), URI-encoded — seeded on first login.
    name = unquote(x_user_name) if x_user_name else None
    image = unquote(x_user_image) if x_user_image else None
    # REF-1: vg_ref 쿠키의 추천 코드 — 첫 프로비저닝(가입)에서만 귀속된다.
    return await ensure_user(x_user_email, name=name, image=image, referral_code=x_referral_code)


async def current_actor(
    x_user_email: Annotated[str | None, Header(alias="X-User-Email")] = None,
    x_user_name: Annotated[str | None, Header(alias="X-User-Name")] = None,
    x_user_image: Annotated[str | None, Header(alias="X-User-Image")] = None,
    x_guest_id: Annotated[str | None, Header(alias="X-Guest-Id")] = None,
    x_guest_ip: Annotated[str | None, Header(alias="X-Guest-Ip")] = None,
) -> User:
    """GUEST-1: 로그인 유저 **또는** 게스트(httpOnly 쿠키 id). 게스트는 채팅·대화 읽기 같은
    핵심 루프에만 마운트한다 — 피드 생성·워치리스트·공유 생성 등은 current_user 유지(쿼터
    우회·리소스 남용 차단). FEATURE_GUEST가 꺼져 있으면 게스트 경로는 401."""
    if x_user_email:
        return await current_user(x_user_email, x_user_name, x_user_image)
    if x_guest_id:
        from studioapi.guest import ensure_guest
        return await ensure_guest(x_guest_id, x_guest_ip)
    raise HTTPException(401, "Missing authenticated user.")


ServiceDep = Depends(require_service)
UserDep = Depends(current_user)
ActorDep = Depends(current_actor)
