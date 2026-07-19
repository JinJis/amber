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


async def _activate_defaults(project_id: str) -> None:
    """Activate the default (free-set) connectors on a USER project (idempotent — the control-plane
    no-ops an already-active one). The system feed project does NOT use this — it is marked internal
    (SYS-1) and entitled to the whole governed catalog by the gateway, no activation list to maintain."""
    for connector_id in DEFAULT_CONNECTORS:
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


_system_backfill_done = False


async def _mark_project_internal(project_id: str) -> None:
    """SYS-1: mark a control-plane project as INTERNAL. The gateway then entitles it to the whole
    governed catalog (skips the activation check) while still metering/rate-limiting/auditing it — so
    the platform's own feed pipelines 'just run' without carrying (and drifting out of sync with) a
    commercial activation list. This is what makes 어닝 레이더(fmp)·한국 수급(kis)·era-news(gdelt/nyt)
    reachable to the feed regardless of any user plan. Raises on failure (caller decides best-effort)."""
    await _admin("PATCH", f"/admin/projects/{project_id}", {"internal": True})


async def _backfill_system_internal() -> None:
    """Self-heal: a system project provisioned BEFORE the internal class existed only has the free
    activation set — so the premium/era sections never generate. Mark it internal once per process
    (idempotent). Every existing deployment fixes itself on its next restart, no manual admin step."""
    global _system_backfill_done
    if _system_backfill_done:
        return
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
    try:
        await _mark_project_internal(pid)
        _system_backfill_done = True
    except Exception as exc:  # noqa: BLE001 — control-plane not ready → retry on the next call/boot
        log.warning("system project: mark-internal deferred (control-plane not ready): %s", exc)


async def ensure_system_project() -> str | None:
    """Provision (once) the dedicated system tenant/project/key for background feeds and cache it in
    ServiceState. Best-effort: if the control-plane is unreachable (boot-ordering) it returns None and
    the caller degrades to `_any_api_key` until a later attempt succeeds. Idempotent via the KV cache."""
    global _system_backfill_done
    cached = system_api_key_cached()
    if cached:
        await _backfill_system_internal()   # SYS-1: mark pre-existing system projects internal
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
        # Cache FIRST so a later mark-internal failure can never cause a re-mint (tenant leak) next boot.
        state = {"tenant_id": tenant["id"], "project_id": project["id"], "api_key": key["api_key"]}
        with SessionLocal() as db:
            db.merge(ServiceState(key=_SYSTEM_STATE_KEY, value=_json.dumps(state)))
            db.commit()
        # SYS-1: the system feed project is infrastructure, not a commercial tenant — mark it internal so
        # the gateway entitles it to the whole governed catalog (no activation bookkeeping, new connectors
        # auto-available). Best-effort: the backfill path retries on the next boot if this fails now.
        try:
            await _mark_project_internal(project["id"])
            _system_backfill_done = True
        except Exception as exc:  # noqa: BLE001
            log.warning("system project: initial mark-internal deferred: %s", exc)
        log.info("system project provisioned (internal): %s", project["id"])
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
