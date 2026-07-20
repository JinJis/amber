"""PLAN-1 — 플랜(티어) 정의: 턴 한도 · 모델 티어 · 커넥터 셋 · 게이트웨이 rate.

플랜은 **리소스 설정**(어떤 모델·몇 스텝·어떤 커넥터)이지 추론 로직이 아니다(인바리언트 #9).
숫자는 전부 ``PLANS_JSON`` env로 오버라이드 가능 — METER 트랙의 유저별 원가 실측을 보고
가격·캡을 조정하는 것이 정석 순서다. 소비(카운트)는 ``quotas.py``, 스펙 병합은
``chat.prepare_turn``(PLAN-3), 커넥터 플립은 ``apply_plan``(PLAN-4)이 담당한다.

Fair-use 철학(업계 표준: ChatGPT식 "강등, 차단 아님"): free는 한도 도달 시 턴이 멈추고
업그레이드 CTA를 보여주지만, pro는 월 fair-use를 넘겨도 flash 티어로 **강등**될 뿐 계속
쓸 수 있다.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# 무료 커넥터 셋 — 신뢰 기반(공시·거시·뉴스·히스토리)은 전부 무료로 열어 제품의 차별점을
# 체감시키고, 실시간·프리미엄 데이터(KIS 수급·FMP 컨센서스/캘린더)가 유료 가치축이 된다.
FREE_CONNECTORS: list[str] = [
    "sec_edgar", "yahoo", "fred", "opendart", "ecos", "google_news",
    "datasets_store", "rag", "market_history",
]
PREMIUM_CONNECTORS: list[str] = ["fmp", "kis"]

# 플랜 기본값. None = 엔진/전역 기본값 사용(제한 없음).
_DEFAULT_PLANS: dict[str, dict] = {
    "guest": {
        "label": "게스트",
        "daily_turns": None,          # 게스트는 평생 캡(guest_sessions.turns_used)으로 관리
        "monthly_turns": None,
        "lifetime_turns": 3,          # GUEST_TURNS_MAX가 오버라이드
        "synthesis_model": "gemini-flash-latest",
        "max_steps": 6,
        "max_subagents": 0,
        "connectors": list(FREE_CONNECTORS),
        "rate_per_minute": 240,       # 공유 게스트 키 전체에 대한 rate
    },
    "free": {
        "label": "Free",
        "daily_turns": 5,
        "monthly_turns": 80,
        "synthesis_model": "gemini-flash-latest",
        "max_steps": 8,
        "max_subagents": 1,
        "connectors": list(FREE_CONNECTORS),
        "rate_per_minute": 60,
    },
    "pro": {
        "label": "Pro",
        "daily_turns": None,
        "monthly_turns": 200,         # pro-턴 fair-use — 초과 시 flash 강등(차단 아님)
        "degrade_over_monthly": True,
        "synthesis_model": None,      # None = 엔진 기본(pro 합성)
        "max_steps": None,
        "max_subagents": None,
        "connectors": list(FREE_CONNECTORS) + list(PREMIUM_CONNECTORS),
        # pro는 allowed_tools를 플랜으로 좁히지 않는다 — 카탈로그가 자라도 pro가 조용히
        # 뒤처지지 않게. 하드 방어는 어차피 게이트웨이 엔타이틀먼트(403)가 담당.
        "restrict_tools": False,
        "rate_per_minute": 240,
    },
}


def _apply_env_shortcuts(plans: dict[str, dict]) -> dict[str, dict]:
    """자주 만지는 단일 값의 env 숏컷 — GUEST_TURNS_MAX (전체 오버라이드는 PLANS_JSON)."""
    gmax = os.environ.get("GUEST_TURNS_MAX", "")
    if gmax.isdigit():
        plans["guest"]["lifetime_turns"] = int(gmax)
    return plans


def _plans() -> dict[str, dict]:
    """PLANS_JSON env를 플랜 단위로 얕게 병합 — 운영 중 캡/모델을 코드 배포 없이 조정한다."""
    merged = {k: dict(v) for k, v in _DEFAULT_PLANS.items()}
    raw = os.environ.get("PLANS_JSON", "")
    if raw:
        try:
            for name, patch in (json.loads(raw) or {}).items():
                merged.setdefault(name, {}).update(patch or {})
        except Exception as exc:  # noqa: BLE001 — 잘못된 오버라이드가 서비스를 죽이면 안 됨
            logger.error("PLANS_JSON invalid (%s) — using defaults", exc)
    return _apply_env_shortcuts(merged)


def limits(plan: str) -> dict:
    """플랜 이름 → 한도 dict. 모르는 플랜은 free로 정규화(과소 권한이 안전한 방향)."""
    plans = _plans()
    return plans.get((plan or "free").lower()) or plans["free"]


def plan_of(user) -> str:
    """User 행 → 정규화된 플랜 이름."""
    p = (getattr(user, "plan", None) or "free").lower()
    return p if p in _plans() else "free"


async def apply_plan(user_email: str, plan: str) -> bool:
    """PLAN-4: 플랜 전환(free↔pro·게스트 승격·결제 상태 변화)의 **단일 경로**.

    순서 고정: ① 커넥터 활성화 토글 → ② 게이트웨이 rate 티어 → ③ users.plan.
    중간에 죽으면 '과소 권한'으로만 남는다(결제했는데 권한이 없으면 재시도로 복구,
    결제 안 했는데 권한이 열리는 일은 없음). 멱등 — 몇 번을 불러도 같은 결과."""
    from datetime import datetime

    from studioapi.db import SessionLocal
    from studioapi.models import User
    from studioapi.provision import _admin

    plan = (plan or "free").lower()
    lim = limits(plan)
    with SessionLocal() as db:
        u = db.get(User, user_email)
        if u is None:
            return False
        project_id = u.project_id
    if not project_id:
        # PROV-1: a claim row (provisioning still in flight, or its provisioner died). Writing the plan
        # here would mark the user upgraded while every activation call 404s against /projects/None —
        # entitlements silently missing on a paid plan. Refuse; the caller retries once provisioning
        # settles (ensure_user finishes it on the user's very next request).
        logger.error("apply_plan(%s→%s): account not provisioned yet — refusing", user_email, plan)
        return False
    want = set(lim.get("connectors") or FREE_CONNECTORS)
    ok = True
    for cid in sorted(set(FREE_CONNECTORS) | set(PREMIUM_CONNECTORS)):
        try:
            await _admin("POST", f"/admin/projects/{project_id}/activations",
                         {"connector_id": cid, "enabled": cid in want})
        except Exception as exc:  # noqa: BLE001 — 일부 실패해도 계속; 요란하게 남긴다
            ok = False
            logger.error("apply_plan(%s→%s): activation %s=%s failed: %s",
                         user_email, plan, cid, cid in want, exc)
    try:
        await _admin("PATCH", f"/admin/projects/{project_id}", {"plan": plan})
    except Exception as exc:  # noqa: BLE001
        ok = False
        logger.error("apply_plan(%s→%s): rate tier patch failed: %s", user_email, plan, exc)
    with SessionLocal() as db:
        u = db.get(User, user_email)
        if u is not None:
            u.plan = plan
            u.plan_updated_at = datetime.utcnow()
            db.commit()
    if not ok:
        logger.error("apply_plan(%s→%s): completed WITH ERRORS — entitlements may be under-provisioned",
                     user_email, plan)
    return ok


def spec_overrides(plan: str, degraded: bool = False) -> dict:
    """PLAN-3: 이 턴의 AgentSpec에 병합할 플랜 티어 필드들.

    degraded(pro fair-use 초과)는 **리소스 티어만** free로 강등한다 — 커넥터 접근(엔타이틀먼트)
    은 결제한 플랜 그대로. 반환 키: synthesis_model / max_steps / max_subagents /
    allowed_connectors(제한 플랜만)."""
    lim = limits(plan)
    tier = limits("free") if degraded else lim
    out: dict = {}
    if tier.get("synthesis_model"):
        out["synthesis_model"] = tier["synthesis_model"]
    if tier.get("max_steps") is not None:
        out["max_steps"] = int(tier["max_steps"])
    if tier.get("max_subagents") is not None:
        out["max_subagents"] = int(tier["max_subagents"])
    if lim.get("restrict_tools", True) and lim.get("connectors"):
        out["allowed_connectors"] = list(lim["connectors"])
    return out
