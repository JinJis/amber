"""PLAN-1/2/3 — 플랜 정의·쿼터 게이트·스펙 티어링 테스트."""

from __future__ import annotations

import asyncio

import pytest

from studioapi import plans, quotas
from studioapi.chat import _merge_plan_spec
from studioapi.db import SessionLocal, init_db
from studioapi.models import TurnUsage, User


def setup_module(_module):
    init_db()


def _user(email: str, plan: str = "free") -> User:
    with SessionLocal() as db:
        u = db.get(User, email)
        if u is None:
            u = User(email=email, project_id="p", api_key="k", plan=plan)
            db.add(u)
        else:
            u.plan = plan
        db.commit()
        db.refresh(u)
        return u


# --- PLAN-1: 플랜 정의 -------------------------------------------------------
def test_plan_limits_and_json_override(monkeypatch):
    assert plans.limits("free")["daily_turns"] == 5
    assert plans.limits("pro")["degrade_over_monthly"] is True
    assert plans.limits("이상한플랜")["daily_turns"] == 5   # 모르는 플랜 → free 정규화
    monkeypatch.setenv("PLANS_JSON", '{"free": {"daily_turns": 99}}')
    assert plans.limits("free")["daily_turns"] == 99        # env 오버라이드
    assert plans.limits("free")["monthly_turns"] == 80      # 나머지 키는 기본값 유지
    monkeypatch.setenv("PLANS_JSON", "not-json")
    assert plans.limits("free")["daily_turns"] == 5         # 깨진 오버라이드 → 기본값 (서비스 생존)


def test_spec_overrides_by_tier():
    free = plans.spec_overrides("free")
    assert free["synthesis_model"].startswith("gemini-flash")
    assert free["max_steps"] == 8 and free["max_subagents"] == 1
    assert "kis" not in free["allowed_connectors"] and "sec_edgar" in free["allowed_connectors"]
    pro = plans.spec_overrides("pro")
    assert "synthesis_model" not in pro          # None = 엔진 기본(pro 합성)
    assert "allowed_connectors" not in pro       # pro는 툴을 플랜으로 좁히지 않음
    # degraded: 리소스는 free 티어로, 커넥터 접근은 결제 플랜 그대로
    deg = plans.spec_overrides("pro", degraded=True)
    assert deg["synthesis_model"].startswith("gemini-flash") and deg["max_steps"] == 8
    assert "allowed_connectors" not in deg


# --- PLAN-3: 턴 스펙 병합 ----------------------------------------------------
def test_merge_plan_spec_narrows_only():
    u = _user("merge@u.com", "free")
    # 에이전트가 스텝 12 + 프리미엄 툴을 원해도 free 플랜이 좁힌다
    spec = {"system": "s", "max_steps": 12, "allowed_tools": ["kis__investor_flow", "yahoo__prices"]}
    out = _merge_plan_spec(spec, u, degraded=False)
    assert out["max_steps"] == 8
    assert out["allowed_tools"] == ["yahoo__prices"]         # 플랜 밖 kis 툴 제거
    assert out["synthesis_model"].startswith("gemini-flash")
    # 에이전트 제한이 전부 플랜 밖이면 → 플랜 셋으로 폴백(턴이 죽지 않게)
    out2 = _merge_plan_spec({"allowed_tools": ["kis__investor_flow"]}, u, degraded=False)
    assert out2["allowed_tools"] == plans.limits("free")["connectors"]
    # pro는 스펙을 건드리지 않는다 (엔진 기본 = pro 합성·풀 스텝)
    p = _user("mergepro@u.com", "pro")
    out3 = _merge_plan_spec({"max_steps": 12}, p, degraded=False)
    assert out3.get("synthesis_model") is None and out3["max_steps"] == 12


# --- PLAN-2: 쿼터 게이트 -----------------------------------------------------
def test_quota_daily_block_and_monthly(monkeypatch):
    monkeypatch.setenv("PLANS_JSON", '{"free": {"daily_turns": 2, "monthly_turns": 3}}')
    u = _user("quota@u.com", "free")
    assert quotas.check_and_consume(u).mode == "ok"
    assert quotas.check_and_consume(u).mode == "ok"
    v = quotas.check_and_consume(u)                # 3번째 → 일일 한도
    assert v.mode == "blocked" and v.scope == "daily" and "내일 0시" in v.message
    assert v.reset_at                              # 충전 시점 명시
    # blocked는 소비하지 않는다 — TurnUsage는 딱 2행
    with SessionLocal() as db:
        n = db.query(TurnUsage).filter(TurnUsage.user_email == u.email).count()
    assert n == 2


def test_quota_pro_degrades_over_monthly(monkeypatch):
    monkeypatch.setenv("PLANS_JSON", '{"pro": {"monthly_turns": 1}}')
    u = _user("fairuse@u.com", "pro")
    assert quotas.check_and_consume(u).mode == "ok"
    v = quotas.check_and_consume(u)                # fair-use 초과 → 강등, 차단 아님
    assert v.mode == "degraded" and v.scope == "fair_use" and "표준 모델" in v.message
    # degraded 턴도 소비는 기록된다 (원가 추적)
    with SessionLocal() as db:
        n = db.query(TurnUsage).filter(TurnUsage.user_email == u.email).count()
    assert n == 2


def test_quota_guest_lifetime(monkeypatch):
    monkeypatch.setenv("PLANS_JSON", '{"guest": {"lifetime_turns": 1}}')
    u = _user("guest_x@guest.local", "guest")
    assert quotas.check_and_consume(u).mode == "ok"
    v = quotas.check_and_consume(u)
    assert v.mode == "blocked" and v.scope == "guest" and "가입하면" in v.message


def test_start_turn_blocked_emits_quota_run(monkeypatch):
    """한도 초과 시: 실제 턴/대화 생성 없이 합성 Run이 quota + done만 흘린다."""
    from studioapi.chat import start_turn
    from studioapi.models import Conversation

    monkeypatch.setenv("PLANS_JSON", '{"free": {"daily_turns": 0}}')
    u = _user("blocked@u.com", "free")

    async def _run():
        # start_turn은 항상 async 컨텍스트(FastAPI 핸들러)에서 불린다 — 테스트도 동일하게
        run = start_turn(u, None, [{"role": "user", "content": "질문"}], None)
        evs = []
        from studioapi.runs import manager
        async for ev in manager.tail(run, 0):
            evs.append(ev)
        return evs

    evs = asyncio.run(_run())
    types = [e["type"] for e in evs]
    assert "quota" in types and "done" in types and "token" not in types
    q = next(e for e in evs if e["type"] == "quota")
    assert q["mode"] == "blocked" and q["plan"] == "free"
    d = next(e for e in evs if e["type"] == "done")
    assert d.get("quota") is True
    # 대화·유저 메시지 미생성 (쿼터에 막힌 질문이 히스토리를 오염시키지 않음)
    with SessionLocal() as db:
        assert db.query(Conversation).filter(Conversation.user_email == u.email).count() == 0


def test_apply_plan_flips_activations_and_rate_tier(monkeypatch):
    """PLAN-4: free↔pro 전환 — 프리미엄 커넥터 토글 + rate 티어 PATCH + users.plan (순서 고정)."""
    import httpx
    import respx

    from studioapi import plans as plans_mod
    from studioapi.config import settings

    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    u = _user("flip@u.com", "free")
    with SessionLocal() as db:
        db.get(User, u.email).project_id = "prjF"
        db.commit()

    acts: list[dict] = []
    patches: list[dict] = []

    def _act(request):
        import json as _json
        acts.append(_json.loads(request.content))
        return httpx.Response(200, json={})

    def _patch(request):
        import json as _json
        patches.append(_json.loads(request.content))
        return httpx.Response(200, json={})

    with respx.mock:
        respx.post("http://cp.test/admin/projects/prjF/activations").mock(side_effect=_act)
        respx.patch("http://cp.test/admin/projects/prjF").mock(side_effect=_patch)
        assert asyncio.run(plans_mod.apply_plan(u.email, "pro")) is True
        by_id = {a["connector_id"]: a["enabled"] for a in acts}
        assert by_id["kis"] is True and by_id["fmp"] is True and by_id["yahoo"] is True
        assert patches[-1]["plan"] == "pro"
        with SessionLocal() as db:
            assert db.get(User, u.email).plan == "pro"

        acts.clear()
        assert asyncio.run(plans_mod.apply_plan(u.email, "free")) is True
        by_id = {a["connector_id"]: a["enabled"] for a in acts}
        assert by_id["kis"] is False and by_id["fmp"] is False and by_id["yahoo"] is True
        with SessionLocal() as db:
            assert db.get(User, u.email).plan == "free"


def test_usage_snapshot_reads_without_consuming(monkeypatch):
    monkeypatch.setenv("PLANS_JSON", '{"free": {"daily_turns": 5, "monthly_turns": 80}}')
    from studioapi.quotas import usage_snapshot
    u = _user("snap@u.com", "free")
    quotas.check_and_consume(u)
    s1 = usage_snapshot(u)
    s2 = usage_snapshot(u)
    assert s1["daily_used"] == s2["daily_used"] == 1     # 읽기만 — 소비 없음
    assert s1["daily_limit"] == 5 and s1["monthly_limit"] == 80 and s1["plan"] == "free"


def test_referral_code_and_attribution():
    """REF-1(코어): 코드 lazy 발급(안정) · 가입 귀속 1회 · 자기추천/미존재 코드 무시."""
    from studioapi.referrals import attribute_signup, ensure_referral_code

    a = _user("ref_a@u.com", "free")
    b = _user("ref_b@u.com", "free")
    code = ensure_referral_code(a.email)
    assert code and code == ensure_referral_code(a.email) and len(code) == 8
    assert ensure_referral_code("guest_x@guest.local") is None   # 게스트는 코드 없음
    assert attribute_signup(b.email, "NOPE1234") is False        # 미존재 코드
    assert attribute_signup(a.email, code) is False              # 자기추천
    assert attribute_signup(b.email, code.lower()) is True       # 대소문자 무관 귀속
    with SessionLocal() as db:
        assert db.get(User, b.email).referred_by == a.email
    assert attribute_signup(b.email, code) is False              # 1회만
