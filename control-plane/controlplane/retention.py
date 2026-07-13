"""HI-13 / SC-3.2 — bound the metering tables' growth.

usage_events + audit_log append two rows per proxied gateway call — unbounded, on the same instance
as the OLTP data. This job (daily, one replica via advisory lock) rolls usage_events older than the
retention window into cumulative per-project totals (usage_rollup) so the settings usage aggregate
keeps its lifetime numbers, then DELETEs those raw rows; audit_log rows past their (shorter) window
are dropped outright. Native monthly partitioning is a larger follow-up — this keeps the table bounded
without it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from controlplane.config import settings
from controlplane.db import SessionLocal, engine
from controlplane.models import AuditLog, UsageEvent, UsageRollup

log = logging.getLogger("controlplane.retention")

_RETENTION_LOCK_ID = 0x76675254  # 'vgRT' — one replica runs retention at a time


def run_retention(now: datetime | None = None) -> dict:
    """Roll + drop aged usage_events and drop aged audit_log. Returns counts. No-op advisory lock on
    SQLite (unit tests exercise the roll/drop logic directly)."""
    now = now or datetime.utcnow()
    ue_cutoff = now - timedelta(days=settings.usage_retention_days)
    al_cutoff = now - timedelta(days=settings.audit_retention_days)
    rolled = deleted_usage = deleted_audit = 0
    with SessionLocal() as db:
        is_pg = engine.dialect.name == "postgresql"
        if is_pg and not bool(db.execute(func.pg_try_advisory_lock(_RETENTION_LOCK_ID)).scalar()):
            return {"rolled": 0, "deleted_usage": 0, "deleted_audit": 0}
        try:
            # 1) aggregate the to-be-dropped usage rows into cumulative per-project rollup totals. ts is
            #    server-set at insert, so nothing new lands with ts < cutoff → the SUM and the DELETE
            #    that follows cover exactly the same set.
            aged = db.execute(
                select(UsageEvent.project_id, func.count(),
                       func.coalesce(func.sum(UsageEvent.cost_units), 0))
                .where(UsageEvent.ts < ue_cutoff).group_by(UsageEvent.project_id)
            ).all()
            for project_id, calls, cost in aged:
                row = db.get(UsageRollup, project_id)
                if row is None:
                    row = UsageRollup(project_id=project_id, calls=0, cost_units=0)
                row.calls = (row.calls or 0) + int(calls)
                row.cost_units = (row.cost_units or 0) + int(cost)
                db.merge(row)
                rolled += int(calls)
            # 2) drop the rolled-up raw usage rows + the aged audit rows
            deleted_usage = db.execute(delete(UsageEvent).where(UsageEvent.ts < ue_cutoff)).rowcount or 0
            deleted_audit = db.execute(delete(AuditLog).where(AuditLog.ts < al_cutoff)).rowcount or 0
            db.commit()
            if deleted_usage or deleted_audit:
                log.info("retention: rolled %d usage rows, dropped %d usage + %d audit",
                         rolled, deleted_usage, deleted_audit)
        finally:
            if is_pg:
                db.execute(func.pg_advisory_unlock(_RETENTION_LOCK_ID))
                db.commit()
    return {"rolled": rolled, "deleted_usage": deleted_usage, "deleted_audit": deleted_audit}


async def retention_loop() -> None:
    """Run retention once per interval. Failures never take the service down."""
    while True:
        await asyncio.sleep(settings.retention_interval_seconds)
        try:
            await asyncio.to_thread(run_retention)
        except Exception:  # noqa: BLE001
            log.exception("retention run failed")
