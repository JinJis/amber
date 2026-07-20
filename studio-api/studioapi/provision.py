"""Provision a platform project/key for an authenticated user.

On first login we create a control-plane project → API key and activate the default connectors, then
cache the pair on the User row. The key is held server-side and never exposed to the browser.

**PROV-1 — why this file is written the way it is.** Provisioning is slow (network calls to the
control plane) and it is triggered by a *dependency* that runs on nearly every authenticated request
(`deps.current_user`). A brand-new user's first screen fires several requests at once, so the naive
shape — "read the row; if absent, mint; then write the row" — had every one of those requests pass
the existence check and mint its own project+key, leaving one referenced and the rest as orphans with
live credentials. The fix makes the database the arbiter at every contested step:

  * the ``users.email`` primary key decides **who provisions** (a claim row is inserted first, before
    any network call — the losers of that INSERT wait instead of minting),
  * a compare-and-set on the claim token decides **whose result is recorded** (so a provisioner that
    stalled and was taken over can never overwrite the newer winner), and
  * ``projects.owner_ref`` in the control plane decides **which project exists** (so even a retry or
    a takeover converges on the same account rather than creating another).

The invariant callers depend on: ``ensure_user`` returns either a FULLY provisioned user (project_id
and api_key both set) or raises. It never returns a half-built claim row — every downstream caller
puts ``user.api_key`` straight into a gateway request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from studioapi.config import DEFAULT_CONNECTORS, settings
from studioapi.db import SessionLocal
from studioapi.models import ServiceState, User

log = logging.getLogger("studioapi.provision")


async def _admin(method: str, path: str, json: dict | None = None, timeout: float | None = None) -> dict:
    async with httpx.AsyncClient(timeout=timeout or settings.http_timeout_seconds) as client:
        resp = await client.request(
            method, f"{settings.control_plane_url}{path}", json=json,
            headers={"X-Admin-Token": settings.admin_token},
        )
        resp.raise_for_status()
        return resp.json()


async def _provision_account(owner_ref: str, *, name: str | None = None, key_name: str = "web",
                             connectors: list[str] | None = None, plan: str | None = None,
                             internal: bool | None = None, fence: int | None = None) -> dict:
    """One idempotent control-plane call that yields a ready account: project (get-or-create by
    ``owner_ref``) + a live API key + the requested activations. Returns ``{project_id, api_key}``.

    Replacing the old create-project → create-key → activate×9 sequence with a single call is what
    removes the long window in which a caller held a half-built account and another caller, seeing no
    finished account, started building a second one. It is also why a retry is now safe: the same
    ``owner_ref`` resolves to the same project instead of minting another.

    Bounded by ``provision_call_timeout_seconds``, NOT the general 120s HTTP timeout — a human is
    waiting on their first screen, and the claim lease is sized against this bound.
    """
    body: dict = {"owner_ref": owner_ref, "name": name or owner_ref, "key_name": key_name,
                  "connectors": list(connectors or [])}
    if plan is not None:
        body["plan"] = plan
    if internal is not None:
        body["internal"] = internal
    if fence is not None:
        body["fence"] = fence
    return await _admin("POST", "/admin/provision", body,
                        timeout=settings.provision_call_timeout_seconds)


async def _activate_defaults(project_id: str) -> None:
    """Activate the default (free-set) connectors on an EXISTING user project — the ME-2 reconcile
    backfill only (fresh signups get their activations from `_provision_account` in one call).
    Idempotent; the control-plane no-ops an already-active connector."""
    for connector_id in DEFAULT_CONNECTORS:
        try:
            await _admin("POST", f"/admin/projects/{project_id}/activations", {"connector_id": connector_id})
        except Exception:  # noqa: BLE001 — best-effort: already active / connector absent / mocked-off in tests
            pass  # the rest still activate; entitlement is never worth failing a request over


# --- platform singletons (system feed + guest) ---------------------------------------------------
# ME-5: a dedicated platform project/key for background/system feed generation. Otherwise the
# news_feed + onboarding refreshers meter against `_any_api_key` — an ARBITRARY real user's key — so
# the platform's background work is billed to them and the feed dies the moment that user is deleted or
# their key revoked. Lazy-provisioned once, cached in ServiceState (mirrors guest.py). Reserved key.
_SYSTEM_STATE_KEY = "system_project"
_system_lock = asyncio.Lock()


def _state_get(state_key: str) -> dict | None:
    with SessionLocal() as db:
        row = db.get(ServiceState, state_key)
    if not row:
        return None
    try:
        value = _json.loads(row.value)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("api_key") else None


def _state_put(state_key: str, state: dict) -> None:
    with SessionLocal() as db:
        db.merge(ServiceState(key=state_key, value=_json.dumps(state)))
        db.commit()


def _singleton_lock_id(state_key: str) -> int:
    """A DISTINCT advisory-lock id per singleton. Sharing one id across the system and guest projects
    would make them block each other — and the loser waits on a ServiceState key the holder is never
    going to write, so it just times out."""
    digest = hashlib.sha256(f"studioapi.singleton:{state_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def _pg_singleton_lock(state_key: str):
    """Cross-replica, NON-blocking guard around provisioning one singleton. Yields True if acquired.

    A Postgres advisory lock is held by the CONNECTION that took it, so acquire and release must
    happen on the same one — taking it from a pooled session and releasing from another session
    unlocks nothing and strands the lock on a pooled connection, wedging every later attempt (the
    next caller can no longer acquire it and gives up waiting). Hence one explicit connection held
    for the whole critical section. Yields True on SQLite/other: single process there, where the
    caller's asyncio lock is already the complete story.
    """
    from studioapi.db import engine

    if engine.dialect.name != "postgresql":
        yield True
        return
    lock_id = _singleton_lock_id(state_key)
    with engine.connect() as conn:
        acquired = bool(conn.execute(select(func.pg_try_advisory_lock(lock_id))).scalar())
        conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(select(func.pg_advisory_unlock(lock_id)))
                conn.commit()


async def _await_state(state_key: str) -> dict | None:
    """Wait for another replica to publish a singleton's ServiceState row (bounded)."""
    deadline = asyncio.get_running_loop().time() + settings.provision_wait_seconds
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(settings.provision_poll_seconds)
        state = _state_get(state_key)
        if state:
            return state
    return None


async def _ensure_singleton(state_key: str, owner_ref: str, *, lock: asyncio.Lock, key_name: str,
                            connectors: list[str], plan: str | None = None,
                            internal: bool | None = None) -> dict | None:
    """Provision-once a PLATFORM-OWNED account (the system feed project, the shared guest project) and
    cache it in ServiceState. Returns ``{project_id, api_key}`` or None if the control plane is not
    reachable yet (boot ordering) — callers degrade rather than fail.

    Two callers must never provision the same singleton concurrently. `owner_ref` already guarantees
    they would converge on one *project*, but the second call ROTATES the key the first is about to
    cache — leaving the cached credential dead, and the background feeds silently failing. So the
    asyncio lock (same process) is paired with a Postgres advisory lock (other replicas), and a caller
    that loses the advisory lock waits for the winner's cache write instead of minting.
    """
    cached = _state_get(state_key)
    if cached:
        return cached
    async with lock:
        cached = _state_get(state_key)   # double-check under the lock
        if cached:
            return cached
        with _pg_singleton_lock(state_key) as acquired:
            if not acquired:
                return await _await_state(state_key)
            cached = _state_get(state_key)   # ...and once more now that we hold the cross-replica lock
            if cached:
                return cached
            try:
                acct = await _provision_account(owner_ref, key_name=key_name, connectors=connectors,
                                                plan=plan, internal=internal)
            except Exception as exc:  # noqa: BLE001 — control plane not ready → degrade, retry later
                log.warning("%s provisioning deferred (control-plane not ready): %s", state_key, exc)
                return None
            state = {"project_id": acct["project_id"], "api_key": acct["api_key"]}
            _state_put(state_key, state)
            log.info("%s provisioned: %s", state_key, state["project_id"])
            return state


def system_api_key_cached() -> str | None:
    """Read the dedicated system project key from ServiceState WITHOUT any network call (returns None if
    it hasn't been provisioned yet — callers fall back to `_any_api_key`). Safe on the request path."""
    state = _state_get(_SYSTEM_STATE_KEY)
    return state.get("api_key") if state else None


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
    state = _state_get(_SYSTEM_STATE_KEY)
    pid = (state or {}).get("project_id")
    if not pid:
        return
    try:
        await _mark_project_internal(pid)
        _system_backfill_done = True
    except Exception as exc:  # noqa: BLE001 — control-plane not ready → retry on the next call/boot
        log.warning("system project: mark-internal deferred (control-plane not ready): %s", exc)


async def ensure_system_project() -> str | None:
    """Provision (once) the dedicated system project/key for background feeds and cache it in
    ServiceState. Best-effort: if the control-plane is unreachable (boot-ordering) it returns None and
    the caller degrades to `_any_api_key` until a later attempt succeeds. Idempotent via the KV cache.

    SYS-1: it is provisioned `internal` and with NO activations — the gateway entitles an internal
    project to the whole governed catalog, so the feed pipelines don't carry (and drift out of sync
    with) a commercial activation list.
    """
    global _system_backfill_done
    cached = system_api_key_cached()
    if cached:
        await _backfill_system_internal()   # SYS-1: mark pre-existing system projects internal
        return cached
    state = await _ensure_singleton(_SYSTEM_STATE_KEY, "system", lock=_system_lock,
                                    key_name="system", connectors=[], internal=True)
    if state:
        _system_backfill_done = True   # provisioned internal in the same call — nothing to backfill
        return state.get("api_key")
    return None


# --- user provisioning ---------------------------------------------------------------------------
def _load_user(email: str) -> User | None:
    with SessionLocal() as db:
        return db.get(User, email)


def _is_provisioned(user: User | None) -> bool:
    return bool(user is not None and user.project_id and user.api_key)


def _fence_of(stamp: datetime) -> int:
    """The claim stamp as a monotonic integer, handed to the control plane so it can refuse a key
    rotation from a provisioner that has since been superseded (every takeover re-stamps later)."""
    return int(stamp.timestamp() * 1_000_000)


def _claim(email: str, token: str, name: str | None, image: str | None) -> datetime | None:
    """Stake this request's exclusive right to provision ``email`` by INSERTing the row itself.

    The email primary key is the mutex: concurrent first-requests all attempt this INSERT and the DB
    lets exactly one through. The row is deliberately written BEFORE the slow control-plane call, with
    project_id/api_key still NULL, so the losers have something to wait on instead of starting their
    own mint. Returns the claim stamp, or None if someone else got there first."""
    stamp = datetime.utcnow()
    user = User(email=email, project_id=None, api_key=None,
                name=(name or None) and name[:120], image=(image or None) and image[:512],
                provision_claim=token, provision_claimed_at=stamp,
                # AUTH-4: 카카오 무이메일 센티널은 실주소가 아니다 — 메일 발송(OTP·던닝) 차단
                email_verified=not email.endswith("@noemail.local"))
    with SessionLocal() as db:
        db.add(user)
        try:
            db.commit()
            return stamp
        except IntegrityError:
            db.rollback()
            if db.get(User, email) is None:
                raise   # not a lost race — a real schema/constraint problem, and it must be loud
            return None


def _seize_stale_claim(email: str, token: str) -> datetime | None:
    """Take over a claim whose provisioner never finished (it died mid-mint), so one crash can't wedge
    an account forever. Conditional on the row still being unprovisioned AND the lease being expired,
    and it re-stamps the claim token — which is what stops the original provisioner from later
    finalizing with the key it minted before losing the claim. Returns the new claim stamp, or None."""
    stamp = datetime.utcnow()
    cutoff = stamp - timedelta(seconds=settings.provision_lease_seconds)
    with SessionLocal() as db:
        result = db.execute(
            update(User)
            .where(User.email == email, User.project_id.is_(None),
                   (User.provision_claimed_at.is_(None)) | (User.provision_claimed_at < cutoff))
            .values(provision_claim=token, provision_claimed_at=stamp)
        )
        db.commit()
        return stamp if result.rowcount == 1 else None


def _finalize(email: str, token: str, project_id: str, api_key: str) -> User | None:
    """Record the minted account — but ONLY if we still hold the claim we staked (compare-and-set).

    This is the step whose absence caused the original bug: an unconditional write let whichever
    provisioner finished last overwrite the others. Here a provisioner that was taken over gets
    rowcount 0 and must discard its own result: its key has already been rotated away by the taker,
    so writing it would persist a dead credential."""
    with SessionLocal() as db:
        result = db.execute(
            update(User)
            .where(User.email == email, User.project_id.is_(None), User.provision_claim == token)
            .values(project_id=project_id, api_key=api_key,
                    # ME-2: freshly activated against the current default set — no reconcile needed later
                    connectors_reconciled_ver=settings.connectors_reconcile_ver,
                    provision_claim=None, provision_claimed_at=None)
        )
        db.commit()
        if result.rowcount != 1:
            return None
        return db.get(User, email)


def _release_claim(email: str, token: str) -> None:
    """Drop our unfinished claim after a failed mint so the next request retries immediately instead of
    waiting out the lease. Conditional, so it can only ever remove OUR own unprovisioned row."""
    with SessionLocal() as db:
        db.execute(delete(User).where(User.email == email, User.project_id.is_(None),
                                      User.provision_claim == token))
        db.commit()


async def _await_provisioned(email: str) -> User | None:
    """Wait for the request that won the claim to finish provisioning. Returns the finished user, or
    None if the claim row disappeared (the winner's mint failed and it released the claim) — the
    caller restarts the flow. Raises 503 if the winner never finishes within the wait budget."""
    deadline = asyncio.get_running_loop().time() + settings.provision_wait_seconds
    while True:
        user = _load_user(email)
        if _is_provisioned(user):
            return user
        if user is None:
            return None   # claim released — start over and try to become the provisioner ourselves
        if asyncio.get_running_loop().time() >= deadline:
            log.warning("provisioning wait timed out for %s", email)
            raise HTTPException(503, "계정을 준비하는 중이에요. 잠시 후 다시 시도해 주세요.")
        await asyncio.sleep(settings.provision_poll_seconds)


async def _reconcile_existing(user: User, name: str | None, image: str | None) -> User:
    """ME-2: reconcile the default connector set ONCE per user across the fleet. The gate is a
    PERSISTENT version column, not a process-local set — a process-local set re-fires the whole
    reconcile herd on EVERY replica after EVERY deploy/restart (each fresh process starts empty).
    Bump settings.connectors_reconcile_ver to re-run it after the default set changes."""
    if user.connectors_reconciled_ver == settings.connectors_reconcile_ver:
        return user
    if settings.plan_enforce_connectors:
        # PLAN-4: 플랜 기준 reconcile — free 유저의 프리미엄 커넥터 회수 포함(1회, 로깅).
        from studioapi.plans import apply_plan
        await apply_plan(user.email, user.plan or "free")
    else:
        await _activate_defaults(user.project_id)  # backfill free-set connectors only
    # Persist the reconcile version (so later requests / other replicas skip) + backfill profile
    # from the provider if we never captured it (never overwrite a set value — the user may have
    # edited their display name). Stamped AFTER the reconcile so a crash retries, not skips.
    with SessionLocal() as db:
        u = db.get(User, user.email)
        if u is None:
            return user
        u.connectors_reconciled_ver = settings.connectors_reconcile_ver
        if name and not u.name:
            u.name = name[:120]
        if image and not u.image:
            u.image = image[:512]
        db.commit()
        return u


async def _ensure_once(email: str, name: str | None, image: str | None,
                       referral_code: str | None) -> User | None:
    """One attempt at the get-or-provision flow. Returns None when the account's state changed under
    us (a claim was released mid-wait) and the caller should simply try again."""
    existing = _load_user(email)
    if _is_provisioned(existing):
        return await _reconcile_existing(existing, name, image)

    token = secrets.token_hex(16)
    stamp = _claim(email, token, name, image) if existing is None else None
    if stamp is None:
        # Someone else owns the claim. Take it over only if they are past the lease (i.e. dead).
        stamp = _seize_stale_claim(email, token)
    if stamp is None:
        return await _await_provisioned(email)

    try:
        # SIMPL-1: the project IS the account — name it after the owner (was the Tenant.name label).
        # The claim stamp travels as the fence: if this call arrives late, after a takeover already
        # provisioned the account, the control plane refuses to rotate the key out from under it.
        acct = await _provision_account(email, name=email, key_name="web",
                                        connectors=list(DEFAULT_CONNECTORS),
                                        fence=_fence_of(stamp))
    except Exception as exc:  # noqa: BLE001 — release the claim so the retry is immediate, not leased
        _release_claim(email, token)
        log.warning("provisioning failed for %s: %s", email, exc)
        raise HTTPException(502, "계정을 준비하지 못했어요. 잠시 후 다시 시도해 주세요.") from exc

    if not acct.get("api_key"):
        # Refused as stale: a takeover has already provisioned this account, so we hold nothing to
        # record. Wait for the winner rather than writing anything.
        log.info("provisioning for %s was superseded — deferring to the winner", email)
        return await _await_provisioned(email)

    user = _finalize(email, token, acct["project_id"], acct["api_key"])
    if user is None:
        # Our claim was taken over while we were minting (we stalled past the lease). The taker's
        # result is authoritative — and it already rotated the key we are holding — so wait for it.
        log.info("provisioning claim for %s was taken over mid-mint — deferring to the winner", email)
        return await _await_provisioned(email)
    if referral_code:  # REF-1: 가입 귀속 — 자기추천·미존재 코드는 내부에서 무시
        from studioapi.referrals import attribute_signup
        attribute_signup(email, referral_code)
    return user


async def ensure_user(email: str, name: str | None = None, image: str | None = None,
                      referral_code: str | None = None) -> User:
    """Get-or-provision the account for ``email``. ALWAYS returns a fully provisioned user (both
    project_id and api_key set) or raises — callers put the key straight into a gateway request."""
    for _ in range(3):
        user = await _ensure_once(email, name, image, referral_code)
        if user is not None:
            return user
    raise HTTPException(503, "계정을 준비하는 중이에요. 잠시 후 다시 시도해 주세요.")
