"""SC-2.3 / CR-1: the durable run registry recovers runs orphaned by a replica crash — marking them
reaped and REFUNDING the quota (TurnUsage) the user was charged for an answer never delivered — while
a live replica's heartbeated runs are left alone."""

from __future__ import annotations

from datetime import datetime, timedelta

from studioapi import runstate
from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.models import RunRecord, TurnUsage


def setup_module(_module):
    init_db()


def _reset():
    with SessionLocal() as db:
        db.query(RunRecord).delete()
        db.query(TurnUsage).filter(TurnUsage.user_email == "u@x.com").delete()
        db.commit()


def _consume_turn() -> int:
    with SessionLocal() as db:
        tu = TurnUsage(user_email="u@x.com", day="2026-07-13", month="2026-07")
        db.add(tu)
        db.commit()
        return tu.id


def test_register_then_mark_done_keeps_quota():
    _reset()
    tu_id = _consume_turn()
    runstate.register("run_done", "c1", "u@x.com", tu_id)
    runstate.mark("run_done", "done")
    with SessionLocal() as db:
        assert db.get(RunRecord, "run_done").status == "done"
        assert db.get(TurnUsage, tu_id) is not None        # a completed turn is NOT refunded


def test_reap_refunds_orphaned_dead_replica_run():
    _reset()
    tu_id = _consume_turn()
    stale = datetime.utcnow() - timedelta(seconds=settings.run_heartbeat_stale_seconds + 60)
    with SessionLocal() as db:   # a run owned by a DEAD replica (stale heartbeat, foreign owner)
        db.add(RunRecord(id="run_dead", conversation_id="c1", user_email="u@x.com",
                         turn_usage_id=tu_id, status="running", owner="dead-replica",
                         heartbeat_at=stale))
        db.commit()

    assert runstate.reap_orphans() == 1
    with SessionLocal() as db:
        assert db.get(TurnUsage, tu_id) is None             # quota refunded
        assert db.get(RunRecord, "run_dead").status == "reaped"


def test_fresh_heartbeat_is_not_reaped():
    _reset()
    tu_id = _consume_turn()
    runstate.register("run_live", "c1", "u@x.com", tu_id)   # fresh heartbeat_at = now
    assert runstate.reap_orphans() == 0
    with SessionLocal() as db:
        assert db.get(RunRecord, "run_live").status == "running"
        assert db.get(TurnUsage, tu_id) is not None


def test_heartbeat_own_refreshes_running_rows():
    _reset()
    stale = datetime.utcnow() - timedelta(seconds=settings.run_heartbeat_stale_seconds + 60)
    with SessionLocal() as db:   # a running row owned by THIS instance but with a stale beat
        db.add(RunRecord(id="run_mine", conversation_id="c1", user_email="u@x.com",
                         turn_usage_id=None, status="running", owner=runstate.INSTANCE_ID,
                         heartbeat_at=stale))
        db.commit()
    runstate.heartbeat_own()          # this replica is alive → refresh its beat
    assert runstate.reap_orphans() == 0   # no longer stale → survives
    with SessionLocal() as db:
        assert db.get(RunRecord, "run_mine").status == "running"


def test_reap_gcs_old_finished_records():
    _reset()
    old = datetime.utcnow() - timedelta(hours=settings.run_record_retention_hours + 1)
    with SessionLocal() as db:
        db.add(RunRecord(id="run_old", conversation_id="c1", user_email="u@x.com",
                         turn_usage_id=None, status="done", owner="whoever",
                         heartbeat_at=old, finished_at=old))
        db.commit()
    runstate.reap_orphans()
    with SessionLocal() as db:
        assert db.get(RunRecord, "run_old") is None        # GC'd past the retention window
