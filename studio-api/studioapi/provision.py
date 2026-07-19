"""Provision a platform tenant/project/key for a Google-authenticated user.

On first login we create a tenant → project → API key via the control-plane admin
API and auto-activate the default connectors, then cache it on the User row. The
key is held server-side and never exposed to the browser.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging

import httpx

from studioapi.config import DEFAULT_CONNECTORS, settings
from studioapi.db import SessionLocal
from studioapi.models import ServiceState, User

log = logging.getLogger("studioapi.provision")


async def _admin(method: str, path: str, json: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.request(
            method, f"{settings.control_plane_url}{path}", json=json,
            headers={"X-Admin-Token": settings.admin_token},
        )
        resp.raise_for_status()
        return resp.json()


async def _activate_defaults(project_id: str, connectors: list[str] | None = None) -> None:
    """Activate connectors on a project (idempotent — the control-plane no-ops an already-active one).
    Defaults to DEFAULT_CONNECTORS (the free set); the system feed project passes the full set so
    background feeds can reach premium sources (fmp earnings/estimates, kis flows)."""
    for connector_id in (connectors if connectors is not None else DEFAULT_CONNECTORS):
        try:
            await _admin("POST", f"/admin/projects/{project_id}/activations", {"connector_id": connector_id})
        except Exception:  # noqa: BLE001 — best-effort: already active / connector absent / mocked-off in tests
            pass  # the rest still activate; entitlement is never worth failing a request over


# ME-5: a dedicated platform tenant/project/key for background/system feed generation. Otherwise the
# news_feed + onboarding refreshers meter against `_any_api_key` — an ARBITRARY real user's key — so
# the platform's background work is billed to them and the feed dies the moment that user is deleted or
# their key revoked. Lazy-provisioned once, cached in ServiceState (mirrors guest.py). Reserved key.
_SYSTEM_STATE_KEY = "system_project"
_system_lock = asyncio.Lock()


def system_api_key_cached() -> str | None:
    """Read the dedicated system tenant key from ServiceState WITHOUT any network call (returns None if
    it hasn't been provisioned yet — callers fall back to `_any_api_key`). Safe on the request path."""
    with SessionLocal() as db:
        row = db.get(ServiceState, _SYSTEM_STATE_KEY)
    if not row:
        return None
    try:
        return _json.loads(row.value).get("api_key")
    except (TypeError, ValueError):
        return None


_system_premium_done = False


async def _ensure_system_premium() -> None:
    """Backfill: a system project provisioned BEFORE premium feed connectors joined the set only has
    the free set activated — so 어닝 레이더(fmp)·한국 수급(kis) never generate. Re-activate the
    premium connectors once per process (idempotent — the control-plane no-ops already-active ones).
    Self-heals every existing deployment on its next restart, no manual admin step."""
    global _system_premium_done
    if _system_premium_done:
        return
    row = None
    with SessionLocal() as db:
        row = db.get(ServiceState, _SYSTEM_STATE_KEY)
    if row is None:
        return
    try:
        pid = _json.loads(row.value).get("project_id")
    except (TypeError, ValueError):
        pid = None
    if not pid:
        return
    from studioapi.plans import PREMIUM_CONNECTORS
    await _activate_defaults(pid, list(PREMIUM_CONNECTORS))
    _system_premium_done = True


async def ensure_system_project() -> str | None:
    """Provision (once) the dedicated system tenant/project/key for background feeds and cache it in
    ServiceState. Best-effort: if the control-plane is unreachable (boot-ordering) it returns None and
    the caller degrades to `_any_api_key` until a later attempt succeeds. Idempotent via the KV cache."""
    cached = system_api_key_cached()
    if cached:
        await _ensure_system_premium()   # backfill premium for projects provisioned before it existed
        return cached
    async with _system_lock:
        cached = system_api_key_cached()  # double-check under the lock
        if cached:
            return cached
        try:
            tenant = await _admin("POST", "/admin/tenants", {"name": "system"})
            project = await _admin("POST", f"/admin/tenants/{tenant['id']}/projects", {"name": "system"})
            key = await _admin("POST", f"/admin/projects/{project['id']}/keys", {"name": "system"})
        except Exception as exc:  # noqa: BLE001 — control-plane not ready yet → degrade, retry next boot
            log.warning("system project provisioning deferred (control-plane not ready): %s", exc)
            return None
        # 시스템 피드 키는 유저 플랜이 아니라 플랫폼 — 프리미엄(fmp 어닝 캘린더/컨센서스, kis 수급)까지
        # 활성화해야 어닝 레이더·한국 수급 섹션이 실제로 생성된다(활성 안 하면 어닝 레이더가 0장).
        from studioapi.plans import FREE_CONNECTORS, PREMIUM_CONNECTORS
        await _activate_defaults(project["id"], list(FREE_CONNECTORS) + list(PREMIUM_CONNECTORS))
        global _system_premium_done
        _system_premium_done = True   # fresh project already has premium — skip the backfill path
        state = {"tenant_id": tenant["id"], "project_id": project["id"], "api_key": key["api_key"]}
        with SessionLocal() as db:
            db.merge(ServiceState(key=_SYSTEM_STATE_KEY, value=_json.dumps(state)))
            db.commit()
        log.info("system project provisioned: %s", project["id"])
        return key["api_key"]


async def ensure_user(email: str, name: str | None = None, image: str | None = None,
                      referral_code: str | None = None) -> User:
    with SessionLocal() as db:
        existing = db.get(User, email)
    if existing:
        # ME-2: reconcile the default connector set ONCE per user across the fleet. The gate is a
        # PERSISTENT version column, not a process-local set — a process-local set re-fires the whole
        # reconcile herd on EVERY replica after EVERY deploy/restart (each fresh process starts empty).
        # Bump settings.connectors_reconcile_ver to re-run it after the default set changes.
        if existing.connectors_reconciled_ver != settings.connectors_reconcile_ver:
            if settings.plan_enforce_connectors:
                # PLAN-4: 플랜 기준 reconcile — free 유저의 프리미엄 커넥터 회수 포함(1회, 로깅).
                from studioapi.plans import apply_plan
                await apply_plan(email, existing.plan or "free")
            else:
                await _activate_defaults(existing.project_id)  # backfill free-set connectors only
            # Persist the reconcile version (so later requests / other replicas skip) + backfill profile
            # from the provider if we never captured it (never overwrite a set value — the user may have
            # edited their display name). Stamped AFTER the reconcile so a crash retries, not skips.
            with SessionLocal() as db:
                u = db.get(User, email)
                if u is not None:
                    u.connectors_reconciled_ver = settings.connectors_reconcile_ver
                    if name and not u.name:
                        u.name = name[:120]
                    if image and not u.image:
                        u.image = image[:512]
                    db.commit()
                    existing = u
        return existing

    tenant = await _admin("POST", "/admin/tenants", {"name": email})
    project = await _admin("POST", f"/admin/tenants/{tenant['id']}/projects", {"name": "default"})
    key = await _admin("POST", f"/admin/projects/{project['id']}/keys", {"name": "web"})
    await _activate_defaults(project["id"])

    user = User(email=email, tenant_id=tenant["id"], project_id=project["id"], api_key=key["api_key"],
                name=(name or None) and name[:120], image=(image or None) and image[:512],
                # ME-2: freshly activated against the current default set — no reconcile needed later
                connectors_reconciled_ver=settings.connectors_reconcile_ver,
                # AUTH-4: 카카오 무이메일 센티널은 실주소가 아니다 — 메일 발송(OTP·던닝) 차단
                email_verified=not email.endswith("@noemail.local"))
    with SessionLocal() as db:
        db.merge(user)
        db.commit()
    if referral_code:  # REF-1: 가입 귀속 — 자기추천·미존재 코드는 내부에서 무시
        from studioapi.referrals import attribute_signup
        attribute_signup(email, referral_code)
    return user
