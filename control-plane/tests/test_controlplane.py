"""Control-plane tests: auth, entitlement resolution, rate limit, gateway flow.

The data plane is respx-mocked, so these run with no external service.
"""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from controlplane import catalog_index, gateway
from controlplane.auth import generate_key, resolve_key
from controlplane.db import SessionLocal, init_db
from controlplane.main import app
from controlplane.models import ApiKey
from controlplane.ratelimit import RateLimiter

client = TestClient(app)
ADMIN = {"X-Admin-Token": "dev-admin-token"}

CATALOG = [
    {"id": "yahoo", "resources": [{"method": "GET", "path": "/prices", "markets": ["US", "KR"], "cost_tier": "free"}]},
    {"id": "sec_edgar", "resources": [{"method": "GET", "path": "/company/facts", "markets": ["US"], "cost_tier": "free"}]},
]
_PRICES = r"http://127\.0\.0\.1:8000/prices"


def setup_module(_module):
    init_db()
    catalog_index.set_catalog(CATALOG)


# --- auth -----------------------------------------------------------------
def test_auth_roundtrip():
    full, prefix, key_hash = generate_key()
    with SessionLocal() as db:
        db.add(ApiKey(project_id="prj_x", name="t", prefix=prefix, key_hash=key_hash))
        db.commit()
        assert resolve_key(db, full) is not None
        assert resolve_key(db, "vgk_deadbeef_wrongsecret") is None
        assert resolve_key(db, None) is None
        assert resolve_key(db, "not-a-key") is None


# --- entitlement resolution ----------------------------------------------
def test_entitlement_resolver():
    catalog_index.set_catalog(CATALOG)
    assert catalog_index.is_governed("GET", "/prices")
    assert not catalog_index.is_governed("GET", "/health")
    assert {c["connector_id"] for c in catalog_index.candidate_connectors("GET", "/prices", "KR")} == {"yahoo"}
    # market gating: company/facts is US-only here
    assert catalog_index.candidate_connectors("GET", "/company/facts", "KR") == []
    assert [c["connector_id"] for c in catalog_index.candidate_connectors("GET", "/company/facts", "US")] == ["sec_edgar"]


# --- rate limit -----------------------------------------------------------
def test_ratelimit_unit():
    rl = RateLimiter(2)
    assert rl.allow("k") and rl.allow("k") and not rl.allow("k")


def test_ratelimit_key_isolation_and_window_reset(monkeypatch):
    # one key exhausting its quota must NOT affect another key (per-key buckets), and crossing the
    # minute boundary resets the window. (Fake the clock so the window advance is deterministic.)
    import types

    import controlplane.ratelimit as RL

    clock = [1_000_000.0]
    monkeypatch.setattr(RL, "time", types.SimpleNamespace(time=lambda: clock[0]))
    rl = RateLimiter(2)
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a")   # key A exhausted
    assert rl.allow("b") and rl.allow("b") and not rl.allow("b")   # key B independent, unaffected
    clock[0] += 60                                                  # next minute window
    assert rl.allow("a") and rl.allow("b")                          # both reset


# --- gateway end-to-end ---------------------------------------------------
def _make_project(name: str):
    t = client.post("/admin/tenants", json={"name": name}, headers=ADMIN).json()
    p = client.post(f"/admin/tenants/{t['id']}/projects", json={"name": "prod"}, headers=ADMIN).json()
    k = client.post(f"/admin/projects/{p['id']}/keys", json={"name": "k"}, headers=ADMIN).json()
    return p["id"], k["api_key"]


@respx.mock
def test_gateway_entitlement_and_metering():
    catalog_index.set_catalog(CATALOG)
    respx.route(method="GET", url__regex=_PRICES).mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": []})
    )
    pid, key = _make_project("Acme")
    H = {"X-API-KEY": key}

    assert client.get("/prices?ticker=AAPL&market=US").status_code == 401  # no key
    assert client.get("/prices?ticker=AAPL&market=US", headers=H).status_code == 403  # not activated

    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "yahoo"}, headers=ADMIN)
    r = client.get("/prices?ticker=AAPL&market=US", headers=H)
    assert r.status_code == 200 and r.json()["ticker"] == "AAPL"
    assert r.headers.get("x-connector") == "yahoo"

    usage = client.get(f"/admin/projects/{pid}/usage", headers=ADMIN).json()
    assert usage["total_calls"] >= 1
    assert client.get(f"/admin/projects/{pid}/usage").status_code == 401  # admin token required


@respx.mock
def test_gateway_rate_limit(monkeypatch):
    catalog_index.set_catalog(CATALOG)
    monkeypatch.setattr(gateway, "_limiter", RateLimiter(1))
    respx.route(method="GET", url__regex=_PRICES).mock(return_value=httpx.Response(200, json={}))
    pid, key = _make_project("Rate")
    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "yahoo"}, headers=ADMIN)
    H = {"X-API-KEY": key}
    assert client.get("/prices?market=US", headers=H).status_code == 200
    assert client.get("/prices?market=US", headers=H).status_code == 429


def test_admin_requires_token():
    assert client.post("/admin/tenants", json={"name": "x"}).status_code == 401


@respx.mock
def test_gateway_routes_rag_to_rag_service(monkeypatch):
    from controlplane.config import settings as cp_settings

    cp_settings.rag_url = "http://rag.test"
    catalog_index.set_catalog(CATALOG + [
        {"id": "rag", "service": "rag", "resources": [
            {"method": "POST", "path": "/rag/search", "markets": ["US", "KR"], "cost_tier": "low"}]}
    ])
    respx.route(method="POST", url__regex=r"http://rag\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"text": "x", "provenance": {"source": "SEC EDGAR"}}]})
    )
    pid, key = _make_project("RagTenant")
    H = {"X-API-KEY": key}
    assert client.post("/rag/search", json={"query": "q"}, headers=H).status_code == 403  # rag not activated
    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "rag"}, headers=ADMIN)
    r = client.post("/rag/search", json={"query": "q"}, headers=H)
    assert r.status_code == 200 and r.headers.get("x-connector") == "rag"
    assert r.json()["hits"][0]["provenance"]["source"] == "SEC EDGAR"


@respx.mock
def test_gateway_injects_tenant_header_for_rag(monkeypatch):
    # PH-2a: the gateway scopes RAG to the caller's project via X-Tenant-Id, and a
    # client-supplied X-Tenant-Id is stripped (no cross-tenant spoofing).
    from controlplane.config import settings as cp_settings

    cp_settings.rag_url = "http://rag.test"
    catalog_index.set_catalog(CATALOG + [
        {"id": "rag", "service": "rag", "resources": [
            {"method": "POST", "path": "/rag/search", "markets": ["US", "KR"], "cost_tier": "low"}]}
    ])
    route = respx.route(method="POST", url__regex=r"http://rag\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": []})
    )
    pid, key = _make_project("RagScoped")
    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "rag"}, headers=ADMIN)
    r = client.post("/rag/search", json={"query": "q"},
                    headers={"X-API-KEY": key, "X-Tenant-Id": "ten_spoofed"})
    assert r.status_code == 200
    forwarded = route.calls.last.request.headers
    assert forwarded["x-tenant-id"] == pid          # authoritative project id
    assert forwarded["x-tenant-id"] != "ten_spoofed"  # client value did not pass through


@respx.mock
def test_gateway_audit_log_and_activation_listing():
    catalog_index.set_catalog(CATALOG)
    respx.route(method="GET", url__regex=_PRICES).mock(return_value=httpx.Response(200, json={"ticker": "AAPL"}))
    pid, key = _make_project("Audited")
    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "yahoo"}, headers=ADMIN)
    # listing reflects the activation
    acts = client.get(f"/admin/projects/{pid}/activations", headers=ADMIN).json()
    assert any(a["connector_id"] == "yahoo" and a["enabled"] for a in acts["activations"])
    # a successful call is written to the audit log
    client.get("/prices?ticker=AAPL&market=US", headers={"X-API-KEY": key})
    audit = client.get(f"/admin/projects/{pid}/audit", headers=ADMIN).json()
    assert audit["audit"]  # at least one audited action recorded
    assert any("yahoo" in (e.get("detail") or "") for e in audit["audit"])


@respx.mock
def test_usage_accumulates_across_calls():
    catalog_index.set_catalog(CATALOG)
    respx.route(method="GET", url__regex=_PRICES).mock(return_value=httpx.Response(200, json={}))
    pid, key = _make_project("Counter")
    client.post(f"/admin/projects/{pid}/activations", json={"connector_id": "yahoo"}, headers=ADMIN)
    H = {"X-API-KEY": key}
    for _ in range(3):
        client.get("/prices?market=US", headers=H)
    assert client.get(f"/admin/projects/{pid}/usage", headers=ADMIN).json()["total_calls"] >= 3


def test_two_keys_resolve_independently():
    # distinct keys must not collide even though they share the vgk_ scheme
    full1, p1, h1 = generate_key()
    full2, p2, h2 = generate_key()
    assert p1 != p2 and full1 != full2
    with SessionLocal() as db:
        db.add(ApiKey(project_id="prjA", name="a", prefix=p1, key_hash=h1))
        db.add(ApiKey(project_id="prjB", name="b", prefix=p2, key_hash=h2))
        db.commit()
        assert resolve_key(db, full1).project_id == "prjA"
        assert resolve_key(db, full2).project_id == "prjB"
        # a valid prefix with the wrong secret resolves to nothing
        assert resolve_key(db, f"{p1}_{'0' * 32}") is None


def test_unknown_route_is_not_governed():
    catalog_index.set_catalog(CATALOG)
    assert not catalog_index.is_governed("GET", "/totally/unknown")
    assert catalog_index.candidate_connectors("GET", "/totally/unknown", "US") == []


def test_catalog_index_carries_service():
    catalog_index.set_catalog([
        {"id": "rag", "service": "rag", "resources": [{"method": "POST", "path": "/rag/search", "markets": [], "cost_tier": "low"}]},
        {"id": "yahoo", "resources": [{"method": "GET", "path": "/prices", "markets": ["US"], "cost_tier": "free"}]},
    ])
    rag = catalog_index.candidate_connectors("POST", "/rag/search", "US")
    assert rag and rag[0]["service"] == "rag"
    yh = catalog_index.candidate_connectors("GET", "/prices", "US")
    assert yh and yh[0]["service"] == "datasets"  # default
