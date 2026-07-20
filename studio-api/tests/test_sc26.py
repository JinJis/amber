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


def _mock_cp(project="p1", key="vgk_1"):
    """Mock the control-plane provisioning chain. Returns (provision_route, activations_route, patch_route).

    PROV-1: signup/singleton provisioning is now ONE idempotent call (`/admin/provision`); the legacy
    per-step routes stay mocked because the ME-2 reconcile backfill and apply_plan still use them."""
    project_route = respx.post("http://cp.test/admin/provision").mock(
        return_value=httpx.Response(200, json={"project_id": project, "api_key": key}))
    respx.post("http://cp.test/admin/projects").mock(
        return_value=httpx.Response(200, json={"id": project}))
    respx.post(f"http://cp.test/admin/projects/{project}/keys").mock(
        return_value=httpx.Response(200, json={"api_key": key}))
    activations = respx.post(f"http://cp.test/admin/projects/{project}/activations").mock(
        return_value=httpx.Response(200, json={}))
    patch_route = respx.patch(f"http://cp.test/admin/projects/{project}").mock(  # SYS-1: internal marking
        return_value=httpx.Response(200, json={"id": project, "internal": True}))
    return project_route, activations, patch_route


# --- ME-2: reconcile version column ------------------------------------------------------------
@respx.mock
async def test_me2_reconcile_fires_once_per_version(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "connectors_reconcile_ver", "1")
    monkeypatch.setattr(settings, "plan_enforce_connectors", False)
    _, activations, _ = _mock_cp()
    from studioapi.provision import ensure_user

    with SessionLocal() as db:
        db.query(User).filter(User.email == "me2@u.com").delete()
        db.commit()

    # first sign-up: freshly activated → stamped at the current version. PROV-1: the activations come
    # with the single provisioning call, so the per-connector backfill route stays untouched here.
    u = await ensure_user("me2@u.com")
    assert u.connectors_reconciled_ver == "1"
    after_signup = activations.call_count
    assert after_signup == 0

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
        db.merge(User(email="rand@u.com", project_id="p", api_key="vgk_rand"))
        db.commit()

    # before provisioning: no cached key, and background feeds fall back to an arbitrary user's key
    from studioapi.askfeed import _any_api_key, _bg_api_key
    assert system_api_key_cached() is None
    with SessionLocal() as db:
        assert _bg_api_key(db) == _any_api_key(db)

    import studioapi.provision as _P
    _P._system_backfill_done = False
    project_route, _, _ = _mock_cp(project="psys", key="vgk_sys")
    key = await ensure_system_project()
    assert key == "vgk_sys"
    assert system_api_key_cached() == "vgk_sys"

    # idempotent: a second call serves the cached key without minting another project
    assert await ensure_system_project() == "vgk_sys"
    assert project_route.call_count == 1

    # background feeds now prefer the stable system key over the arbitrary user's
    with SessionLocal() as db:
        assert _bg_api_key(db) == "vgk_sys"


@respx.mock
async def test_system_project_marked_internal(monkeypatch):
    """SYS-1: 시스템 피드 프로젝트는 커머셜 테넌트가 아니라 플랫폼 인프라 — activation 목록을 만들지
    않고 INTERNAL로 표시된다(게이트웨이가 카탈로그 전체를 엔타이틀). 어닝 레이더가 0장이던 근본을 제거."""
    import json as _json

    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    import studioapi.provision as P

    with SessionLocal() as db:
        db.query(ServiceState).filter(ServiceState.key == P._SYSTEM_STATE_KEY).delete()
        db.commit()
    P._system_backfill_done = False

    provision_route, activations, _ = _mock_cp(project="psys2", key="vgk_sys2")
    key = await P.ensure_system_project()
    assert key == "vgk_sys2"
    # 커머셜 activation 목록을 만들지 않는다 — 엔타이틀은 게이트웨이의 internal 예외가 담당
    assert activations.call_count == 0
    # PROV-1: 프로젝트·키·internal 표시가 멱등 호출 하나로 끝난다(중간에 죽어도 재호출이 같은 계정)
    body = _json.loads(provision_route.calls.last.request.content)
    assert body["owner_ref"] == "system" and body["internal"] is True and body["connectors"] == []


@respx.mock
async def test_existing_system_project_backfilled_internal(monkeypatch):
    """SYS-1: internal 클래스 이전에 만들어진 캐시된 시스템 프로젝트도 재시작 시 internal로 백필된다 —
    수동 admin 없이 자가치유. 프로세스당 1회."""
    import json as _json

    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    import studioapi.provision as P

    # 캐시된 시스템 프로젝트가 이미 있는 상태(예전에 FREE만 활성으로 만들어짐)를 재현
    with SessionLocal() as db:
        db.merge(ServiceState(key=P._SYSTEM_STATE_KEY,
                              value=_json.dumps({"tenant_id": "told", "project_id": "pold",
                                                 "api_key": "vgk_old"})))
        db.commit()
    P._system_backfill_done = False   # 새 프로세스 부팅 흉내

    patch_route = respx.patch("http://cp.test/admin/projects/pold").mock(
        return_value=httpx.Response(200, json={"internal": True}))
    key = await P.ensure_system_project()               # 캐시 히트 → 백필만
    assert key == "vgk_old"
    assert patch_route.called
    assert _json.loads(patch_route.calls.last.request.content) == {"internal": True}
    # 한 프로세스에서 두 번째 호출은 다시 PATCH 하지 않는다(프로세스당 1회)
    before = patch_route.call_count
    await P.ensure_system_project()
    assert patch_route.call_count == before


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
        db.merge(User(email=guest_email(s_stale), project_id="p", api_key="k", plan="guest"))
        # stale but CLAIMED (converted to a real account) → kept
        db.add(GuestSession(id=s_claimed, claimed_by="real@u.com", created_at=old))
        # recent unclaimed → kept (still inside TTL)
        db.add(GuestSession(id=s_recent, created_at=recent))
        db.merge(User(email=guest_email(s_recent), project_id="p", api_key="k", plan="guest"))
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
