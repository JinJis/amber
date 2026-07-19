"""SC-2.6 — multi-replica readiness for the provisioning/guest/feed edges.

- ME-2: reconcile is gated by a PERSISTENT version column (not a process-local set), so a deploy's
  default-connector backfill fires once per user across the fleet — never a per-replica herd.
- ME-4: abandoned guest rows are GC'd (bounded growth) and the per-IP turn count JOINs instead of
  materializing a thousands-entry IN list.
- ME-5: background feeds meter against a dedicated system tenant key, not an arbitrary user's.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import respx

from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.guest import cleanup_stale_guests, guest_email
from studioapi.models import GuestSession, ServiceState, TurnUsage, User


def setup_module(_module):
    init_db()


def _mock_cp(tenant="t1", project="p1", key="vgk_1"):
    """Mock the control-plane provisioning chain. Returns (tenant_route, activations_route)."""
    tenant_route = respx.post("http://cp.test/admin/tenants").mock(
        return_value=httpx.Response(200, json={"id": tenant}))
    respx.post(f"http://cp.test/admin/tenants/{tenant}/projects").mock(
        return_value=httpx.Response(200, json={"id": project}))
    respx.post(f"http://cp.test/admin/projects/{project}/keys").mock(
        return_value=httpx.Response(200, json={"api_key": key}))
    activations = respx.post(f"http://cp.test/admin/projects/{project}/activations").mock(
        return_value=httpx.Response(200, json={}))
    return tenant_route, activations


# --- ME-2: reconcile version column ------------------------------------------------------------
@respx.mock
async def test_me2_reconcile_fires_once_per_version(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "connectors_reconcile_ver", "1")
    monkeypatch.setattr(settings, "plan_enforce_connectors", False)
    _, activations = _mock_cp()
    from studioapi.provision import ensure_user

    with SessionLocal() as db:
        db.query(User).filter(User.email == "me2@u.com").delete()
        db.commit()

    # first sign-up: freshly activated → stamped at the current version
    u = await ensure_user("me2@u.com")
    assert u.connectors_reconciled_ver == "1"
    after_signup = activations.call_count
    assert after_signup >= 1

    # a later request at the SAME version does NOT re-fire the reconcile (no herd)
    await ensure_user("me2@u.com")
    assert activations.call_count == after_signup

    # an old row (version NULL, provisioned before the column existed) reconciles ONCE, then stamps
    with SessionLocal() as db:
        db.get(User, "me2@u.com").connectors_reconciled_ver = None
        db.commit()
    await ensure_user("me2@u.com")
    assert activations.call_count > after_signup
    reconciled = activations.call_count
    with SessionLocal() as db:
        assert db.get(User, "me2@u.com").connectors_reconciled_ver == "1"
    # ...and now it's stamped, so it never fires again
    await ensure_user("me2@u.com")
    assert activations.call_count == reconciled


# --- ME-5: dedicated system tenant key ---------------------------------------------------------
@respx.mock
async def test_me5_system_key_provisioned_cached_and_preferred(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    from studioapi.provision import _SYSTEM_STATE_KEY, ensure_system_project, system_api_key_cached

    with SessionLocal() as db:
        db.query(ServiceState).filter(ServiceState.key == _SYSTEM_STATE_KEY).delete()
        db.merge(User(email="rand@u.com", tenant_id="t", project_id="p", api_key="vgk_rand"))
        db.commit()

    # before provisioning: no cached key, and background feeds fall back to an arbitrary user's key
    from studioapi.askfeed import _any_api_key, _bg_api_key
    assert system_api_key_cached() is None
    with SessionLocal() as db:
        assert _bg_api_key(db) == _any_api_key(db)

    tenant_route, _ = _mock_cp(tenant="tsys", project="psys", key="vgk_sys")
    key = await ensure_system_project()
    assert key == "vgk_sys"
    assert system_api_key_cached() == "vgk_sys"

    # idempotent: a second call serves the cached key without minting another tenant
    assert await ensure_system_project() == "vgk_sys"
    assert tenant_route.call_count == 1

    # background feeds now prefer the stable system key over the arbitrary user's
    with SessionLocal() as db:
        assert _bg_api_key(db) == "vgk_sys"


@respx.mock
async def test_system_project_activates_premium_connectors(monkeypatch):
    """어닝 레이더·한국 수급이 실제로 생성되려면 시스템 피드 키가 fmp·kis(프리미엄)까지 활성화해야
    한다 — FREE만 활성이던 버그가 어닝 레이더를 통째로 0장으로 만들었다."""
    import json as _json

    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    from studioapi.provision import _SYSTEM_STATE_KEY, ensure_system_project

    with SessionLocal() as db:
        db.query(ServiceState).filter(ServiceState.key == _SYSTEM_STATE_KEY).delete()
        db.commit()

    _, activations = _mock_cp(tenant="tsys2", project="psys2", key="vgk_sys2")
    key = await ensure_system_project()
    assert key == "vgk_sys2"
    activated = {_json.loads(c.request.content)["connector_id"] for c in activations.calls}
    assert {"fmp", "kis"} <= activated                     # 프리미엄 포함 (어닝·수급의 데이터원)
    assert {"sec_edgar", "market_history", "google_news"} <= activated   # free도 함께


@respx.mock
async def test_existing_system_project_backfills_premium(monkeypatch):
    """이미 프로비저닝된(캐시된) 시스템 프로젝트도 재시작 시 fmp·kis를 백필로 활성화한다 —
    프리미엄 활성화 이전에 만들어진 배포가 자가치유되도록(수동 admin 단계 없이)."""
    import json as _json

    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    import studioapi.provision as P

    # 캐시된 시스템 프로젝트가 이미 있는 상태(프리미엄 미활성으로 예전에 만들어짐)를 재현
    with SessionLocal() as db:
        db.merge(ServiceState(key=P._SYSTEM_STATE_KEY,
                              value=_json.dumps({"tenant_id": "told", "project_id": "pold",
                                                 "api_key": "vgk_old"})))
        db.commit()
    P._system_premium_done = False   # 새 프로세스 부팅 흉내

    activations = respx.post("http://cp.test/admin/projects/pold/activations").mock(
        return_value=httpx.Response(200, json={}))
    key = await P.ensure_system_project()               # 캐시 히트 → 백필만
    assert key == "vgk_old"
    backfilled = {_json.loads(c.request.content)["connector_id"] for c in activations.calls}
    assert {"fmp", "kis"} <= backfilled                 # 프리미엄이 백필됨
    # 한 프로세스에서 두 번째 호출은 재활성화하지 않는다(프로세스당 1회)
    before = activations.call_count
    await P.ensure_system_project()
    assert activations.call_count == before


async def test_me5_provision_degrades_when_control_plane_down(monkeypatch):
    """control-plane 미기동이면 None을 돌려주고 폴백에 맡긴다 — 부팅을 막지 않는다."""
    monkeypatch.setattr(settings, "control_plane_url", "http://127.0.0.1:1")  # nothing listening
    from studioapi.provision import _SYSTEM_STATE_KEY, ensure_system_project
    with SessionLocal() as db:
        db.query(ServiceState).filter(ServiceState.key == _SYSTEM_STATE_KEY).delete()
        db.commit()
    assert await ensure_system_project() is None


# --- ME-4: guest GC + JOIN-based IP count ------------------------------------------------------
def _reset_guests():
    with SessionLocal() as db:
        db.query(GuestSession).delete()
        db.query(User).filter(User.email.like("guest_%@guest.local")).delete(synchronize_session=False)
        db.query(TurnUsage).filter(TurnUsage.user_email.like("guest_%@guest.local")).delete(
            synchronize_session=False)
        db.commit()


def test_me4_cleanup_removes_only_stale_unclaimed(monkeypatch):
    monkeypatch.setattr(settings, "guest_session_ttl_days", 30)
    _reset_guests()
    old = datetime.utcnow() - timedelta(days=40)
    recent = datetime.utcnow() - timedelta(days=1)
    s_stale, s_claimed, s_recent = "s" * 32, "c" * 32, "r" * 32
    with SessionLocal() as db:
        # stale + unclaimed + no dependents → GC'd (row + guest User)
        db.add(GuestSession(id=s_stale, ip_hash=None, created_at=old))
        db.merge(User(email=guest_email(s_stale), tenant_id="t", project_id="p", api_key="k", plan="guest"))
        # stale but CLAIMED (converted to a real account) → kept
        db.add(GuestSession(id=s_claimed, claimed_by="real@u.com", created_at=old))
        # recent unclaimed → kept (still inside TTL)
        db.add(GuestSession(id=s_recent, created_at=recent))
        db.merge(User(email=guest_email(s_recent), tenant_id="t", project_id="p", api_key="k", plan="guest"))
        db.commit()

    assert cleanup_stale_guests() == 1
    with SessionLocal() as db:
        assert db.get(GuestSession, s_stale) is None
        assert db.get(User, guest_email(s_stale)) is None
        assert db.get(GuestSession, s_claimed) is not None
        assert db.get(GuestSession, s_recent) is not None
        assert db.get(User, guest_email(s_recent)) is not None


def test_me4_ip_turn_count_joins_siblings():
    from studioapi.quotas import _guest_ip_turns_today

    _reset_guests()
    a, b = "a" * 32, "b" * 32
    with SessionLocal() as db:
        db.add(GuestSession(id=a, ip_hash="HASH"))
        db.add(GuestSession(id=b, ip_hash="HASH"))   # same IP → sibling
        db.add(TurnUsage(user_email=guest_email(a), day="2026-07-13", month="2026-07"))
        db.add(TurnUsage(user_email=guest_email(b), day="2026-07-13", month="2026-07"))
        db.add(TurnUsage(user_email=guest_email(a), day="2026-07-12", month="2026-07"))  # other day
        db.commit()
        # both siblings' turns for the day are summed via the JOIN (no per-email IN list)
        assert _guest_ip_turns_today(db, guest_email(a), "2026-07-13") == 2
        # a session with no ip_hash → backstop not applicable
        db.add(GuestSession(id="n" * 32, ip_hash=None))
        db.commit()
        assert _guest_ip_turns_today(db, guest_email("n" * 32), "2026-07-13") is None
