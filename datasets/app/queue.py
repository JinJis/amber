"""Procrastinate task queue — Postgres-backed, async. The platform's job queue + scheduler.

Replaces the in-process asyncio Scheduler **and** the DB-status mutexes (`backfill_running` /
`news_ingest_running`). One `App` over the datasets Postgres DB; Procrastinate owns its
`procrastinate_*` tables there (the broker IS Postgres — no Redis/RabbitMQ). Each pipeline run
becomes a durable job with:

* **retries** (`RetryStrategy`) — a transient upstream failure self-heals instead of vanishing;
* a **lock** — serialize execution per `pipeline+market` (replaces the old global mutex);
* a **queueing_lock** — never queue the same `pipeline+market` twice (dedup).

The scheduler loop becomes per-pipeline `@app.periodic` cron sweeps. A separate **`worker`** process
runs the sweeps + processes the jobs; the **datasets web** process opens the same `App` to *defer*
jobs (manual admin runs) and to *read/control* them via `app.job_manager` (the admin Queue console).
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from procrastinate import App, PsycopgConnector, RetryStrategy

from app.config import settings
from app.logging_config import setup_logging
from app.pipelines import PIPELINE_BY_ID

# The worker process is launched by the procrastinate CLI (`python -m procrastinate --app=app.queue.app
# worker …`), which imports THIS module and never runs our FastAPI startup — so configure logging here
# or the worker ignores LOG_LEVEL entirely (found during the 2026-07 full-pipeline error audit: DEBUG
# applied to the web process but not to the process actually running the ingestion). Idempotent: the
# handler is tagged, so the datasets web process calling setup_logging() again just replaces it.
setup_logging()

logger = logging.getLogger(__name__)


def _conninfo(url: str) -> str:
    """SQLAlchemy uses ``postgresql+psycopg://``; Procrastinate/psycopg want a plain libpq URL."""
    return url.replace("postgresql+psycopg://", "postgresql://")


app = App(connector=PsycopgConnector(conninfo=_conninfo(settings.database_url)))


# One durable job = run one pipeline over one (market, tickers) set. queueing_lock dedups (only one
# pending job per pipeline+market); lock serializes execution per pipeline+market; retries on failure.
@app.task(
    name="run_pipeline",
    queue="ingest",
    retry=RetryStrategy(max_attempts=3, exponential_wait=30),
)
async def run_pipeline(market: str, tickers: list[str], pipeline_id: str, mode: str = "full") -> dict:
    p = PIPELINE_BY_ID.get(pipeline_id)
    if not p:
        return {"skipped": f"unknown pipeline {pipeline_id!r}"}
    if market not in p["markets"]:
        return {"skipped": f"{pipeline_id} not for market {market}"}
    # A prior run killed mid-flight (worker restart/deploy) leaves its IngestionJob stuck at
    # 'running'. Reap stale ones for this kind so the admin shows the truth, not a phantom run.
    kind = p.get("kind") or pipeline_id
    await _reap_stale_jobs(kind, market)
    try:
        await p["runner"](market, tickers, mode=mode)
    except Exception:  # noqa: BLE001 — log the FULL traceback (not just str) before retry kicks in
        tb = traceback.format_exc()
        logger.error("run_pipeline %s/%s failed:\n%s", pipeline_id, market, tb)
        # persist it to an admin-visible IngestionJob — a failure OUTSIDE the runner's own job
        # tracking (e.g. the start_job INSERT) would otherwise leave the admin with an empty note.
        await _record_error(kind, market, tb)
        raise   # re-raise so Procrastinate records the failure + applies the retry strategy
    return {"ran": pipeline_id, "market": market, "tickers": len(tickers), "mode": mode}


async def _record_error(kind: str, market: str, tb: str) -> None:
    try:
        import asyncio

        from app.store.jobs import record_pipeline_error
        await asyncio.to_thread(record_pipeline_error, kind, market, tb)
    except Exception as exc:  # noqa: BLE001 — recording the error must never mask the original
        logger.info("record_pipeline_error %s/%s skipped: %s", kind, market, exc)


async def _reap_stale_jobs(kind: str, market: str) -> None:
    """Mark any IngestionJob of this kind/market left 'running' for too long (worker died before it
    finalised) as an error, so the admin shows a real terminal state instead of a phantom run."""
    try:
        from app.store.jobs import reap_stale_jobs
        import asyncio
        await asyncio.to_thread(reap_stale_jobs, kind, market)
    except Exception as exc:  # noqa: BLE001 — reaping is best-effort, never blocks the run
        logger.info("reap_stale_jobs %s/%s skipped: %s", kind, market, exc)


async def defer_pipeline(market: str, tickers: list[str], pipeline_id: str,
                         mode: str = "full") -> int | None:
    """Enqueue one pipeline run (deduped per pipeline+market). Returns the job id (or None if a
    job for that pipeline+market is already queued). ``mode="delta"`` = new/changed items only."""
    job = run_pipeline.configure(
        lock=f"pipe:{pipeline_id}:{market}",
        queueing_lock=f"pipe:{pipeline_id}:{market}",
    )
    try:
        return await job.defer_async(market=market, tickers=tickers, pipeline_id=pipeline_id, mode=mode)
    except Exception as exc:  # noqa: BLE001 — AlreadyEnqueued (queueing_lock) → skip silently
        logger.info("defer_pipeline %s/%s skipped: %s", pipeline_id, market, exc)
        return None


# --- scheduling: per-pipeline cron sweeps (replaces the asyncio Scheduler loop) ----------------
# Each sweep resolves the configured universe and enqueues a run_pipeline job per market. Cadence
# lives in the cron (news ~hourly · prices ~daily · financials/corp_actions/filing_text ~weekly),
# which is what the old min_interval_seconds encoded. The worker runs these + processes the jobs.
async def _sweep(pipeline_id: str) -> None:
    from app.store.universes import resolve_universe

    # Cron sweeps run in DELTA mode: the point of a periodic sweep is to pick up what's NEW —
    # re-collecting the unchanged universe every week burned the OpenDART daily quota (and
    # embedding cost) for nothing. A full re-collect stays available as a manual admin run.
    markets = (PIPELINE_BY_ID.get(pipeline_id) or {}).get("markets") or []
    for market, tickers in await resolve_universe(settings.scheduler_universe):
        # skip markets the pipeline doesn't serve (e.g. presentation_text=US-only) so a
        # single-market pipeline doesn't queue a no-op job for the other market.
        if tickers and market.value in markets:
            await defer_pipeline(market.value, tickers, pipeline_id, mode="delta")


@app.periodic(cron="0 * * * *")            # hourly
@app.task(name="sweep_news", queue="sweep", queueing_lock="sweep_news")
async def sweep_news(timestamp: int) -> None:
    await _sweep("news")


@app.periodic(cron="0 4 * * *")            # daily 04:00
@app.task(name="sweep_prices", queue="sweep", queueing_lock="sweep_prices")
async def sweep_prices(timestamp: int) -> None:
    await _sweep("prices")


@app.periodic(cron="0 3 * * 1")            # weekly Mon 03:00
@app.task(name="sweep_financials", queue="sweep", queueing_lock="sweep_financials")
async def sweep_financials(timestamp: int) -> None:
    await _sweep("financials")


@app.periodic(cron="30 3 * * 1")           # weekly Mon 03:30
@app.task(name="sweep_corp_actions", queue="sweep", queueing_lock="sweep_corp_actions")
async def sweep_corp_actions(timestamp: int) -> None:
    await _sweep("corp_actions")


@app.periodic(cron="0 5 * * 1")            # weekly Mon 05:00
@app.task(name="sweep_filing_text", queue="sweep", queueing_lock="sweep_filing_text")
async def sweep_filing_text(timestamp: int) -> None:
    await _sweep("filing_text")


# 어닝콜/발표자료/KR실적 — 원래 default-off라 자동 갱신이 안 됐다(수동 1회만 색인 → 커버리지 희박).
# filing_text 뒤에 스태거해 주간 델타 스윕에 편입: 이미 색인한 트랜스크립트·덱·잠정실적은 건너뛰고
# 새 분기/공시만 받는다. 한 종류만 관심이면 해당 스윕을 주석 처리하면 된다.
@app.periodic(cron="0 6 * * 1")            # weekly Mon 06:00
@app.task(name="sweep_transcript_text", queue="sweep", queueing_lock="sweep_transcript_text")
async def sweep_transcript_text(timestamp: int) -> None:
    await _sweep("transcript_text")         # US+KR 어닝콜 전문 (API Ninjas 프리미엄 / AV 폴백)


@app.periodic(cron="30 6 * * 1")           # weekly Mon 06:30
@app.task(name="sweep_kr_earnings", queue="sweep", queueing_lock="sweep_kr_earnings")
async def sweep_kr_earnings(timestamp: int) -> None:
    await _sweep("kr_earnings")             # KR 잠정실적 공정공시 (OpenDART — 쿼터 로테이션)


@app.periodic(cron="0 7 * * 1")            # weekly Mon 07:00
@app.task(name="sweep_presentation_text", queue="sweep", queueing_lock="sweep_presentation_text")
async def sweep_presentation_text(timestamp: int) -> None:
    await _sweep("presentation_text")       # US 8-K 발표자료 덱 (Document AI — 콜당 과금)


# --- lifecycle (datasets web process) -----------------------------------------------------------
async def open_and_migrate() -> None:
    """Open the shared connector + ensure the Procrastinate schema exists. Called once at datasets
    startup so manual admin runs can ``defer`` jobs and the admin Queue console can read
    ``app.job_manager``. The worker is a separate process that opens its own connector.

    ``apply_schema`` issues plain ``CREATE TABLE`` (no IF NOT EXISTS), so it is first-run only —
    re-applying on an existing DB errors. We probe for ``procrastinate_jobs`` and apply only when
    absent, making startup idempotent across restarts."""
    await app.open_async()
    row = await app.connector.execute_query_one_async(
        "SELECT to_regclass('public.procrastinate_jobs') AS tbl"
    )
    if not row.get("tbl"):
        logger.info("procrastinate schema absent → applying")
        await app.schema_manager.apply_schema_async()


async def close() -> None:
    await app.close_async()


async def defer_sweep(pipeline_id: str, mode: str = "delta") -> dict:
    """Manually enqueue a pipeline's sweep right now (the admin 'run now' button) — resolves the
    configured universe and defers a job per market. Defaults to delta (like the cron sweep);
    pass mode='full' for a complete re-collect."""
    if pipeline_id not in PIPELINE_BY_ID:
        return {"deferred": False, "detail": f"unknown pipeline {pipeline_id!r}"}
    from app.store.universes import resolve_universe

    for market, tickers in await resolve_universe(settings.scheduler_universe):
        if tickers:
            await defer_pipeline(market.value, tickers, pipeline_id, mode=mode)
    return {"deferred": True, "pipeline_id": pipeline_id, "mode": mode}


# --- admin: monitor + control over app.job_manager ----------------------------------------------
_STATUSES = ("todo", "doing", "succeeded", "failed", "cancelled", "aborting", "aborted")


async def _periodic_schedules() -> list[dict]:
    """The registered cron sweeps (no DB) — what runs automatically and how often."""
    out: list[dict] = []
    for (name, _key), pt in app.periodic_registry.periodic_tasks.items():
        pid = name.removeprefix("sweep_")
        p = PIPELINE_BY_ID.get(pid, {})
        out.append({"task": name, "pipeline_id": pid, "cron": pt.cron,
                    "label": p.get("label", pid), "source": p.get("source")})
    return out


def _job_dict(j) -> dict:
    return {
        "id": j.id, "task": j.task_name, "queue": j.queue, "status": j.status,
        "lock": j.lock, "args": j.task_kwargs, "attempts": j.attempts,
        "scheduled_at": j.scheduled_at.isoformat() if j.scheduled_at else None,
    }


async def queue_overview() -> dict:
    """Queue health for the admin: per-queue job counts by status + the periodic sweep schedule +
    the registered task names. Fail-safe — if the queue DB is unreachable, return the (DB-free)
    schedule so the page still renders instead of 500-ing."""
    periodic = await _periodic_schedules()
    tasks = sorted(t for t in app.tasks if not t.startswith(("builtin", "procrastinate")))
    universe = settings.scheduler_universe  # the source ids each sweep refreshes
    try:
        queues = [dict(q) for q in await app.job_manager.list_queues_async()]
    except Exception as exc:  # noqa: BLE001 — queue DB unreachable → schedule-only view
        return {"queues": [], "totals": {}, "periodic": periodic, "tasks": tasks,
                "universe": universe, "error": str(exc)[:200]}
    totals = {k: sum(int(q.get(k) or 0) for q in queues) for k in _STATUSES}
    return {"queues": queues, "totals": totals, "periodic": periodic, "tasks": tasks,
            "universe": universe, "running": totals.get("doing", 0),
            "pending": totals.get("todo", 0), "failed": totals.get("failed", 0)}


async def list_queue_jobs(status: str | None = None, queue: str | None = None, limit: int = 50) -> list[dict]:
    jobs = list(await app.job_manager.list_jobs_async(status=status, queue=queue))
    jobs.sort(key=lambda j: j.id, reverse=True)  # newest first
    return [_job_dict(j) for j in jobs[:limit]]


async def job_events(job_id: int) -> list[dict]:
    """The Procrastinate event timeline for a job (deferred → started → [abort_requested] → failed/
    succeeded) — the missing diagnostic that shows WHY a job is stuck/failed. The procrastinate_*
    tables live in the datasets DB, so we read them via the app's own session."""
    import asyncio

    from sqlalchemy import text

    from app.store.db import SessionLocal

    def _q() -> list[dict]:
        with SessionLocal() as db:
            rows = db.execute(
                text("SELECT type, at FROM procrastinate_events WHERE job_id = :id ORDER BY at, id"),
                {"id": job_id},
            ).all()
            return [{"type": r[0], "at": r[1].isoformat() if r[1] else None} for r in rows]

    return await asyncio.to_thread(_q)


async def job_detail(job_id: int) -> dict:
    """A single queue job + its event timeline + the linked IngestionJob's error note (matched by
    pipeline kind + market) — everything the admin needs to diagnose a stuck/failed pipeline run."""
    import asyncio

    from app.store.jobs import latest_job, list_activity

    events = await job_events(job_id)
    found = list(await app.job_manager.list_jobs_async(id=job_id))
    job = _job_dict(found[0]) if found else {"id": job_id}
    args = job.get("args") or {}
    pid, market = args.get("pipeline_id"), args.get("market")
    ingestion, activity = None, []
    if pid:
        kind = (PIPELINE_BY_ID.get(pid) or {}).get("kind") or pid
        ingestion = await asyncio.to_thread(latest_job, kind, market)
        # the live activity feed for this run (by IngestionJob id if matched, else the kind)
        ing_id = (ingestion or {}).get("id")
        activity = await asyncio.to_thread(
            list_activity, 120, None if ing_id else kind, ing_id)
    return {"job": job, "events": events, "ingestion": ingestion, "activity": activity}


async def retry_queue_job(job_id: int) -> dict:
    """Re-enqueue a failed/stuck job to run now. Only failed or in-flight jobs are retryable —
    Procrastinate rejects retrying an already-succeeded/cancelled job, so guard it instead of 500-ing."""
    status = getattr(await app.job_manager.get_job_status_async(job_id), "value", None)
    if status not in ("failed", "doing", "aborting"):
        return {"retried": False, "job_id": job_id, "status": status,
                "detail": "only failed or in-flight jobs can be retried"}
    await app.job_manager.retry_job_by_id_async(job_id, retry_at=datetime.now(timezone.utc))
    return {"retried": True, "job_id": job_id}


async def cancel_queue_job(job_id: int) -> dict:
    """Cancel a queued job, or request an abort if it is already running."""
    status = await app.job_manager.get_job_status_async(job_id)
    abort = getattr(status, "value", status) == "doing"
    ok = await app.job_manager.cancel_job_by_id_async(job_id, abort=abort)
    return {"cancelled": bool(ok), "job_id": job_id, "aborted": abort}
