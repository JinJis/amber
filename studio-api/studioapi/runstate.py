"""SC-2.3 / CR-1 — durable run registry so a run orphaned by a replica crash is recovered.

The live SSE event buffer stays in-process (runs.py) — that's the pragmatic "survives client
disconnect within a session" layer. THIS module is the cross-replica truth for a run's *lifecycle*
and its *consumed quota*: every real turn registers a ``RunRecord`` (owner = this replica) the moment
it starts, marks it done/error when the driver returns, and the owner heartbeats its running rows on
each watchdog tick. If a replica dies mid-flight it stops heartbeating; a reaper (one replica at a
time, advisory-locked) then marks the orphan ``reaped`` and DELETES its ``TurnUsage`` row — refunding
the quota the user was charged for an answer they never got.

Postgres-backed (durable + transactional) regardless of whether Redis is configured — quota refund
must not live in an ephemeral store. SQLite (unit tests) exercises the same code path minus the
advisory lock.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.models import RunRecord, TurnUsage

log = logging.getLogger("studioapi.runstate")

# Identifies THIS studio-api process/replica for the lifetime of the process. A crash → this id never
# heartbeats again → the reaper recovers its in-flight runs.
INSTANCE_ID = uuid.uuid4().hex[:16]

_REAP_LOCK_ID = 0x76675250  # 'vgRP' — only one replica reaps/GCs at a time


def register(run_id: str, conversation_id: str, user_email: str, turn_usage_id: int | None) -> None:
    """Record a just-started run as owned by this replica (best-effort — a registry write must never
    sink the run itself)."""
    try:
        with SessionLocal() as db:
            db.merge(RunRecord(id=run_id, conversation_id=conversation_id, user_email=user_email,
                               turn_usage_id=turn_usage_id, status="running", owner=INSTANCE_ID,
                               heartbeat_at=datetime.utcnow()))
            db.commit()
    except Exception:  # noqa: BLE001 — never block a chat turn on the registry
        log.warning("run register failed for %s", run_id, exc_info=True)


def mark(run_id: str, status: str) -> None:
    """Close a run (done|error) — only if still 'running' (never clobber a reap that already fired)."""
    try:
        with SessionLocal() as db:
            db.execute(update(RunRecord).where(RunRecord.id == run_id, RunRecord.status == "running")
                       .values(status=status, finished_at=datetime.utcnow()))
            db.commit()
    except Exception:  # noqa: BLE001
        log.warning("run mark %s failed for %s", status, run_id, exc_info=True)


def heartbeat_own() -> None:
    """Prove this replica is alive by bumping heartbeat_at on its own running rows."""
    with SessionLocal() as db:
        db.execute(update(RunRecord).where(RunRecord.owner == INSTANCE_ID, RunRecord.status == "running")
                   .values(heartbeat_at=datetime.utcnow()))
        db.commit()


def reap_orphans() -> int:
    """One replica at a time (advisory lock): mark runs whose owner stopped heartbeating as 'reaped'
    and refund their quota (delete the linked TurnUsage). Also GC old finished rows so the table can't
    grow without bound. Returns the number of quota refunds issued. No-op lock on SQLite."""
    now = datetime.utcnow()
    stale_cutoff = now - timedelta(seconds=settings.run_heartbeat_stale_seconds)
    gc_cutoff = now - timedelta(hours=settings.run_record_retention_hours)
    refunded = 0
    with SessionLocal() as db:
        is_pg = db.bind.dialect.name == "postgresql"
        if is_pg and not bool(db.execute(func.pg_try_advisory_lock(_REAP_LOCK_ID)).scalar()):
            return 0
        try:
            orphans = db.execute(select(RunRecord).where(
                RunRecord.status == "running", RunRecord.heartbeat_at < stale_cutoff)).scalars().all()
            for r in orphans:
                if r.turn_usage_id is not None:
                    # refund: the turn was charged but produced no answer (its replica died)
                    db.execute(delete(TurnUsage).where(TurnUsage.id == r.turn_usage_id))
                    refunded += 1
                r.status = "reaped"
                r.finished_at = now
            # GC: drop long-finished records (done|error|reaped) past the retention window
            db.execute(delete(RunRecord).where(
                RunRecord.status != "running", RunRecord.finished_at < gc_cutoff))
            db.commit()
            if orphans:
                log.info("reaped %d orphaned run(s), refunded %d turn(s)", len(orphans), refunded)
        finally:
            if is_pg:
                db.execute(func.pg_advisory_unlock(_REAP_LOCK_ID))
                db.commit()
    return refunded
