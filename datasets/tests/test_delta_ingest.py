"""Delta-ingest behavior (OPS-2): filing cursor skips, the financials store-freshness delta,
the /admin/quota panel, and the filterable /admin/jobs history.

Everything runs against the real SQLite test DB with the upstreams (filing refs / HTML / RAG /
bulk loaders) monkeypatched — no network, no keys. Unique tickers/kinds keep these isolated from
the rest of the suite sharing the same DB file.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import ingest_state as IS
from app.store.db import SessionLocal, init_db
from app.symbols import Market

client = TestClient(app)


# --- filing_text delta: the IngestState cursor skips already-ingested accessions -----------
def _patch_filing_upstreams(monkeypatch, fetched: list, ingested: list):
    from app.store import filing_ingest as FI

    async def fake_refs(market, ticker, limit):
        return {"A1": {"cik": "1", "fetch_url": None, "canonical": "http://x/1"},
                "A2": {"cik": "1", "fetch_url": None, "canonical": "http://x/2"}}

    async def fake_html(market, accn, cik=None, fetch_url=None):
        fetched.append(accn)
        return "<html><body>markup</body></html>"

    def fake_docs(html, market, ticker, accession, source, url, doc_type="filing"):
        return [{"text": "body", "source": source, "doc_type": doc_type,
                 "doc_id": f"{accession}:s.1", "ticker": ticker, "market": market,
                 "accession": accession, "section": "s.1", "url": url}]

    async def fake_rag(rag_url, docs, **kwargs):
        ingested.extend(d["accession"] for d in docs)
        return len(docs)

    monkeypatch.setattr(FI, "filing_refs", fake_refs)
    monkeypatch.setattr(FI, "get_filing_html", fake_html)
    monkeypatch.setattr(FI, "_html_to_docs", fake_docs)
    monkeypatch.setattr(FI, "_ingest_to_rag", fake_rag)


async def test_filing_ingest_delta_skips_done_accessions(monkeypatch):
    from app.store import filing_ingest as FI

    init_db()
    fetched: list[str] = []
    ingested: list[str] = []
    _patch_filing_upstreams(monkeypatch, fetched, ingested)

    t = "DLT1"
    IS.mark_items("filing_text", "US", t, {"A1"})          # A1 was ingested by a prior run
    n = await FI.ingest_filing_text_for_ticker("US", t, mode="delta", rag_url="http://rag.test")
    assert n == 1 and fetched == ["A2"] and ingested == ["A2"]   # only the NEW filing is touched
    assert IS.done_items("filing_text", "US", t) == {"A1", "A2"}  # cursor marked after ingest

    # nothing new → 0, and no download/embedding is spent at all
    fetched.clear(), ingested.clear()
    assert await FI.ingest_filing_text_for_ticker("US", t, mode="delta", rag_url="http://rag.test") == 0
    assert fetched == [] and ingested == []


async def test_filing_ingest_marks_cursor_per_accession_before_a_later_failure(monkeypatch):
    # ING-1: the cursor is marked PER-accession right after each successful RAG ingest — so when a
    # LATER accession's ingest blows up, the earlier ones stay behind the cursor and a delta re-run
    # redoes only the failed tail. (The OLD behavior marked the whole ticker once at loop end, so a
    # mid-list failure marked NEITHER and the next run re-spent the quota on the already-done ones.)
    from app.store import filing_ingest as FI

    init_db()
    fetched: list[str] = []
    _patch_filing_upstreams(monkeypatch, fetched, ingested=[])

    async def rag_ok_a1_then_fail_a2(rag_url, docs, **kwargs):
        if docs[0]["accession"] == "A2":
            raise RuntimeError("RAG ingest timeout")   # A1 embeds fine; A2's embed POST blows up
        return len(docs)

    monkeypatch.setattr(FI, "_ingest_to_rag", rag_ok_a1_then_fail_a2)

    t = "DLTPART"
    with pytest.raises(RuntimeError, match="RAG ingest timeout"):   # the failure propagates out…
        await FI.ingest_filing_text_for_ticker("US", t, mode="delta", rag_url="http://rag.test")
    assert fetched == ["A1", "A2"]                              # both fetched before A2's ingest failed
    assert IS.done_items("filing_text", "US", t) == {"A1"}     # …but A1's cursor was already marked

    # a delta re-run now touches ONLY the still-unfinished A2 (A1 is behind the cursor)
    fetched.clear()
    _patch_filing_upstreams(monkeypatch, fetched, ingested=[])  # A2 succeeds this time
    n = await FI.ingest_filing_text_for_ticker("US", t, mode="delta", rag_url="http://rag.test")
    assert n == 1 and fetched == ["A2"]
    assert IS.done_items("filing_text", "US", t) == {"A1", "A2"}


async def test_filing_ingest_full_mode_reingests_everything(monkeypatch):
    from app.store import filing_ingest as FI

    init_db()
    fetched: list[str] = []
    ingested: list[str] = []
    _patch_filing_upstreams(monkeypatch, fetched, ingested)

    t = "DLT2"
    IS.mark_items("filing_text", "US", t, {"A1"})          # cursor exists, but full ignores it
    n = await FI.ingest_filing_text_for_ticker("US", t, mode="full", rag_url="http://rag.test")
    assert n == 2 and sorted(fetched) == ["A1", "A2"] and sorted(ingested) == ["A1", "A2"]
    # full runs also record the cursor, so the NEXT delta knows the baseline
    assert IS.done_items("filing_text", "US", t) == {"A1", "A2"}


# --- run_backfill delta: skip tickers whose stored statements are still fresh ---------------
async def test_run_backfill_delta_all_fresh_short_circuits(monkeypatch):
    from app.store import jobs as J

    init_db()

    async def boom(tickers=None, zip_path=None, limit=None, on_progress=None):
        raise AssertionError("bulk loader must not run when every ticker is fresh")

    monkeypatch.setattr(J, "bulk_load_us", boom)
    monkeypatch.setattr(IS, "fresh_financials_tickers", lambda market, tickers: {"AAPL", "MSFT"})

    out = await J.run_backfill("US", ["AAPL", "MSFT"], mode="delta")
    assert out["status"] == "success" and out["rows"] == 0 and out["skipped_fresh"] == 2
    # the short-circuit is still an admin-visible job with an honest note (not a silent no-op)
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["status"] == "success" and "델타" in (row["error"] or "")


async def test_run_backfill_delta_loads_only_stale_tickers(monkeypatch):
    from app.store import jobs as J

    init_db()
    seen: dict = {}

    async def fake_us(tickers=None, zip_path=None, limit=None, on_progress=None):
        seen["tickers"] = list(tickers)
        return {t: 10 for t in tickers}

    monkeypatch.setattr(J, "bulk_load_us", fake_us)
    monkeypatch.setattr(IS, "fresh_financials_tickers", lambda market, tickers: {"AAPL"})

    out = await J.run_backfill("US", ["AAPL", "STALE"], mode="delta")
    assert seen["tickers"] == ["STALE"]                       # the fresh ticker is skipped
    assert out["status"] == "success" and out["rows"] == 10
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert "delta" in (row["spec"] or "")


async def test_run_backfill_full_mode_never_consults_freshness(monkeypatch):
    from app.store import jobs as J

    init_db()
    seen: dict = {}

    async def fake_us(tickers=None, zip_path=None, limit=None, on_progress=None):
        seen["tickers"] = list(tickers)
        return {t: 1 for t in tickers}

    def boom(market, tickers):
        raise AssertionError("full mode must not consult the freshness store")

    monkeypatch.setattr(J, "bulk_load_us", fake_us)
    monkeypatch.setattr(IS, "fresh_financials_tickers", boom)
    out = await J.run_backfill("US", ["AAPL", "MSFT"])        # default mode = full
    assert out["status"] == "success" and seen["tickers"] == ["AAPL", "MSFT"]


# --- defer_sweep hands the mode through (admin 'run now' defaults to delta) -----------------
async def test_defer_sweep_passes_mode_through(monkeypatch):
    from app import queue as Q
    from app.store import universes as U

    async def fake_resolve(spec):
        return [(Market.US, ["AAPL"])]

    calls = []

    async def fake_defer(market, tickers, pipeline_id, mode="full"):
        calls.append((market, pipeline_id, mode))
        return 1

    monkeypatch.setattr(U, "resolve_universe", fake_resolve)
    monkeypatch.setattr(Q, "defer_pipeline", fake_defer)
    out = await Q.defer_sweep("filing_text")                  # default = delta, like the cron sweep
    assert out == {"deferred": True, "pipeline_id": "filing_text", "mode": "delta"}
    await Q.defer_sweep("filing_text", mode="full")           # explicit full re-collect
    assert calls == [("US", "filing_text", "delta"), ("US", "filing_text", "full")]


# --- /admin/quota: per-key OpenDART usage today + remaining + block state -------------------
def test_admin_quota_endpoint(monkeypatch):
    import app.providers.kr.opendart as od
    from app.config import settings

    init_db()
    monkeypatch.setattr(settings, "opendart_api_keys", "quota-key-a1x1,quota-key-b2y2", raising=False)
    od.reset_quota_blocks()
    try:
        for _ in range(3):
            IS.record_upstream_call("opendart", "quota-key-a1x1")
        IS.record_upstream_call("opendart", "quota-key-b2y2")

        body = client.get("/admin/quota").json()
        assert body["provider"] == "opendart" and body["day_kst"] == IS.kst_today()
        assert body["limit_per_key"] == 20000                  # settings.opendart_daily_limit default
        by_label = {k["key"]: k for k in body["keys"]}
        assert set(by_label) == {"…a1x1", "…b2y2"}             # masked labels only — never the key
        assert by_label["…a1x1"]["used_today"] == 3 and by_label["…a1x1"]["remaining"] == 20000 - 3
        assert by_label["…b2y2"]["used_today"] == 1 and by_label["…b2y2"]["remaining"] == 20000 - 1
        assert all(k["limit"] == 20000 and k["blocked"] is False for k in body["keys"])
        assert {"day": IS.kst_today(), "key": "…a1x1", "calls": 3} in body["history"]

        # a key parked as 사용한도-초과 shows blocked=True (the other stays usable)
        od.mark_quota_blocked("quota-key-a1x1")
        by_label = {k["key"]: k for k in client.get("/admin/quota").json()["keys"]}
        assert by_label["…a1x1"]["blocked"] is True and by_label["…b2y2"]["blocked"] is False
    finally:
        od.reset_quota_blocks()                                # never poison the rest of the suite


# --- /admin/jobs: kind/market/status filters + total + offset paging ------------------------
def test_admin_jobs_filters_total_and_offset():
    from app.store import jobs as J
    from app.store.models import IngestionJob

    init_db()
    kind = "filtkind"                                          # unique kind → isolated
    a = J.start_job(kind, "US", "a", total=1)
    J.finish_job(a, "success", rows=1)
    b = J.start_job(kind, "US", "b", total=1)
    J.finish_job(b, "error", error="boom")
    c = J.start_job(kind, "KR", "c", total=1)
    J.finish_job(c, "success", rows=2)
    with SessionLocal() as db:                                 # spread started_at → deterministic order
        for jid, mins in ((a, 3), (b, 2), (c, 1)):
            db.get(IngestionJob, jid).started_at = J._now() - timedelta(minutes=mins)
        db.commit()

    body = client.get(f"/admin/jobs?kind={kind}").json()
    assert body["total"] == 3 and [j["id"] for j in body["jobs"]] == [c, b, a]   # newest first

    errs = client.get(f"/admin/jobs?kind={kind}&status=error").json()
    assert errs["total"] == 1 and errs["jobs"][0]["id"] == b

    kr = client.get(f"/admin/jobs?kind={kind}&market=kr").json()                 # case-insensitive
    assert kr["total"] == 1 and kr["jobs"][0]["id"] == c

    # paging: limit slices, offset walks, total stays the filter's denominator
    p1 = client.get(f"/admin/jobs?kind={kind}&limit=2").json()
    p2 = client.get(f"/admin/jobs?kind={kind}&limit=2&offset=2").json()
    assert [j["id"] for j in p1["jobs"]] == [c, b] and [j["id"] for j in p2["jobs"]] == [a]
    assert p1["total"] == p2["total"] == 3
    assert (p1["offset"], p1["limit"]) == (0, 2) and (p2["offset"], p2["limit"]) == (2, 2)
