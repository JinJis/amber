"""M-SHARE / SH-1 — share links: immutable snapshots of an artifact/verdict/note + per-SNS URLs.

A share is an explicit act: the payload is SNAPSHOTTED (a share never silently changes; the
public page shows its as_of), the token is unguessable and revocable, and the response carries
ready-made intent URLs for X/Threads/Telegram (KakaoTalk = link copy; no SDK v1) so the client
shares to any platform in one tap. QT-2 gate: a payload whose audit reports unsupported numbers
is refused — nothing leaves the app below the trust floor (PUBLISH_SPEC §3/§5).
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import ShareLink, User

router = APIRouter(tags=["Shares"], dependencies=[Depends(require_service)])

_KINDS = {"artifact", "verdict", "quote", "answer"}


class ShareIn(BaseModel):
    # `answer` shares a WHOLE chat answer (prose + inline figures + citations + audit) — the payload
    # is a pure content snapshot with NO user identity (no email/conversation id), so the public
    # page shows the research, never the researcher.
    kind: str = Field(pattern="^(artifact|verdict|quote|answer)$")
    title: str = Field(min_length=1, max_length=160)
    payload: dict                      # the snapshot (artifact JSON / note blocks / quote card / answer)
    audit: dict | None = None          # QT-2 result from the turn ({checked, unsupported: []})
    image_path: str | None = None      # client-rendered OG PNG (uploaded separately, optional v1)


class ShareImageIn(BaseModel):
    """SH-2b: the client-rendered OG card PNG as a base64 data URL (or bare base64)."""
    data_url: str = Field(..., description="data:image/png;base64,… or bare base64")


def _decode_png(data_url: str) -> bytes:
    b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "이미지 데이터가 올바르지 않습니다.")
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "PNG 이미지만 허용됩니다.")
    if len(raw) > 3_000_000:  # a card PNG is ~50-300KB; cap abuse
        raise HTTPException(413, "이미지가 너무 큽니다 (최대 3MB).")
    return raw


def _share_urls(token: str, title: str) -> dict:
    """Ready-made SNS intent links — the public page URL rides every platform."""
    url = f"{settings.public_base_url.rstrip('/')}/s/{token}"
    text = quote(f"{title} — 출처·기준일 포함 · finnote")
    u = quote(url)
    return {
        "page": url,
        "x": f"https://twitter.com/intent/tweet?text={text}&url={u}",
        "threads": f"https://www.threads.net/intent/post?text={text}%0A{u}",
        "telegram": f"https://t.me/share/url?url={u}&text={text}",
        "kakao": url,  # KakaoTalk: link copy v1 (JS SDK 통합은 SH-2 후속)
    }


def _out(s: ShareLink) -> dict:
    return {"token": s.token, "kind": s.kind, "title": s.title,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "revoked": s.revoked, "views": int(s.views or 0),
            "share_urls": _share_urls(s.token, s.title)}


@router.post("/shares", summary="공유 만들기 — 스냅샷 + 공개 링크 + SNS 인텐트 URL")
async def create_share(body: ShareIn, user: User = Depends(current_user)) -> dict:
    # QT-2 gate: unsupported numbers never leave the app (the trust floor for the whole brand)
    if body.audit and body.audit.get("unsupported"):
        raise HTTPException(422, "이 자료에는 원본 데이터와 대조되지 않은 수치가 있어 공유할 수 없습니다: "
                                 + ", ".join(map(str, body.audit["unsupported"][:5])))
    with SessionLocal() as db:
        # COUNT, not fetch-all (IMP-6). The check-then-insert isn't strictly atomic, but the cap is
        # a soft abuse guard (not a billing invariant) — an off-by-one race is acceptable.
        from sqlalchemy import func as _f
        active = db.execute(select(_f.count()).select_from(ShareLink).where(
            ShareLink.user_email == user.email, ShareLink.revoked.is_(False))).scalar() or 0
        if active >= settings.shares_per_user_cap:
            raise HTTPException(429, "공유 한도에 도달했습니다 — 기존 공유를 해제한 뒤 다시 시도하세요.")
        s = ShareLink(token=secrets.token_urlsafe(24), user_email=user.email, kind=body.kind,
                      title=body.title, payload=json.dumps(body.payload, ensure_ascii=False),
                      image_path=body.image_path,
                      expires_at=datetime.utcnow() + timedelta(days=settings.share_ttl_days))
        db.add(s)
        db.commit()
    # ME-6: ensure the sharer's referral code HERE (once, on the authenticated create) so the public
    # read path can look it up read-only — a public share view must not perform a write.
    from studioapi.referrals import ensure_referral_code
    ensure_referral_code(user.email)
    return _out(s)


@router.get("/shares", summary="내 공유 목록")
async def list_shares(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(ShareLink).where(ShareLink.user_email == user.email)
                          .order_by(ShareLink.created_at.desc())).scalars().all()
        return {"shares": [_out(s) for s in rows]}


@router.delete("/shares/{token}", summary="공유 해제 (공개 페이지 410)")
async def revoke_share(token: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        s = db.get(ShareLink, token)
        if s is None or s.user_email != user.email:
            raise HTTPException(404, "share not found")
        s.revoked = True
        db.commit()
        return {"revoked": token}


@router.put("/shares/{token}/image", summary="SH-2b: OG 카드 이미지 첨부 (게시자만)")
async def set_share_image(token: str, body: ShareImageIn, user: User = Depends(current_user)) -> dict:
    raw = _decode_png(body.data_url)  # validate before touching the DB
    with SessionLocal() as db:
        s = db.get(ShareLink, token)
        if s is None or s.user_email != user.email:
            raise HTTPException(404, "share not found")
        s.og_image = base64.b64encode(raw).decode("ascii")
        db.commit()
        return {"token": token, "image_url": f"/shares/{token}/image", "bytes": len(raw)}


@router.get("/shares/{token}/image", summary="공개 OG 카드 이미지 (PNG)")
async def get_share_image(token: str) -> Response:
    """Public: the baked card PNG for og:image. 404 when absent/revoked/expired (never a broken image)."""
    with SessionLocal() as db:
        s = db.get(ShareLink, token)
    if s is None or s.revoked or not s.og_image:
        raise HTTPException(404, "no image")
    if s.expires_at and s.expires_at < datetime.utcnow():
        raise HTTPException(410, "expired")
    try:
        raw = base64.b64decode(s.og_image)
    except (binascii.Error, ValueError):
        raise HTTPException(404, "no image")
    return Response(content=raw, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@router.post("/shares/{token}/view", summary="V-4: 공개 페이지 뷰 비콘 (+1, best-effort)")
async def count_view(token: str) -> dict:
    """공개 페이지의 sendBeacon이 호출 — 서비스 토큰만(뷰어는 비로그인). revoked/만료엔 안 셈."""
    with SessionLocal() as db:
        s = db.get(ShareLink, token)
        if s is None or s.revoked or (s.expires_at and s.expires_at < datetime.utcnow()):
            return {"ok": False}
        s.views = int(s.views or 0) + 1
        db.commit()
        return {"ok": True, "views": s.views}


@router.get("/shares/{token}", summary="공개 읽기 — 스냅샷 페이로드 (유저 인증 불필요)")
async def read_share(token: str) -> dict:
    """The web /s/{token} page calls this server-side with the service token only — no user."""
    with SessionLocal() as db:
        s = db.get(ShareLink, token)
    if s is None:
        raise HTTPException(404, "share not found")
    if s.revoked:
        raise HTTPException(410, "이 공유는 게시자가 해제했습니다.")
    if s.expires_at and s.expires_at < datetime.utcnow():
        raise HTTPException(410, "이 공유는 만료되었습니다.")
    # GUEST-4/REF-1: 공유자의 추천 코드 — 공개 페이지 CTA·이어 묻기 칩이 ?ref=로 달고 가면
    # 이 링크로 유입된 가입이 공유자에게 귀속된다. ME-6: READ-ONLY 조회 — 발급은 create_share에서
    # 이미 했으므로 이 고트래픽 공개 읽기는 쓰기를 하지 않는다(read replica 가능).
    from studioapi.referrals import referral_code_of
    ref_code = referral_code_of(s.user_email)
    return {"token": s.token, "kind": s.kind, "title": s.title,
            "payload": json.loads(s.payload), "image_path": s.image_path,
            "has_image": bool(s.og_image), "views": int(s.views or 0),
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "referral_code": ref_code,
            "share_urls": _share_urls(s.token, s.title)}
