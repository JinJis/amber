"""Admin / Ops: self-test, store stats, and scheduler monitor/control."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app import queue as Q
from app.deps import ApiKeyDep
from app.pipelines import PIPELINE_BY_ID, list_pipelines, resolve_pipeline_ids
from app.selftest import run_selftest
from app.store.jobs import list_jobs
from app.store.screener import store_stats
from app.store.universes import list_presets, resolve_one, resolve_universe
from app.symbols import Market

router = APIRouter(tags=["Admin / Ops"], prefix="/admin")


async def _defer_groups(groups, pipeline_ids: list[str]) -> int:
    """Enqueue the selected pipelines over each (market, tickers) group via the Procrastinate queue.
    The queue's queueing_lock dedups, so re-dispatching an already-queued pipeline+market is a no-op.
    Returns how many jobs were actually enqueued."""
    n = 0
    for market, tickers in groups:
        if not tickers:
            continue
        for pid in pipeline_ids:
            p = PIPELINE_BY_ID.get(pid)
            if p and market.value in p["markets"]:
                if await Q.defer_pipeline(market.value, tickers, pid):
                    n += 1
    return n


class BackfillRequest(BaseModel):
    preset: str | None = None  # a universe preset id (see /admin/universes); takes precedence
    market: str = "US"
    tickers: list[str] | None = None  # explicit tickers (required for US/KR without a preset)
    deep: bool = True
    limit: int | None = None


class PipelineRunRequest(BaseModel):
    """PH-PIPE unified backfill: run a SET of pipelines over a preset (or explicit market+tickers)."""
    preset: str | None = None       # universe preset id (see /admin/universes)
    market: str | None = None       # with `tickers`, an explicit universe instead of a preset
    tickers: list[str] | None = None
    pipelines: list[str] | None = None  # pipeline ids (see /admin/pipelines); omit → default set


class NewsIngestRequest(BaseModel):
    market: str = "US"
    tickers: list[str] | None = None  # omit for broad market news
    limit: int | None = None


@router.get(
    "/selftest",
    dependencies=[ApiKeyDep],
    summary="▶ Run a live self-test of implemented endpoints",
    description=(
        "Drives a curated set of implemented endpoints (US + KR) through the real stack and "
        "returns pass / fail / skipped per check. `skipped` = an upstream key or IP is "
        "unavailable here (not a code failure). Click **Try it out → Execute** to run it."
    ),
)
async def selftest(request: Request) -> dict:
    return await run_selftest(request.app)


@router.get("/store/stats", dependencies=[ApiKeyDep], summary="Ingestion store stats")
async def store_statistics() -> dict:
    return await asyncio.to_thread(store_stats)


@router.get("/upstream-health", dependencies=[ApiKeyDep], summary="Upstream API health (reachability/latency/key)")
async def upstream_health() -> dict:
    """CE-HEALTH: probe every connector's upstream (SEC/DART/Yahoo/DBnomics/ECOS/news) so the
    admin console shows which data source is healthy/degraded/down at a glance."""
    from app.store.upstream_health import probe_upstreams
    return await probe_upstreams()


@router.get("/universes", dependencies=[ApiKeyDep], summary="Curated backfill universes (presets)")
async def universes() -> dict:
    return {"universes": list_presets()}


@router.get("/pipelines", dependencies=[ApiKeyDep], summary="Data pipelines registry + latest run per pipeline")
async def pipelines() -> dict:
    """The PH-PIPE pipeline registry (what each collects, source, store) joined with the most
    recent IngestionJob per pipeline — so the admin can show path · schedule · status · coverage."""
    jobs = await asyncio.to_thread(list_jobs, 100)
    latest: dict[str, dict] = {}
    for j in jobs:  # jobs are newest-first → first seen per kind is the latest
        latest.setdefault(j["kind"], j)
    pipes = []
    for p in list_pipelines():
        pipes.append({**p, "latest": latest.get(p["kind"])})
    return {"pipelines": pipes, "queue": await Q.queue_overview()}


@router.post(
    "/pipelines/run",
    dependencies=[ApiKeyDep],
    summary="▶ PH-PIPE: run a SET of pipelines over a universe (unified backfill)",
    description=(
        "Enqueues the selected `pipelines` (omit → default set) over a `preset` universe (or explicit "
        "`market`+`tickers`) on the Procrastinate queue. The worker runs them with retries; each "
        "pipeline records its own IngestionJob (see `/admin/jobs`) and live queue state shows in "
        "`/admin/queue`. The queueing_lock dedups, so re-running an in-flight pipeline is a no-op."
    ),
)
async def pipelines_run(body: PipelineRunRequest) -> dict:
    ids = resolve_pipeline_ids(body.pipelines)
    # Explicit market+tickers resolve instantly → enqueue now and report how many jobs were queued.
    if body.market and body.tickers:
        try:
            groups = [(Market(body.market.upper()), body.tickers)]
        except ValueError:
            return {"started": False, "detail": f"unknown market {body.market!r}"}
        deferred = await _defer_groups(groups, ids)
        return {"started": True, "pipelines": ids,
                "universe": [{"market": m.value, "count": len(t)} for m, t in groups],
                "deferred": deferred, "see": "/admin/queue"}
    if body.preset:
        # A PRESET may need a slow upstream fetch (pykrx/SEC/OpenDART) to resolve membership, so do it
        # in the background and enqueue once resolved — the request returns immediately, never times out.
        async def _resolve_and_defer() -> None:
            await _defer_groups(await resolve_universe(body.preset), ids)
        asyncio.create_task(_resolve_and_defer())
        return {"started": True, "pipelines": ids, "preset": body.preset, "see": "/admin/queue"}
    return {"started": False, "detail": "preset or (market + tickers) required"}


@router.get("/jobs", dependencies=[ApiKeyDep], summary="Recent ingestion jobs (backfill / scheduled)")
async def ingestion_jobs(limit: int = 25) -> dict:
    return {"jobs": await asyncio.to_thread(list_jobs, limit)}


@router.post(
    "/backfill",
    dependencies=[ApiKeyDep],
    summary="▶ Trigger a historical backfill (populates the ingestion store)",
    description=(
        "Enqueues the **financials** pipeline (deep statements + company facts) over a `preset` "
        "universe or explicit `market`+`tickers` on the queue. The worker runs it with retries and "
        "records an `IngestionJob` (see `/admin/jobs`); queue state shows in `/admin/queue`. "
        "Without this, the store is empty and the screener / historical endpoints return nothing."
    ),
)
async def backfill(body: BackfillRequest) -> dict:
    if body.preset:
        async def _resolve_and_defer() -> None:
            await _defer_groups(await resolve_universe(body.preset), ["financials"])
        asyncio.create_task(_resolve_and_defer())
        return {"started": True, "target": body.preset, "see": "/admin/queue"}
    if body.market and body.tickers:
        try:
            groups = [(Market(body.market.upper()), body.tickers)]
        except ValueError:
            return {"started": False, "detail": f"unknown market {body.market!r}"}
        deferred = await _defer_groups(groups, ["financials"])
        return {"started": True, "target": f"{body.market}:{len(body.tickers)}t",
                "deferred": deferred, "see": "/admin/queue"}
    return {"started": False, "detail": "preset or (market + tickers) required"}


class FilingsIngestRequest(BaseModel):
    preset: str | None = None  # a universe preset id (US); takes precedence
    market: str = "US"
    tickers: list[str] | None = None  # explicit tickers (watchlist-scoped)


@router.post(
    "/filings/ingest",
    dependencies=[ApiKeyDep],
    summary="▶ index filing text into RAG (full-text search + passage evidence)",
    description=(
        "Fetches each ticker's recent filings as HTML (US iXBRL · KR document.xml) and indexes "
        "their text into RAG, so `rag__search` returns real filing passages (MD&A, notes, any line) "
        "and the in-app viewer can highlight the cited passage. Global corpus; runs in the "
        "background, progress in `/admin/jobs`."
    ),
)
async def filings_ingest(body: FilingsIngestRequest) -> dict:
    market, tickers = body.market, body.tickers
    if body.preset:
        market, tickers = await resolve_one(body.preset)  # dynamic fetch (PH-PIPE)
        if not tickers:
            return {"started": False, "detail": f"universe {body.preset!r} resolved to no tickers"}
    if market not in ("US", "KR"):
        return {"started": False, "detail": "US + KR only", "market": market}
    if not tickers:
        return {"started": False, "detail": "tickers required"}
    deferred = await _defer_groups([(Market(market.upper()), tickers)], ["filing_text"])
    return {"started": True, "target": f"{market}:{tickers}", "deferred": deferred, "see": "/admin/queue"}


@router.post(
    "/news/ingest",
    dependencies=[ApiKeyDep],
    summary="▶ Pull news into the RAG index (makes rag__search return real context)",
    description=(
        "Enqueues the **news** pipeline (Google News headlines → RAG) for the given tickers on the "
        "queue. The worker runs it and records an `IngestionJob` (kind `news`); queue state shows in "
        "`/admin/queue`."
    ),
)
async def news_ingest(body: NewsIngestRequest) -> dict:
    if not body.tickers:
        return {"started": False, "detail": "tickers required (broad-market news is the streaming sweep)"}
    try:
        groups = [(Market(body.market.upper()), body.tickers)]
    except ValueError:
        return {"started": False, "detail": f"unknown market {body.market!r}"}
    deferred = await _defer_groups(groups, ["news"])
    return {"started": True, "target": f"{body.market}:{body.tickers}", "deferred": deferred, "see": "/admin/queue"}


# --- Queue (Procrastinate) monitor + control ----------------------------------------------------
@router.get("/queue", dependencies=[ApiKeyDep], summary="Queue overview (Procrastinate jobs + cron sweeps)")
async def queue_status() -> dict:
    return await Q.queue_overview()


@router.get("/queue/jobs", dependencies=[ApiKeyDep], summary="List queue jobs (filter by status/queue)")
async def queue_jobs(status: str | None = None, queue: str | None = None, limit: int = 50) -> dict:
    return {"jobs": await Q.list_queue_jobs(status=status, queue=queue, limit=limit)}


@router.get("/queue/activity", dependencies=[ApiKeyDep],
            summary="Live pipeline activity feed (what's being fetched, from where, with what result)")
async def queue_activity(limit: int = 80, kind: str | None = None, job_id: int | None = None) -> dict:
    """A real-time, granular feed of what the running pipelines are doing — the worker writes a line
    per ticker/step ("[005930] OpenDART 공시 → RAG 320 chunks"), so the admin can watch progress live
    instead of only a status/progress bar. Newest first; optionally scoped to a kind or job."""
    from app.store.jobs import list_activity
    return {"activity": await asyncio.to_thread(list_activity, limit, kind, job_id)}


@router.get("/queue/jobs/{job_id}", dependencies=[ApiKeyDep],
            summary="Job detail — Procrastinate event timeline + linked IngestionJob error")
async def queue_job_detail(job_id: int) -> dict:
    """The diagnostic view for one job: its event timeline (deferred → started → abort_requested →
    failed/succeeded) plus the matching pipeline run's IngestionJob error note — so a stuck/failed
    pipeline shows WHY in the admin, not only in the container logs."""
    return await Q.job_detail(job_id)


@router.post("/queue/jobs/{job_id}/retry", dependencies=[ApiKeyDep], summary="Retry a failed job now")
async def queue_retry(job_id: int) -> dict:
    return await Q.retry_queue_job(job_id)


@router.post("/queue/jobs/{job_id}/cancel", dependencies=[ApiKeyDep], summary="Cancel/abort a job")
async def queue_cancel(job_id: int) -> dict:
    return await Q.cancel_queue_job(job_id)


@router.post("/queue/sweep/{pipeline_id}", dependencies=[ApiKeyDep], summary="▶ Enqueue a pipeline's sweep now")
async def queue_sweep(pipeline_id: str) -> dict:
    return await Q.defer_sweep(pipeline_id)
