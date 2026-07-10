"""Provision a platform tenant/project/key for a Google-authenticated user.

On first login we create a tenant → project → API key via the control-plane admin
API and auto-activate the default connectors, then cache it on the User row. The
key is held server-side and never exposed to the browser.
"""

from __future__ import annotations

import httpx

from studioapi.config import DEFAULT_CONNECTORS, settings
from studioapi.db import SessionLocal
from studioapi.models import User


async def _admin(method: str, path: str, json: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.request(
            method, f"{settings.control_plane_url}{path}", json=json,
            headers={"X-Admin-Token": settings.admin_token},
        )
        resp.raise_for_status()
        return resp.json()


async def _activate_defaults(project_id: str) -> None:
    """Activate every DEFAULT_CONNECTORS on a project (idempotent — the control-plane no-ops an
    already-active one). Used at provision time AND to backfill when the default set grows."""
    for connector_id in DEFAULT_CONNECTORS:
        try:
            await _admin("POST", f"/admin/projects/{project_id}/activations", {"connector_id": connector_id})
        except Exception:  # noqa: BLE001 — best-effort: already active / connector absent / mocked-off in tests
            pass  # the rest still activate; entitlement is never worth failing a request over


# Emails reconciled this process lifetime — so an existing user (provisioned before the default set
# grew) gets the new connectors activated ONCE on their next request after a deploy, without a
# per-request admin round-trip or a schema migration.
_reconciled: set[str] = set()


async def ensure_user(email: str, name: str | None = None, image: str | None = None,
                      referral_code: str | None = None) -> User:
    with SessionLocal() as db:
        existing = db.get(User, email)
    if existing:
        if email not in _reconciled:
            if settings.plan_enforce_connectors:
                # PLAN-4: 플랜 기준 reconcile — free 유저의 프리미엄 커넥터 회수 포함(1회, 로깅).
                from studioapi.plans import apply_plan
                await apply_plan(email, existing.plan or "free")
            else:
                await _activate_defaults(existing.project_id)  # backfill free-set connectors only
            # backfill profile from the provider if we never captured it (never overwrite a set value —
            # the user may have edited their display name).
            if name and not existing.name or image and not existing.image:
                with SessionLocal() as db:
                    u = db.get(User, email)
                    if u is not None:
                        if name and not u.name:
                            u.name = name[:120]
                        if image and not u.image:
                            u.image = image[:512]
                        db.commit()
                        existing = u
            _reconciled.add(email)
        return existing

    tenant = await _admin("POST", "/admin/tenants", {"name": email})
    project = await _admin("POST", f"/admin/tenants/{tenant['id']}/projects", {"name": "default"})
    key = await _admin("POST", f"/admin/projects/{project['id']}/keys", {"name": "web"})
    await _activate_defaults(project["id"])
    _reconciled.add(email)

    user = User(email=email, tenant_id=tenant["id"], project_id=project["id"], api_key=key["api_key"],
                name=(name or None) and name[:120], image=(image or None) and image[:512],
                # AUTH-4: 카카오 무이메일 센티널은 실주소가 아니다 — 메일 발송(OTP·던닝) 차단
                email_verified=not email.endswith("@noemail.local"))
    with SessionLocal() as db:
        db.merge(user)
        db.commit()
    if referral_code:  # REF-1: 가입 귀속 — 자기추천·미존재 코드는 내부에서 무시
        from studioapi.referrals import attribute_signup
        attribute_signup(email, referral_code)
    return user
