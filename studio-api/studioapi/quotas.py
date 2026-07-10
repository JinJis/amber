"""PLAN-2 — 서버사이드 턴 쿼터: 답변을 시작하기 전에 딱 한 곳(start_turn)에서 판정한다.

판정은 DB(TurnUsage) 기반 — 프로세스 로컬 상태(runs.py)는 재시작에 날아가므로 부적합.
결과는 셋 중 하나:
  ok        그대로 진행 (TurnUsage 1행 소비)
  degraded  진행하되 flash 티어로 강등 (pro fair-use 초과 — 차단이 아니라 강등, 업계 표준)
  blocked   턴을 시작하지 않음 (합성 Run이 quota SSE 이벤트만 흘리고 끝남)

일/월 경계는 KST — "내일 0시에 충전돼요"가 유저의 자정과 일치해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from studioapi import plans
from studioapi.db import SessionLocal
from studioapi.models import TurnUsage, User

KST = ZoneInfo("Asia/Seoul")


@dataclass
class QuotaVerdict:
    mode: str                     # ok | degraded | blocked
    scope: str = ""               # daily | monthly | guest | fair_use
    plan: str = "free"
    used: int = 0
    limit: int | None = None
    reset_at: str | None = None   # ISO(KST) — 다음 충전 시점
    message: str = ""
    extra: dict = field(default_factory=dict)

    def event(self) -> dict:
        """웹이 그대로 렌더하는 quota SSE 이벤트 페이로드."""
        return {"type": "quota", "mode": self.mode, "scope": self.scope, "plan": self.plan,
                "used": self.used, "limit": self.limit, "reset_at": self.reset_at,
                "message": self.message, **self.extra}


def _tomorrow_midnight(now: datetime) -> str:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _next_month_first(now: datetime) -> str:
    y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return now.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _guest_ip_turns_today(db, email: str, day: str) -> int | None:
    """같은 IP의 게스트 세션들이 오늘 소비한 턴 합계 — 쿠키를 지워 새 세션을 파는 우회를 막는
    어뷰즈 백스톱. IP를 모르는 세션(ip_hash 없음)은 None(백스톱 미적용)."""
    from studioapi.guest import gid_of, guest_email
    from studioapi.models import GuestSession

    gid = gid_of(email)
    if not gid:
        return None
    sess = db.get(GuestSession, gid)
    if sess is None or not sess.ip_hash:
        return None
    sibling_ids = db.execute(select(GuestSession.id).where(
        GuestSession.ip_hash == sess.ip_hash)).scalars().all()
    emails = [guest_email(s) for s in sibling_ids]
    return db.execute(select(func.count()).select_from(TurnUsage).where(
        TurnUsage.user_email.in_(emails), TurnUsage.day == day)).scalar() or 0


def _daily_limit(user: User, lim: dict, now: datetime) -> int | None:
    """플랜 일일 한도 + REF-2 가입 보너스(+N턴/일, 만료일까지)."""
    base = lim.get("daily_turns")
    if base is None:
        return None
    bonus_until = getattr(user, "bonus_turns_until", None)
    if bonus_until is not None and getattr(user, "bonus_daily_turns", 0):
        # naive datetime(DB) vs KST now — DB는 UTC server_default이므로 UTC로 비교
        if datetime.utcnow() <= bonus_until:
            return base + int(user.bonus_daily_turns)
    return base


def check_and_consume(user: User, conversation_id: str | None = None) -> QuotaVerdict:
    """한도 판정 + 허용 시 같은 트랜잭션에서 TurnUsage 소비. blocked면 아무것도 쓰지 않는다."""
    plan = plans.plan_of(user)
    lim = plans.limits(plan)
    now = datetime.now(KST)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    with SessionLocal() as db:
        daily_used = db.execute(select(func.count()).select_from(TurnUsage).where(
            TurnUsage.user_email == user.email, TurnUsage.day == day)).scalar() or 0
        monthly_used = db.execute(select(func.count()).select_from(TurnUsage).where(
            TurnUsage.user_email == user.email, TurnUsage.month == month)).scalar() or 0

        # 게스트: 평생 캡(디바이스당) — 일/월이 아니라 총 사용량으로 판정 (GUEST-1)
        lifetime_limit = lim.get("lifetime_turns")
        if lifetime_limit is not None:
            from studioapi.config import settings as _settings
            total_used = db.execute(select(func.count()).select_from(TurnUsage).where(
                TurnUsage.user_email == user.email)).scalar() or 0
            if total_used >= lifetime_limit:
                return QuotaVerdict(
                    mode="blocked", scope="guest", plan=plan, used=total_used, limit=lifetime_limit,
                    message=(f"게스트 체험 {lifetime_limit}회를 모두 사용했어요. 가입하면 지금까지 "
                             "나눈 대화 그대로, 바로 이어갈 수 있어요."))
            # 어뷰즈 백스톱: 같은 IP의 게스트 세션들이 오늘 쓴 턴 합계 (쿠키 삭제 우회 차단)
            ip_used = _guest_ip_turns_today(db, user.email, day)
            if ip_used is not None and ip_used >= _settings.guest_turns_per_ip_day:
                return QuotaVerdict(
                    mode="blocked", scope="guest", plan=plan, used=ip_used,
                    limit=_settings.guest_turns_per_ip_day, reset_at=_tomorrow_midnight(now),
                    message=("오늘은 이 네트워크의 게스트 체험이 모두 소진됐어요. 가입하면 "
                             "지금 바로 이어갈 수 있어요."))

        daily_limit = _daily_limit(user, lim, now)
        if daily_limit is not None and daily_used >= daily_limit:
            return QuotaVerdict(
                mode="blocked", scope="daily", plan=plan, used=daily_used, limit=daily_limit,
                reset_at=_tomorrow_midnight(now),
                message=(f"오늘 무료 분석 {daily_limit}회를 모두 사용했어요. 내일 0시에 다시 "
                         "충전돼요 — Pro로 업그레이드하면 지금 바로 이어갈 수 있어요."))

        monthly_limit = lim.get("monthly_turns")
        degraded = False
        if monthly_limit is not None and monthly_used >= monthly_limit:
            if lim.get("degrade_over_monthly"):
                degraded = True  # pro fair-use: 강등하고 계속 (아래에서 소비는 기록)
            else:
                return QuotaVerdict(
                    mode="blocked", scope="monthly", plan=plan, used=monthly_used, limit=monthly_limit,
                    reset_at=_next_month_first(now),
                    message=(f"이번 달 무료 분석 {monthly_limit}회를 모두 사용했어요. 다음 달 1일에 "
                             "다시 충전돼요 — Pro로 업그레이드하면 지금 바로 이어갈 수 있어요."))

        db.add(TurnUsage(user_email=user.email, conversation_id=conversation_id, day=day, month=month))
        if lifetime_limit is not None:   # 게스트: 세션 카운터도 동기(빠른 잔여 표시용)
            from studioapi.guest import gid_of
            from studioapi.models import GuestSession
            gid = gid_of(user.email)
            sess = db.get(GuestSession, gid) if gid else None
            if sess is not None:
                sess.turns_used = (sess.turns_used or 0) + 1
        db.commit()

    if degraded:
        return QuotaVerdict(
            mode="degraded", scope="fair_use", plan=plan, used=monthly_used, limit=monthly_limit,
            reset_at=_next_month_first(now),
            message=(f"이번 달 Pro 분석 {monthly_limit}회를 넘어서 지금은 표준 모델로 답해요. "
                     "다음 달 1일에 다시 충전돼요."))
    return QuotaVerdict(mode="ok", plan=plan, used=daily_used + 1, limit=daily_limit)
