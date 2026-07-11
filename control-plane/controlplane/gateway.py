"""The data gateway: authenticate → entitle → rate-limit → meter → audit → proxy.

A catch-all route. Anything not matched by the admin/meta routes is treated as a
data request and forwarded to the data plane, gated by the tenant's connector
activations. Registered last so specific routes win.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from controlplane.auth import resolve_key
from controlplane.catalog_index import candidate_connectors, cost_units, is_governed
from controlplane.config import settings
from controlplane.db import SessionLocal
from controlplane.models import Activation, AuditLog, Project, UsageEvent
from controlplane.ratelimit import RateLimiter

router = APIRouter()
_client = httpx.AsyncClient(timeout=settings.http_timeout_seconds)
_limiter = RateLimiter(settings.rate_limit_per_minute)

# PLAN-2: per-plan gateway rate tiers (abuse backstop — the product quota lives in studio).
# Env-overridable; a project with no/unknown plan keeps the global default, so existing ops/test
# projects behave exactly as before.
import json as _json
import os as _os

_PLAN_RATE_DEFAULTS = {"guest": 240, "free": 60, "pro": 240}
try:
    _PLAN_RATES = {**_PLAN_RATE_DEFAULTS, **_json.loads(_os.environ.get("PLAN_RATE_LIMITS_JSON", "{}"))}
except Exception:  # noqa: BLE001 — a bad override never takes the gateway down
    _PLAN_RATES = dict(_PLAN_RATE_DEFAULTS)

_plan_cache: dict[str, tuple[float, str | None]] = {}   # project_id → (expires, plan)
_PLAN_CACHE_TTL = 60.0


def _project_plan(project_id: str) -> str | None:
    """The project's plan tier, cached ~60s — one tiny lookup per key per minute, not per call."""
    hit = _plan_cache.get(project_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    with SessionLocal() as db:
        row = db.get(Project, project_id)
        plan = getattr(row, "plan", None) if row else None
    _plan_cache[project_id] = (time.monotonic() + _PLAN_CACHE_TTL, plan)
    return plan


def _rate_limit_for(project_id: str) -> int | None:
    plan = _project_plan(project_id)
    return _PLAN_RATES.get(plan) if plan else None

_HOP = {"host", "content-length", "x-api-key", "x-admin-token", "connection", "x-tenant-id"}

# AuditLog action labels — named so audit queries ("show me everything denied") are discoverable
# and typo-proof, instead of bare string literals scattered across the flow.
ACTION_ACCESS = "access"
ACTION_DENIED = "denied"
ACTION_ERROR = "error"


def _audit(project_id, key_id, action: str, detail: str) -> None:
    with SessionLocal() as db:
        db.add(AuditLog(project_id=project_id, api_key_id=key_id, action=action, detail=detail[:500]))
        db.commit()


def _meter(project_id, key_id, connector_id, method, path, status, cost, latency) -> None:
    with SessionLocal() as db:
        db.add(UsageEvent(project_id=project_id, api_key_id=key_id, connector_id=connector_id,
                          method=method, path=path, status=status, cost_units=cost, latency_ms=latency))
        db.commit()


async def _proxy(method, path, request, *, connector_id, cost=0, project_id=None, key_id=None, base_url=None, extra_headers=None) -> Response:
    url = f"{base_url or settings.datasets_url}{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    if extra_headers:
        headers.update(extra_headers)
    # RAG-bound requests (semantic search/ingest) embed via an external model API and can run
    # long under concurrent ingest — give them their own budget instead of the global cap, so a
    # slow search degrades a turn's latency rather than silently dropping its RAG evidence.
    timeout = settings.rag_http_timeout_seconds if base_url == settings.rag_url else settings.http_timeout_seconds
    started = time.monotonic()
    try:
        upstream = await _client.request(method, url, content=await request.body(),
                                         headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        if project_id:
            _audit(project_id, key_id, ACTION_ERROR, f"{method} {path}: {exc}")
        raise HTTPException(502, f"Data plane error: {exc}")
    latency = round((time.monotonic() - started) * 1000)
    if project_id:
        _meter(project_id, key_id, connector_id, method, path, upstream.status_code, cost, latency)
        _audit(project_id, key_id, ACTION_ACCESS, f"{method} {path} -> {upstream.status_code} ({connector_id})")
    out_headers = {"x-connector": connector_id or "-", "x-cost-units": str(cost)}
    ct = upstream.headers.get("content-type")
    if ct:
        out_headers["content-type"] = ct
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)


def _resolve_entitlement(project_id, key_id, method: str, path: str, market: str):
    """Entitlement = activation (invariant #2/#3): which activated connector may serve this request.

    For a catalog-governed path, find the candidate connectors and pick the first the project has
    activated — else audit the denial and raise 403. Ungoverned paths get the default (no connector,
    datasets base). Returns ``(connector_id, cost, base_url, extra_headers)``."""
    if not is_governed(method, path):
        return None, 0, settings.datasets_url, None
    cands = candidate_connectors(method, path, market)
    cand_ids = [c["connector_id"] for c in cands]
    with SessionLocal() as db:
        active = {
            a.connector_id
            for a in db.execute(
                select(Activation).where(
                    Activation.project_id == project_id,
                    Activation.connector_id.in_(cand_ids),
                    Activation.enabled.is_(True),
                )
            ).scalars()
        }
    chosen = next((c for c in cands if c["connector_id"] in active), None)
    if chosen is None:
        _audit(project_id, key_id, ACTION_DENIED, f"{method} {path} market={market} needs one of {cand_ids}")
        raise HTTPException(403, f"Not entitled — activate one of {cand_ids} for this project.")
    cost = cost_units(chosen["cost_tier"])
    is_rag = chosen.get("service") == "rag"
    base_url = settings.rag_url if is_rag else settings.datasets_url
    # RAG is the only multi-tenant data store — scope it to the caller's project so one tenant's
    # ingested docs never surface in another's search.
    extra_headers = {"X-Tenant-Id": project_id} if is_rag else None
    return chosen["connector_id"], cost, base_url, extra_headers


@router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def gateway(full_path: str, request: Request) -> Response:
    method, path = request.method, "/" + full_path
    market = (request.query_params.get("market") or "US").upper()

    # 0) public discovery (catalog) — forwarded without auth/metering
    if path == "/catalog" or path.startswith("/catalog/"):
        return await _proxy(method, path, request, connector_id=None)

    # 1) authenticate
    with SessionLocal() as db:
        api_key = resolve_key(db, request.headers.get("X-API-KEY"))
        if api_key is None:
            raise HTTPException(401, "Missing or invalid API key.")
        project_id, key_id = api_key.project_id, api_key.id

    # 2) entitlement (only catalog-governed paths)
    connector_id, cost, base_url, extra_headers = _resolve_entitlement(project_id, key_id, method, path, market)

    # 3) rate limit (plan-tiered backstop — None plan keeps the global default)
    if not _limiter.allow(key_id, _rate_limit_for(project_id)):
        raise HTTPException(429, "Rate limit exceeded.")

    # 4) proxy + meter + audit
    return await _proxy(method, path, request, connector_id=connector_id, cost=cost,
                        project_id=project_id, key_id=key_id, base_url=base_url, extra_headers=extra_headers)
