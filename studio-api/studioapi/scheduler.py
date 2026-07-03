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

from sqlalchemy import select

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


def start(task_holder: list) -> None:
    """Start the loop if enabled, appending the task to ``task_holder`` for shutdown."""
    if not settings.alerts_scheduler_enabled:
        log.info("alerts scheduler disabled")
        return
    task_holder.append(asyncio.create_task(_loop()))
    log.info("alerts scheduler started (tick=%ss)", settings.alerts_tick_seconds)
