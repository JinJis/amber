"""Admin / management endpoints (guarded by the X-Admin-Token header).

Create tenants → projects → API keys, and activate connectors per project. These
are platform-operator actions; tenant self-service UI can wrap them later.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from controlplane.auth import generate_key
from controlplane.config import settings
from controlplane.db import SessionLocal
from controlplane.models import (
    Activation, ApiKey, AuditLog, LlmUsage, Project, ProviderUsage, Tenant, UsageEvent)


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


@router.post("/tenants", summary="Create a tenant")
async def create_tenant(body: NameIn) -> dict:
    with SessionLocal() as db:
        t = Tenant(name=body.name)
        db.add(t)
        db.commit()
        return {"id": t.id, "name": t.name}


@router.post("/tenants/{tenant_id}/projects", summary="Create a project")
async def create_project(tenant_id: str, body: NameIn) -> dict:
    with SessionLocal() as db:
        if db.get(Tenant, tenant_id) is None:
            raise HTTPException(404, "Unknown tenant.")
        p = Project(tenant_id=tenant_id, name=body.name)
        db.add(p)
        db.commit()
        return {"id": p.id, "tenant_id": tenant_id, "name": p.name}


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
        result = {"id": p.id, "tenant_id": p.tenant_id, "name": p.name, "plan": p.plan, "internal": p.internal}
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
    """LLM 토큰을 project(=유저 테넌트) × model로 롤업 — admin '유저별 원가' 화면이 pricing
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
        # project → tenant 이름(=유저 이메일) 매핑
        pids = {p for p, *_ in rows if p}
        names: dict[str, str] = {}
        if pids:
            for pid, tname in db.execute(
                select(Project.id, Tenant.name).join(Tenant, Project.tenant_id == Tenant.id)
                .where(Project.id.in_(pids))
            ).all():
                names[pid] = tname
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
