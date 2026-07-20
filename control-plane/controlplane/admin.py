"""Admin / management endpoints (guarded by the X-Admin-Token header).

Create projects (accounts) → API keys, and activate connectors per project. These are
platform-operator actions; account self-service UI can wrap them later.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError

from controlplane.auth import generate_key
from controlplane.config import settings
from controlplane.db import SessionLocal
from controlplane.models import (
    Activation, ApiKey, AuditLog, LlmUsage, Project, ProviderUsage, UsageEvent)


async def require_admin(x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None) -> None:
    if not x_admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "Invalid admin token.")


router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])


class NameIn(BaseModel):
    name: str


class KeyIn(BaseModel):
    name: str = "key"
    scopes: str = "read"


class ActivationIn(BaseModel):
    connector_id: str
    enabled: bool = True
    byo_credentials: str | None = None


@router.post("/projects", summary="Create a project (account)")
async def create_project(body: NameIn) -> dict:
    with SessionLocal() as db:
        p = Project(name=body.name)
        db.add(p)
        db.commit()
        return {"id": p.id, "name": p.name}


class ProvisionIn(BaseModel):
    owner_ref: str                      # stable caller-side identity (studio: the user's email)
    name: str | None = None             # display label; defaults to owner_ref
    key_name: str = "web"               # keys under this name are rotated on re-provision
    connectors: list[str] = []          # activated (enabled=True) if missing; never de-activates
    plan: str | None = None             # PLAN-2 rate tier, applied like PATCH /projects/{id}
    internal: bool | None = None        # SYS-1 platform-infra class
    fence: int | None = None            # monotonic guard against a superseded caller rotating the key


@router.post("/provision", summary="PROV-1: idempotently provision an account (project + key + activations)")
async def provision_account(body: ProvisionIn) -> dict:
    """Get-or-create the project owned by ``owner_ref``, issue it a working API key, and ensure the
    requested connectors are activated — **idempotent under concurrency**, which the separate
    create-project → create-key → activate calls could never be.

    This exists because provisioning is a multi-step, slow (network) operation that concurrent
    first-requests for the same user used to run in parallel, each minting its own project+key and
    leaving orphans behind (the surviving `users` row referenced only the last one). The arbiter is
    the DB: `projects.owner_ref` is UNIQUE, so exactly one creator can win and every other caller
    converges onto that same row.

    Key handling is **rotation**, not reuse: only the key's hash is stored, so a previously issued
    secret cannot be handed out twice. A repeat call therefore deactivates the existing keys under
    ``key_name`` and returns a fresh one — which is exactly the recovery semantics the caller needs,
    since a repeat call means the earlier secret was lost (a crashed provisioner). Callers must treat
    the returned key as the only live one; anyone still holding the previous secret loses access
    within the gateway's auth-cache TTL.
    """
    created = False
    with SessionLocal() as db:
        # ── 1. the project: get-or-create, arbitrated by the unique owner_ref ──────────────────
        p = db.execute(select(Project).where(Project.owner_ref == body.owner_ref)).scalar_one_or_none()
        if p is None:
            p = Project(name=(body.name or body.owner_ref)[:128], owner_ref=body.owner_ref)
            db.add(p)
            try:
                db.commit()
                created = True
            except IntegrityError:
                # A concurrent caller won the unique owner_ref — converge onto its project.
                db.rollback()
                p = db.execute(select(Project).where(Project.owner_ref == body.owner_ref)).scalar_one_or_none()
                if p is None:  # unique violation on something else (or the row vanished) — surface it
                    raise HTTPException(500, "provision: could not resolve the project for this owner.")
        project_id = p.id

        # ── 2. key + activations, serialized per project ──────────────────────────────────────
        # Lock the project row for the rest of the transaction. Without it, two callers that
        # converged on the same project would each read "no active key", each insert one, and both
        # commit — leaving TWO live keys where the contract promises exactly one, i.e. re-creating
        # the orphan credential this endpoint exists to prevent. (A no-op on SQLite, which already
        # serializes writers; the tests still cover the converge path below.)
        locked = db.execute(
            select(Project).where(Project.id == project_id).with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise HTTPException(500, "provision: the project disappeared mid-provision.")
        if body.plan is not None:
            locked.plan = body.plan
        if body.internal is not None:
            locked.internal = body.internal

        # Fencing. Rotation retires the previous secret, so a caller that has already been superseded
        # (its work was taken over, and the successor has since provisioned) must NOT be allowed to
        # rotate: it would kill the key its successor is now using and leave the account holding a dead
        # credential. Callers that can be superseded pass a monotonically increasing `fence`; a lower
        # one than the last accepted rotation means exactly that, so refuse and hand back no key. The
        # comparison happens under the row lock, so it is decided by the DB, not by arrival order.
        stale = body.fence is not None and locked.key_fence is not None and body.fence < locked.key_fence
        full: str | None = None
        if not stale:
            if body.fence is not None:
                locked.key_fence = body.fence
            db.execute(
                sa_update(ApiKey)
                .where(ApiKey.project_id == project_id, ApiKey.name == body.key_name,
                       ApiKey.active.is_(True))
                .values(active=False)
            )
            full, prefix, key_hash = generate_key()
            db.add(ApiKey(project_id=project_id, name=body.key_name, prefix=prefix, key_hash=key_hash))

        have = set(db.execute(
            select(Activation.connector_id).where(Activation.project_id == project_id)
        ).scalars().all())
        for cid in body.connectors:
            if cid not in have:
                db.add(Activation(project_id=project_id, connector_id=cid, enabled=True))
                have.add(cid)
        db.commit()

    # The gateway caches both the activation set and (plan, internal) — make this call's effect live now.
    from controlplane.gateway import invalidate_entitlement, invalidate_project_meta
    invalidate_entitlement(project_id)
    invalidate_project_meta(project_id)
    return {"project_id": project_id, "api_key": full, "created": created, "stale": stale,
            "note": "Store this now — it is not retrievable later."}


class ProjectPatchIn(BaseModel):
    plan: str | None = None      # guest | free | pro — drives the gateway's per-key rate tier
    internal: bool | None = None # SYS-1: mark as platform infra (gateway skips entitlement) — server-side only


@router.patch("/projects/{project_id}", summary="Update a project (PLAN-2: plan tier / SYS-1: internal)")
async def patch_project(project_id: str, body: ProjectPatchIn) -> dict:
    with SessionLocal() as db:
        p = db.get(Project, project_id)
        if p is None:
            raise HTTPException(404, "Unknown project.")
        if body.plan is not None:
            p.plan = body.plan
        if body.internal is not None:
            p.internal = body.internal
        db.commit()
        result = {"id": p.id, "name": p.name, "plan": p.plan, "internal": p.internal}
    # the gateway caches (plan, internal) ~60s — invalidate so a plan/internal change is live now.
    from controlplane.gateway import invalidate_project_meta
    invalidate_project_meta(project_id)
    return result


@router.post("/projects/{project_id}/keys", summary="Create an API key (shown once)")
async def create_key(project_id: str, body: KeyIn) -> dict:
    with SessionLocal() as db:
        if db.get(Project, project_id) is None:
            raise HTTPException(404, "Unknown project.")
        full, prefix, key_hash = generate_key()
        k = ApiKey(project_id=project_id, name=body.name, prefix=prefix, key_hash=key_hash, scopes=body.scopes)
        db.add(k)
        db.commit()
        return {"id": k.id, "project_id": project_id, "api_key": full, "note": "Store this now — it is not retrievable later."}


@router.post("/projects/{project_id}/activations", summary="Activate a connector for a project")
async def activate(project_id: str, body: ActivationIn) -> dict:
    with SessionLocal() as db:
        if db.get(Project, project_id) is None:
            raise HTTPException(404, "Unknown project.")
        existing = db.execute(
            select(Activation).where(Activation.project_id == project_id, Activation.connector_id == body.connector_id)
        ).scalar_one_or_none()
        if existing:
            existing.enabled = body.enabled
            existing.byo_credentials = body.byo_credentials
            db.commit()
            act = existing
        else:
            act = Activation(project_id=project_id, connector_id=body.connector_id, enabled=body.enabled, byo_credentials=body.byo_credentials)
            db.add(act)
            try:
                db.commit()
            except IntegrityError:
                # PROV-1: (project, connector) is UNIQUE, so a concurrent activation of the same
                # connector lands here instead of silently duplicating the row — converge onto the
                # row the other caller inserted and apply our values to it.
                db.rollback()
                act = db.execute(
                    select(Activation).where(Activation.project_id == project_id,
                                             Activation.connector_id == body.connector_id)
                ).scalar_one_or_none()
                if act is None:
                    raise HTTPException(500, "activation: could not resolve the row after a conflict.")
                act.enabled = body.enabled
                act.byo_credentials = body.byo_credentials
                db.commit()
        # CR-4: the gateway caches the project's activation set — invalidate so this change is live now.
        from controlplane.gateway import invalidate_entitlement
        invalidate_entitlement(project_id)
        return {"id": act.id, "project_id": project_id, "connector_id": act.connector_id, "enabled": act.enabled}


@router.get("/projects/{project_id}/activations", summary="List project activations")
async def list_activations(project_id: str) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(Activation).where(Activation.project_id == project_id)).scalars().all()
        return {"activations": [{"connector_id": a.connector_id, "enabled": a.enabled} for a in rows]}


class LlmUsageIn(BaseModel):
    service: str
    kind: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 1
    estimated: bool = False
    project_id: str | None = None   # METER-1: per-user cost attribution (None = shared/background)
    # COST-2: usage_metadata breakdowns (optional; old emitters omit them → 0). Subsets of the totals.
    cached_input_tokens: int = 0
    tool_input_tokens: int = 0
    thinking_tokens: int = 0


@router.post("/llm-usage", summary="COST-1: record one LLM/embedding call's token usage")
async def llm_usage_ingest(body: LlmUsageIn) -> dict:
    with SessionLocal() as db:
        db.add(LlmUsage(service=body.service[:24], kind=body.kind[:32], model=body.model[:64],
                        input_tokens=max(0, body.input_tokens), output_tokens=max(0, body.output_tokens),
                        calls=max(1, body.calls), estimated=body.estimated,
                        project_id=(body.project_id or None) and body.project_id[:40],
                        cached_input_tokens=max(0, body.cached_input_tokens),
                        tool_input_tokens=max(0, body.tool_input_tokens),
                        thinking_tokens=max(0, body.thinking_tokens)))
        db.commit()
    return {"ok": True}


class ProviderUsageIn(BaseModel):
    providers: dict[str, int] = {}   # COST-3: {provider: calls} accumulated by a sweep, batched


@router.post("/provider-usage", summary="COST-3: record background-sweep upstream call counts")
async def provider_usage_ingest(body: ProviderUsageIn) -> dict:
    with SessionLocal() as db:
        for prov, calls in (body.providers or {}).items():
            if prov and int(calls) > 0:
                db.add(ProviderUsage(provider=str(prov)[:48], calls=max(0, int(calls))))
        db.commit()
    return {"ok": True}


@router.get("/llm-usage/summary", summary="COST-1: token usage grouped by model × kind (+ per-day tail)")
async def llm_usage_summary(days: int = 30) -> dict:
    from datetime import datetime as _dt, timedelta as _td
    since = _dt.utcnow() - _td(days=max(1, min(days, 365)))
    with SessionLocal() as db:
        rows = db.execute(
            select(LlmUsage.service, LlmUsage.model, LlmUsage.kind,
                   func.count(), func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                   func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                   func.coalesce(func.sum(LlmUsage.calls), 0),
                   func.max(LlmUsage.estimated))
            .where(LlmUsage.ts >= since)
            .group_by(LlmUsage.service, LlmUsage.model, LlmUsage.kind)
        ).all()
        daily = db.execute(
            select(func.date(LlmUsage.ts), func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                   func.coalesce(func.sum(LlmUsage.output_tokens), 0), func.coalesce(func.sum(LlmUsage.calls), 0))
            .where(LlmUsage.ts >= since).group_by(func.date(LlmUsage.ts)).order_by(func.date(LlmUsage.ts))
        ).all()
    return {
        "since": since.isoformat(), "days": days,
        "rows": [{"service": s, "model": m, "kind": k, "records": n,
                  "input_tokens": int(i), "output_tokens": int(o), "calls": int(c),
                  "estimated": bool(e)} for s, m, k, n, i, o, c, e in rows],
        "daily": [{"date": str(d), "input_tokens": int(i), "output_tokens": int(o), "calls": int(c)}
                  for d, i, o, c in daily],
    }


@router.get("/llm-usage/by-project", summary="METER-2: 프로젝트(유저)별 LLM 토큰 롤업 — 유닛 이코노믹스")
async def llm_usage_by_project(days: int = 30) -> dict:
    """LLM 토큰을 project(=유저 계정) × model로 롤업 — admin '유저별 원가' 화면이 pricing
    레지스트리로 달러화한다. project_id NULL = 공용/백그라운드(피드·인제스트)."""
    from datetime import datetime as _dt, timedelta as _td
    since = _dt.utcnow() - _td(days=max(1, min(days, 365)))
    with SessionLocal() as db:
        rows = db.execute(
            select(LlmUsage.project_id, LlmUsage.model,
                   func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                   func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                   func.coalesce(func.sum(LlmUsage.calls), 0))
            .where(LlmUsage.ts >= since)
            .group_by(LlmUsage.project_id, LlmUsage.model)
            .order_by(func.sum(LlmUsage.input_tokens).desc())
        ).all()
        # project → 계정 라벨(=유저 이메일, Project.name) 매핑
        pids = {p for p, *_ in rows if p}
        names: dict[str, str] = {}
        if pids:
            for pid, pname in db.execute(
                select(Project.id, Project.name).where(Project.id.in_(pids))
            ).all():
                names[pid] = pname
    return {"since": since.isoformat(), "days": days,
            "rows": [{"project_id": p, "tenant": names.get(p) if p else None, "model": m,
                      "input_tokens": int(i), "output_tokens": int(o), "calls": int(c)}
                     for p, m, i, o, c in rows]}


@router.get("/projects/{project_id}/usage", summary="Usage + cost summary")
async def usage(project_id: str) -> dict:
    from controlplane.models import UsageRollup

    with SessionLocal() as db:
        total_calls = db.scalar(select(func.count()).select_from(UsageEvent).where(UsageEvent.project_id == project_id)) or 0
        total_cost = db.scalar(select(func.coalesce(func.sum(UsageEvent.cost_units), 0)).where(UsageEvent.project_id == project_id)) or 0
        by_conn = db.execute(
            select(UsageEvent.connector_id, func.count(), func.coalesce(func.sum(UsageEvent.cost_units), 0))
            .where(UsageEvent.project_id == project_id).group_by(UsageEvent.connector_id)
        ).all()
        # HI-13: add the cumulative totals for rows retention has already dropped, so lifetime
        # calls/cost stay correct after the raw usage_events age out.
        roll = db.get(UsageRollup, project_id)
        return {
            "project_id": project_id,
            "total_calls": int(total_calls) + (roll.calls if roll else 0),
            "total_cost_units": int(total_cost) + (roll.cost_units if roll else 0),
            "by_connector": [{"connector_id": c, "calls": n, "cost_units": int(cost)} for c, n, cost in by_conn],
        }


@router.get("/projects/{project_id}/audit", summary="Recent audit log")
async def audit(project_id: str, limit: int = 50) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(AuditLog).where(AuditLog.project_id == project_id).order_by(AuditLog.id.desc()).limit(limit)
        ).scalars().all()
        return {"audit": [{"action": a.action, "detail": a.detail} for a in rows]}


@router.get("/catalog", summary="Proxy the data-plane catalog (what can be activated)")
async def catalog() -> dict:
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{settings.datasets_url}/catalog")
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Data plane catalog unavailable: {exc}")
