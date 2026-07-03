"""Ingestion job log + backfill runner (PH-1).

Makes data ingestion *operable and visible*: every manual backfill or scheduled
refresh writes an ``IngestionJob`` row (status / rows / error / timing), and a
backfill can be triggered without shelling into the container. The admin ops
console reads these so an empty store is obvious (and fixable) rather than silent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.store.bulk import bulk_load_kr, bulk_load_us
from app.store.db import SessionLocal
from app.store.models import IngestionJob, PipelineActivity
from app.store.universes import resolve_one

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def start_job(kind: str, market: str | None, spec: str | None, total: int = 0) -> int:
    # `spec` is varchar(256): a caller that joined a long ticker list (filing_text over 200-500
    # tickers) would overflow it and the INSERT would throw *before* the job row exists — failing
    # the run in milliseconds with no visible record. Truncate defensively so no caller can do that.
    with SessionLocal() as db:
        job = IngestionJob(kind=kind[:16], market=(market or None) and market[:2],
                           spec=(spec or None) and spec[:256], status="running",
                           total=total, done=0, started_at=_now())
        db.add(job)
        db.commit()
        return job.id


def update_progress(job_id: int, done: int) -> None:
    # Best-effort: a live progress tick must never abort the whole ingest run on a transient
    # write lock (WAL + busy_timeout make these rare). The next tick / finish_job will catch up.
    try:
        with SessionLocal() as db:
            job = db.get(IngestionJob, job_id)
            if job is not None:
                job.done = done
                db.commit()
    except Exception:  # noqa: BLE001
        pass


def finish_job(job_id: int, status: str, rows: int = 0, error: str | None = None) -> None:
    with SessionLocal() as db:
        job = db.get(IngestionJob, job_id)
        if job is None:
            return
        job.status = status
        job.rows = rows
        job.error = (error or "")[:2000] or None
        job.ended_at = _now()
        db.commit()


def record_pipeline_error(kind: str, market: str | None, error: str) -> int:
    """Persist a pipeline-run failure into an admin-visible IngestionJob. Finalizes the run's own
    'running' row as error if it exists, else creates a fresh error row — so a failure that happened
    BEFORE/OUTSIDE the runner's own job-tracking (e.g. the start_job INSERT itself) is still shown in
    the admin job-detail, not just the worker stdout. Returns the job id."""
    with SessionLocal() as db:
        q = select(IngestionJob).where(IngestionJob.kind == kind, IngestionJob.status == "running")
        if market:
            q = q.where(IngestionJob.market == market)
        job = db.execute(q.order_by(IngestionJob.started_at.desc()).limit(1)).scalar_one_or_none()
        if job is None:
            job = IngestionJob(kind=kind[:16], market=(market or None) and market[:2], spec="(run failed)",
                               status="running", total=0, done=0, started_at=_now())
            db.add(job)
        job.status = "error"
        job.error = (error or "")[:2000] or None
        job.ended_at = _now()
        db.commit()
        return job.id


_ACTIVITY_CAP = 800  # keep the live feed bounded — trim older lines past this many


def log_activity(kind: str, market: str | None, message: str,
                 job_id: int | None = None, level: str = "info") -> None:
    """Append a milestone line to the live pipeline-activity feed (best-effort — never breaks a run).
    The worker calls this as it works ("[005930] OpenDART 공시 4건 → RAG 320 chunks") so the admin can
    show, in real time, what data a running pipeline is fetching and what came back."""
    try:
        with SessionLocal() as db:
            row = PipelineActivity(job_id=job_id, kind=(kind or "")[:16],
                                   market=(market or None) and market[:2], level=(level or "info")[:8],
                                   message=(message or "")[:512], at=_now())
            db.add(row)
            db.commit()
            if row.id % 50 == 0 and row.id > _ACTIVITY_CAP:   # occasional trim, not every insert
                db.execute(delete(PipelineActivity).where(PipelineActivity.id < row.id - _ACTIVITY_CAP))
                db.commit()
    except Exception:  # noqa: BLE001 — activity logging must never sink a pipeline run
        pass


def list_activity(limit: int = 80, kind: str | None = None, job_id: int | None = None) -> list[dict]:
    """Most-recent-first activity lines, optionally scoped to a pipeline kind or a specific job."""
    try:
        with SessionLocal() as db:
            q = select(PipelineActivity)
            if job_id is not None:
                q = q.where(PipelineActivity.job_id == job_id)
            if kind:
                q = q.where(PipelineActivity.kind == kind)
            rows = db.execute(q.order_by(PipelineActivity.id.desc()).limit(limit)).scalars().all()
            return [{"id": r.id, "job_id": r.job_id, "kind": r.kind, "market": r.market,
                     "level": r.level, "message": r.message,
                     "at": r.at.isoformat() if r.at else None} for r in rows]
    except Exception:  # noqa: BLE001 — the table may not exist yet on an un-migrated DB
        return []


def reap_stale_jobs(kind: str, market: str | None = None, older_than_minutes: int = 20) -> int:
    """Finalize IngestionJobs left 'running' past a threshold (the worker died before finishing) so
    the admin shows a real terminal state instead of a phantom run. Returns how many were reaped."""
    from datetime import timedelta
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    with SessionLocal() as db:
        q = select(IngestionJob).where(
            IngestionJob.kind == kind, IngestionJob.status == "running",
            IngestionJob.started_at < cutoff)
        if market:
            q = q.where(IngestionJob.market == market)
        stale = db.execute(q).scalars().all()
        for j in stale:
            j.status = "error"
            j.error = ((j.error or "") + " · 미완료(워커 재시작/종료로 중단됨)").strip(" ·")[:2000]
            j.ended_at = _now()
        if stale:
            db.commit()
        return len(stale)


def latest_job(kind: str, market: str | None = None) -> dict | None:
    """The most recent IngestionJob for a pipeline kind (+ market), as a dict — lets the admin link a
    queue job to its run outcome + error note."""
    with SessionLocal() as db:
        q = select(IngestionJob).where(IngestionJob.kind == kind)
        if market:
            q = q.where(IngestionJob.market == market)
        j = db.execute(q.order_by(IngestionJob.started_at.desc()).limit(1)).scalar_one_or_none()
        if j is None:
            return None
        return {"id": j.id, "kind": j.kind, "market": j.market, "spec": j.spec, "status": j.status,
                "rows": j.rows, "total": j.total, "done": j.done, "error": j.error,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "ended_at": j.ended_at.isoformat() if j.ended_at else None}


def last_job_at(kind: str) -> datetime | None:
    """Most recent start time for a pipeline `kind` (any status) — the scheduler uses this to
    skip a pipeline that ran within its cadence (so heavy historical pipelines don't re-fetch the
    full history every sweep). Returns a naive-UTC datetime, matching how jobs are stamped."""
    with SessionLocal() as db:
        return db.scalar(select(func.max(IngestionJob.started_at)).where(IngestionJob.kind == kind))


def list_jobs(limit: int = 25) -> list[dict]:
    with SessionLocal() as db:
        rows = db.execute(
            select(IngestionJob).order_by(IngestionJob.started_at.desc()).limit(limit)
        ).scalars().all()
        return [
            {
                "id": j.id, "kind": j.kind, "market": j.market, "spec": j.spec,
                "status": j.status, "rows": j.rows, "total": j.total, "done": j.done,
                "error": j.error,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "ended_at": j.ended_at.isoformat() if j.ended_at else None,
            }
            for j in rows
        ]


async def run_ticker_job(
    kind: str, market: str, spec: str, tickers: list[str],
    ingest_one: Callable[[str], Awaitable[int]],
) -> dict:
    """Run ``ingest_one(ticker)`` for each ticker as ONE IngestionJob — the per-ticker best-effort
    pattern shared by the prices + corp-actions runners (RF-05).

    Best-effort per ticker: a failure records ``-1`` and is logged, never sinking the run; live
    progress ticks after each; the job finalizes ``success`` (with a ``failed: [...]`` note if any
    failed) or ``error`` if the loop itself blows up. Sync ``jobs`` calls run off the event loop.
    Returns ``{status, rows, per_ticker, failed}`` (or ``{status: 'error', error}``).
    """
    job = await asyncio.to_thread(start_job, kind, market, spec, len(tickers))
    await asyncio.to_thread(log_activity, kind, market, f"▶ 시작 · {len(tickers)}종목", job)
    per: dict[str, int] = {}
    try:
        for i, t in enumerate(tickers, 1):
            try:
                per[t] = await ingest_one(t)
                await asyncio.to_thread(log_activity, kind, market,
                                        f"[{t}] {per[t]} rows ✓ ({i}/{len(tickers)})", job)
            except Exception as exc:  # noqa: BLE001 — one ticker never sinks the run
                per[t] = -1
                logger.warning("%s ingest failed %s:%s — %s", kind, market, t, exc)
                await asyncio.to_thread(log_activity, kind, market,
                                        f"[{t}] 실패 — {type(exc).__name__}: {exc}", job, "error")
            await asyncio.to_thread(update_progress, job, i)
        rows = sum(v for v in per.values() if v > 0)
        failed = [t for t, v in per.items() if v == -1]
        await asyncio.to_thread(finish_job, job, "success", rows, (f"failed: {failed}" if failed else None))
        await asyncio.to_thread(log_activity, kind, market,
                                f"✓ 완료 · {rows} rows" + (f", {len(failed)} 실패" if failed else ""), job)
        return {"status": "success", "rows": rows, "per_ticker": per, "failed": failed}
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(finish_job, job, "error", 0, str(exc))
        await asyncio.to_thread(log_activity, kind, market, f"✗ 실패 — {exc}", job, "error")
        return {"status": "error", "error": str(exc)}


async def run_backfill(
    market: str | None = None, tickers: list[str] | None = None, deep: bool = True,
    limit: int | None = None, preset: str | None = None,
) -> dict:
    """Run a backfill and record it as an IngestionJob (with live per-ticker progress).

    Either pass a ``preset`` id (resolved from universes) or an explicit ``market`` +
    ``tickers``. Concurrency is serialized by the Procrastinate queue's per-pipeline lock
    (``pipe:financials:<market>``), so this runner no longer guards itself.
    """
    if preset:
        market, tickers = await resolve_one(preset)  # dynamic fetch (PH-PIPE)
        if not tickers:
            return {"status": "error", "error": f"Universe '{preset}' resolved to no tickers."}
        spec = f"universe:{preset}"
    else:
        spec = ",".join(tickers or []) or "(none)"
    if not market:
        return {"status": "error", "error": "market or preset required."}
    if not tickers:
        return {"status": "error", "error": "No tickers to backfill (US universe needs a preset or explicit tickers)."}

    job_id = start_job("backfill", market, (spec + (" · deep" if deep else ""))[:256], total=len(tickers))
    src = "SEC EDGAR" if market.upper() == "US" else "OpenDART"
    log_activity("backfill", market, f"▶ 재무 백필 시작 · {len(tickers)}종목 · {src}", job_id)
    # log progress milestones (~every 10%) so the feed shows the bulk load advancing, not just a bar
    step = max(1, len(tickers) // 10)

    def progress(done, total):
        update_progress(job_id, done)
        if done and done % step == 0:
            log_activity("backfill", market, f"{src} 재무 수집 {done}/{total}", job_id)
    try:
        if market.upper() == "US":
            result = await bulk_load_us(tickers=tickers, limit=limit, on_progress=progress)
        elif market.upper() == "KR":
            result = await bulk_load_kr(tickers, limit=limit or 15, on_progress=progress)
        else:
            raise ValueError(f"Unknown market '{market}'.")
        per = result if isinstance(result, dict) else {}  # {ticker: rows} (-1 = per-ticker failure)
        rows = sum(v for v in per.values() if isinstance(v, int) and v > 0)
        failed = [t for t, v in per.items() if v == -1]
        finish_job(job_id, "success", rows=rows, error=(f"failed: {failed}" if failed else None))
        log_activity("backfill", market, f"✓ 완료 · {rows} rows" + (f", {len(failed)} 실패" if failed else ""), job_id)
        return {"job_id": job_id, "status": "success", "rows": rows, "per_ticker": per, "failed": failed}
    except Exception as exc:  # noqa: BLE001 — record the failure, don't crash the worker
        finish_job(job_id, "error", error=str(exc))
        log_activity("backfill", market, f"✗ 실패 — {exc}", job_id, "error")
        return {"job_id": job_id, "status": "error", "error": str(exc)}
