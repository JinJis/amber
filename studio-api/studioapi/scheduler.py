"""Background scheduler for notification alerts (F3).

A single asyncio loop ticks every ``settings.alerts_tick_seconds``, finds active alerts whose
``next_fire_at`` is due (and not inside quiet hours), and fires them via
:func:`studioapi.alerts.fire_alert`. Lives in studio-api (where alerts, deliveries, and the user's
channel credentials all are). Disabled in tests / when ``ALERTS_SCHEDULER_ENABLED=false`` so the
suite stays deterministic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from sqlalchemy import func, select

from studioapi.alerts import fire_alert
from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.models import NotificationAlert

log = logging.getLogger("studioapi.scheduler")


def _in_quiet_hours(quiet: dict | None, now: datetime) -> bool:
    """True if ``now`` is in the quiet window {start:'HH:MM', end:'HH:MM'} (wraps midnight)."""
    if not quiet:
        return False
    try:
        sh, sm = (int(x) for x in str(quiet.get("start", "22:00")).split(":", 1))
        eh, em = (int(x) for x in str(quiet.get("end", "07:00")).split(":", 1))
    except (ValueError, AttributeError):
        return False
    cur, start, end = now.hour * 60 + now.minute, sh * 60 + sm, eh * 60 + em
    return (start <= cur or cur < end) if start > end else (start <= cur < end)


def _due(a: NotificationAlert, now: datetime) -> bool:
    if a.status != "active" or a.next_fire_at is None or a.next_fire_at > now:
        return False
    return not _in_quiet_hours(json.loads(a.quiet_hours) if a.quiet_hours else None, now)


def tick(now: datetime | None = None) -> int:
    """One scheduler pass: fire every due alert. Returns how many fired. Synchronous + isolated so
    it's unit-testable without the loop."""
    now = now or datetime.utcnow()
    fired = 0
    with SessionLocal() as db:
        # CR-3: hold an advisory lock for the WHOLE tick so only ONE replica fires each pass —
        # otherwise every replica selects the same due alerts and delivers N× (user-visible spam).
        # SQLite / single-process is a no-op. Held across the fire loop on this one session.
        is_pg = db.bind.dialect.name == "postgresql"
        if is_pg and not bool(db.execute(func.pg_try_advisory_lock(0x76674131)).scalar()):  # 'vgA1'
            return 0
        try:
            alerts = db.execute(
                select(NotificationAlert).where(NotificationAlert.status == "active")
            ).scalars().all()
            for a in alerts:
                if not _due(a, now):
                    continue
                try:
                    fire_alert(a, db, now=now)
                    fired += 1
                except Exception:  # one bad alert must not stall the loop
                    log.exception("alert %s failed to fire", a.id)
            if fired:
                db.commit()
        finally:
            if is_pg:
                db.execute(func.pg_advisory_unlock(0x76674131))
    return fired


async def _loop() -> None:
    while True:
        try:
            # tick() now does blocking, network-bound work (it re-fetches each due alert's widget
            # through the gateway), so run it off the event loop.
            n = await asyncio.to_thread(tick)
            if n:
                log.info("scheduler fired %d alert(s)", n)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(settings.alerts_tick_seconds)


async def _billing_loop() -> None:
    """BILL-3: 시간별 정기결제·던닝 틱 — billing_tick 내부의 advisory lock이 복수 레플리카
    이중과금을 막는다. 실패해도 루프는 계속(다음 시간에 재시도)."""
    from studioapi.billing import billing_tick

    while True:
        try:
            n = await billing_tick()
            if n:
                log.info("billing tick: %d subscription/invoice(s) processed", n)
        except Exception:  # noqa: BLE001
            log.exception("billing tick failed — retrying next hour")
        await asyncio.sleep(3600)


def start(task_holder: list) -> None:
    """Start the loop only when the 알림봇 feature is on AND the scheduler switch is enabled, appending
    the task to ``task_holder`` for shutdown. Chat-first (FLAG-1): feature_alerts defaults off, so no
    scheduler ticks unless an operator explicitly turns the alert surface on."""
    if settings.billing_enabled:   # BILL-3: 결제 틱은 알림봇 플래그와 무관하게 자체 게이트
        task_holder.append(asyncio.create_task(_billing_loop()))
        log.info("billing scheduler started (hourly)")
    if not (settings.feature_alerts and settings.alerts_scheduler_enabled):
        log.info("alerts scheduler disabled (feature_alerts=%s, enabled=%s)",
                 settings.feature_alerts, settings.alerts_scheduler_enabled)
        return
    task_holder.append(asyncio.create_task(_loop()))
    log.info("alerts scheduler started (tick=%ss)", settings.alerts_tick_seconds)
