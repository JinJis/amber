"""HI-13 / SC-3.2: retention rolls aged usage_events into cumulative per-project totals then drops the
raw rows (+ aged audit rows); the rollup accumulates across runs so lifetime numbers survive."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from controlplane import retention
from controlplane.config import settings
from controlplane.db import SessionLocal, init_db
from controlplane.models import AuditLog, UsageEvent, UsageRollup


def setup_module(_module):
    init_db()


def _reset():
    with SessionLocal() as db:
        db.query(UsageEvent).delete()
        db.query(AuditLog).delete()
        db.query(UsageRollup).delete()
        db.commit()


def _add_usage(project_id: str, cost: int, ts: datetime):
    with SessionLocal() as db:
        db.add(UsageEvent(project_id=project_id, api_key_id="k", method="GET", path="/x",
                          status=200, cost_units=cost, ts=ts))
        db.commit()


def test_retention_rolls_then_drops_aged_rows():
    _reset()
    now = datetime.utcnow()
    old = now - timedelta(days=settings.usage_retention_days + 5)
    recent = now - timedelta(days=1)
    for cost in (1, 5, 0):
        _add_usage("p1", cost, old)      # 3 aged rows, cost sum 6
    _add_usage("p1", 2, recent)          # 1 recent row — kept
    with SessionLocal() as db:
        db.add(AuditLog(project_id="p1", action="access", detail="d",
                        ts=now - timedelta(days=settings.audit_retention_days + 1)))
        db.commit()

    out = retention.run_retention(now)
    assert out == {"rolled": 3, "deleted_usage": 3, "deleted_audit": 1}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageEvent)) == 1   # only the recent row
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 0
        roll = db.get(UsageRollup, "p1")
        assert roll.calls == 3 and roll.cost_units == 6


def test_rollup_accumulates_across_runs():
    _reset()
    now = datetime.utcnow()
    old = now - timedelta(days=settings.usage_retention_days + 5)
    _add_usage("p2", 4, old)
    retention.run_retention(now)
    _add_usage("p2", 6, old)             # more aged rows land later
    retention.run_retention(now)
    with SessionLocal() as db:
        roll = db.get(UsageRollup, "p2")
        assert roll.calls == 2 and roll.cost_units == 10   # cumulative, not overwritten


def test_recent_rows_are_untouched():
    _reset()
    now = datetime.utcnow()
    _add_usage("p3", 9, now - timedelta(days=1))   # inside the window
    out = retention.run_retention(now)
    assert out["deleted_usage"] == 0 and out["rolled"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(UsageEvent)) == 1
        assert db.get(UsageRollup, "p3") is None
