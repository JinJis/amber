"""Unit + integration tests for the US+KR datasets API.

Mapping logic is tested against synthetic upstream payloads (no network); a few
end-to-end checks use FastAPI's TestClient; one provider path is exercised with
respx-mocked SEC responses.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from datetime import date

from app.filters import ReportPeriodFilters
from app.providers.kr.opendart import (
    INCOME_MAP as KR_INCOME,
    _amount,
    _extract,
    _fiscal_period,
    _kr_date,
)
from app.providers.us.sec_edgar import (
    INCOME_MAP as US_INCOME,
    _assemble,
    _parse_13f,
    _parse_form4,
    _ttm_value,
)
from app.symbols import Market, build_ref, normalize_ticker

client = TestClient(app)


# --- symbol normalization -------------------------------------------------
def test_normalize_us_ticker():
    assert normalize_ticker(Market.US, "aapl") == "AAPL"


def test_normalize_kr_ticker_strips_suffix_and_pads():
    assert normalize_ticker(Market.KR, "5930") == "005930"
    assert normalize_ticker(Market.KR, "005930.KS") == "005930"
    assert normalize_ticker(Market.KR, "035720.KQ") == "035720"


def test_normalize_kr_rejects_alpha():
    with pytest.raises(ValueError):
        normalize_ticker(Market.KR, "AAPL")


def test_build_ref_requires_identifier():
    with pytest.raises(ValueError):
        build_ref(Market.US)


# --- US XBRL assembler ----------------------------------------------------
def test_us_assemble_income_picks_latest_annual():
    gaap = {
        "Revenues": {
            "units": {
                "USD": [
                    {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "form": "10-K", "fy": 2023, "fp": "FY", "accn": "A"},
                    {"start": "2022-01-01", "end": "2022-12-31", "val": 800, "form": "10-K", "fy": 2022, "fp": "FY", "accn": "B"},
                    {"start": "2023-07-01", "end": "2023-09-30", "val": 250, "form": "10-Q", "fy": 2023, "fp": "Q3", "accn": "C"},
                ]
            }
        },
        "NetIncomeLoss": {
            "units": {"USD": [{"start": "2023-01-01", "end": "2023-12-31", "val": 100, "form": "10-K", "fy": 2023, "fp": "FY", "accn": "A"}]}
        },
    }
    rows = _assemble(gaap, US_INCOME, US_INCOME["revenue"] + US_INCOME["net_income"], "annual", 4, instant=False, cik10="0000000001")
    assert len(rows) == 2  # two annual periods, the 10-Q excluded
    assert rows[0]["report_period"] == "2023-12-31"
    assert rows[0]["revenue"] == 1000
    assert rows[0]["net_income"] == 100
    assert rows[0]["accession_number"] == "A"


def test_us_assemble_quarterly_filters_by_duration():
    gaap = {
        "Revenues": {
            "units": {"USD": [
                {"start": "2023-07-01", "end": "2023-09-30", "val": 250, "form": "10-Q", "fy": 2023, "fp": "Q3", "accn": "C"},
                {"start": "2023-01-01", "end": "2023-12-31", "val": 1000, "form": "10-K", "fy": 2023, "fp": "FY", "accn": "A"},
            ]}
        },
    }
    rows = _assemble(gaap, US_INCOME, US_INCOME["revenue"], "quarterly", 4, instant=False, cik10="1")
    assert len(rows) == 1
    assert rows[0]["revenue"] == 250


# --- KR DART extraction ---------------------------------------------------
def test_kr_amount_parsing():
    assert _amount("1,234,567") == 1234567.0
    assert _amount("-") is None
    assert _amount("") is None


def test_kr_extract_income_by_account_id():
    rows = [
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "thstrm_amount": "300,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "9,000"},
        {"sj_div": "CIS", "account_id": "ifrs-full_ProfitLoss", "thstrm_amount": "50,000"},
    ]
    out = _extract(rows, KR_INCOME, {"IS", "CIS"})
    assert out["revenue"] == 300000.0
    assert out["net_income"] == 50000.0
    assert "total_assets" not in out  # BS row ignored for income map


def test_kr_fiscal_period_labels():
    assert _fiscal_period(2025, "11011") == "2025-FY"
    assert _fiscal_period(2025, "11013") == "2025-Q1"
    assert _fiscal_period(2025, "11014") == "2025-Q3"


# --- US TTM ----------------------------------------------------------------
def test_ttm_value_formula():
    # ttm = last FY (1000) + latest YTD (600) - prior-year YTD (500) = 1100
    gaap = {
        "Revenues": {
            "units": {"USD": [
                {"start": "2024-01-01", "end": "2024-12-31", "val": 1000, "form": "10-K", "fy": 2024, "fp": "FY"},
                {"start": "2025-01-01", "end": "2025-06-30", "val": 600, "form": "10-Q", "fy": 2025, "fp": "Q2"},
                {"start": "2024-01-01", "end": "2024-06-30", "val": 500, "form": "10-Q", "fy": 2024, "fp": "Q2"},
            ]}
        }
    }
    val, end = _ttm_value(gaap, ["Revenues"])
    assert val == 1100
    assert end == "2025-06-30"


def test_ttm_falls_back_to_annual_without_interim():
    gaap = {"Revenues": {"units": {"USD": [
        {"start": "2024-01-01", "end": "2024-12-31", "val": 1000, "form": "10-K", "fy": 2024, "fp": "FY"},
    ]}}}
    val, end = _ttm_value(gaap, ["Revenues"])
    assert val == 1000 and end == "2024-12-31"


# --- report_period filter --------------------------------------------------
def test_report_period_filter_bounds():
    f = ReportPeriodFilters(None, date(2023, 1, 1), date(2023, 12, 31), None, None)
    rows = [
        type("R", (), {"report_period": date(2024, 9, 30)})(),
        type("R", (), {"report_period": date(2023, 9, 30)})(),
    ]
    out = f.apply(rows, 10)
    assert len(out) == 1 and out[0].report_period == date(2023, 9, 30)


def test_report_period_inactive_is_passthrough():
    f = ReportPeriodFilters(None, None, None, None, None)
    assert not f.active
    assert f.fetch_limit(4) == 4


# --- insider / 13F parsers + KR date --------------------------------------
def test_parse_form4():
    xml = """<ownershipDocument>
      <issuer><issuerName>Apple Inc.</issuerName></issuer>
      <reportingOwner>
        <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>1</isDirector><officerTitle>CEO</officerTitle></reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>2026-05-27</value></transactionDate>
        <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>1000</value></transactionShares>
          <transactionPricePerShare><value>200</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
        <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable>
    </ownershipDocument>"""
    issuer, owner, title, is_dir, txns = _parse_form4(xml, "AAPL")
    assert issuer == "Apple Inc." and owner == "DOE JANE" and title == "CEO" and is_dir is True
    assert len(txns) == 1
    assert txns[0]["shares"] == -1000  # disposal -> negative
    assert txns[0]["price"] == 200 and txns[0]["owned_after"] == 5000


def test_parse_13f_with_namespace():
    xml = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
      <infoTable>
        <nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass>
        <cusip>037833100</cusip><value>1000</value>
        <shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
      </infoTable>
    </informationTable>"""
    rows = _parse_13f(xml, "2026-03-31", "2026-05-15", "13F-HR", "ACCN")
    assert len(rows) == 1
    assert rows[0].name_of_issuer == "APPLE INC" and rows[0].cusip == "037833100"
    assert rows[0].shares == 50 and rows[0].value_usd == 1000


def test_kr_date_normalization():
    assert _kr_date("20240607") == "2024-06-07"
    assert _kr_date("2024-06-07") == "2024-06-07"
    assert _kr_date("2024.06.07") == "2024-06-07"
    assert _kr_date("") is None


# --- scheduler + selftest classifier --------------------------------------
async def test_resolve_universe_legacy_and_dynamic(monkeypatch):
    from app.store import universes as U

    async def fake_fetch(sid):
        return {"us_sp500": ["AAPL", "MSFT"], "kr_kospi200": ["005930"]}.get(sid, [])

    monkeypatch.setattr(U, "fetch_source", fake_fetch)
    # legacy explicit form (no fetch)
    u = dict((m.value, t) for m, t in await U.resolve_universe("US:AAPL,MSFT;KR:005930"))
    assert u["US"] == ["AAPL", "MSFT"] and u["KR"] == ["005930"]
    # dynamic source ids → fetched at resolve time
    u2 = dict((m.value, t) for m, t in await U.resolve_universe("us_sp500,kr_kospi200"))
    assert u2["US"] == ["AAPL", "MSFT"] and u2["KR"] == ["005930"]
    # mixed: a source + an explicit extra ticker for the same market → de-duplicated union
    u3 = dict((m.value, t) for m, t in await U.resolve_universe("us_sp500;US:AAPL,TSLA"))
    assert u3["US"].count("AAPL") == 1 and "TSLA" in u3["US"]


async def test_queue_periodic_schedules_registered():
    # The asyncio scheduler is replaced by Procrastinate @app.periodic cron sweeps — one per pipeline.
    from app import queue as Q

    sched = {s["pipeline_id"]: s["cron"] for s in await Q._periodic_schedules()}
    assert sched["news"] == "0 * * * *"          # hourly
    assert sched["prices"] == "0 4 * * *"         # daily
    assert sched["financials"] == "0 3 * * 1"     # weekly
    assert set(sched) == {"news", "prices", "financials", "corp_actions", "filing_text"}


async def test_queue_overview_failsafe_without_db(monkeypatch):
    # With the queue DB unreachable, the overview degrades to the (DB-free) schedule instead of 500-ing.
    from app import queue as Q

    async def boom():
        raise RuntimeError("queue DB down")

    monkeypatch.setattr(Q.app.job_manager, "list_queues_async", boom)
    ov = await Q.queue_overview()
    assert ov["queues"] == [] and "error" in ov
    assert {s["pipeline_id"] for s in ov["periodic"]} >= {"news", "prices", "financials"}
    assert "run_pipeline" in ov["tasks"]


def test_router_common_validators_keep_messages():
    # RF-04: shared validators raise the SAME 400 message the inline checks used to.
    import pytest as _pytest

    from app.errors import APIError
    from app.routers._common import INTERVALS, PERIODS, validate_interval, validate_period

    validate_interval("day")          # valid → no raise
    validate_period("ttm")
    with _pytest.raises(APIError) as ei:
        validate_interval("decade")
    assert ei.value.status_code == 400 and ei.value.message == f"interval must be one of {INTERVALS}."
    with _pytest.raises(APIError) as ep:
        validate_period("forever")
    assert ep.value.message == f"period must be one of {PERIODS}."


async def test_router_common_gather_best_effort_skips_failures():
    # RF-04: best-effort fan-out drops items that raise OR return None, keeps the rest, concurrently.
    from app.routers._common import gather_best_effort

    async def fn(x):
        if x == "boom":
            raise RuntimeError("upstream down")
        if x == "none":
            return None
        return x.upper()

    out = await gather_best_effort(["a", "boom", "none", "b"], fn)
    assert out == ["A", "B"]
    assert await gather_best_effort([], fn) == []


def test_shared_number_parsers():
    # RF-02: one comma-aware parser shared by SEC/FMP/KIS (replaces the per-provider _num/_i/_f).
    from app.providers._parse_utils import parse_float, parse_int

    assert parse_float("1,234,567.5") == 1234567.5
    assert parse_float(42) == 42.0          # already-numeric (FMP JSON numbers) still works
    assert parse_float(None) is None and parse_float("") is None and parse_float("n/a") is None
    assert parse_int("1,234") == 1234
    assert parse_int(None) is None and parse_int("") is None and parse_int("3.5") is None
    # the provider aliases point at the shared impl
    from app.providers.us.sec_edgar import _num as sec_num
    from app.providers.kr.kis import _i as kis_i, _f as kis_f
    assert sec_num is parse_float and kis_f is parse_float and kis_i is parse_int


def test_selftest_classifier():
    from app.selftest import _classify

    assert _classify(200, "{}")[0] == "pass"
    assert _classify(503, "FRED served a bot-verification challenge")[0] == "skipped"
    assert _classify(400, "OPENDART_API_KEY is not configured.")[0] == "skipped"
    assert _classify(404, "not found")[0] == "fail"


# --- app-level (no upstream) ----------------------------------------------
def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_macro_banks_static():
    body = client.get("/macro/interest-rates/banks?market=KR").json()
    assert body["banks"][0]["bank"] == "BOK"


def test_scaffold_returns_501():
    r = client.get("/financials/segments?ticker=AAPL&market=US")
    assert r.status_code == 501
    assert r.json()["error"] == "Not Implemented"


def test_missing_param_maps_to_400_envelope():
    r = client.get("/prices?market=US&interval=day&start_date=2024-01-01&end_date=2024-01-02")
    assert r.status_code == 400
    assert set(r.json().keys()) == {"error", "message"}


# --- screener over the store ----------------------------------------------
def test_screener_over_store():
    from datetime import date as _date

    from sqlalchemy import delete

    from app.store.db import SessionLocal, init_db
    from app.store.models import FinancialFact
    from app.store.screener import run_line_items, run_screener

    init_db()
    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.market == "ZZ"))
        for tk, val in [("ZTESTA", 100.0), ("ZTESTB", 10.0)]:
            db.add(
                FinancialFact(
                    market="ZZ", ticker=tk, statement="income", line_item="revenue", value=val,
                    currency="USD", period="annual", report_period=_date(2025, 1, 1),
                    accession_number="t", source="test",
                )
            )
        db.commit()
    try:
        res = run_screener([{"field": "revenue", "operator": "gt", "value": 50}], 10, "annual", "ZZ")
        assert {r["ticker"] for r in res} == {"ZTESTA"}
        li = run_line_items(["ZTESTA"], ["revenue"], "annual", 1)
        assert li and li[0]["revenue"] == 100.0
    finally:
        with SessionLocal() as db:
            db.execute(delete(FinancialFact).where(FinancialFact.market == "ZZ"))
            db.commit()


# --- PH-1: ingestion jobs + backfill operability --------------------------
async def test_ingestion_job_log_lifecycle_and_list():
    from app.store.db import init_db
    from app.store import jobs as J

    init_db()
    jid = J.start_job("backfill", "US", "AAPL · deep")
    J.finish_job(jid, "success", rows=42)
    listed = J.list_jobs(50)
    mine = next(j for j in listed if j["id"] == jid)
    assert mine["status"] == "success" and mine["rows"] == 42 and mine["market"] == "US"
    assert mine["started_at"] and mine["ended_at"]


async def test_run_backfill_records_job(monkeypatch):
    from app.store.db import init_db
    from app.store import jobs as J

    init_db()

    async def fake_us(tickers=None, zip_path=None, limit=None, on_progress=None):
        return {"AAPL": 120, "MSFT": -1}  # one ok, one per-ticker failure

    monkeypatch.setattr(J, "bulk_load_us", fake_us)
    out = await J.run_backfill("US", ["AAPL", "MSFT"], deep=True, limit=None)
    assert out["status"] == "success" and out["rows"] == 120 and out["failed"] == ["MSFT"]
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["status"] == "success" and row["rows"] == 120


def test_reap_stale_running_jobs_and_latest_job():
    # a job left 'running' past the threshold (worker died) is reaped to 'error' so the admin shows
    # a real terminal state instead of a phantom run; latest_job exposes it (with the note) per kind.
    from datetime import timedelta

    from app.store.db import SessionLocal, init_db
    from app.store import jobs as J
    from app.store.models import IngestionJob

    init_db()
    kind = "reaptest"                                              # unique kind → isolated from other tests
    jid = J.start_job(kind, "US", "MSFT", total=1)                 # stuck 'running'
    with SessionLocal() as db:                                      # backdate it past the cutoff
        db.get(IngestionJob, jid).started_at = J._now() - timedelta(hours=2)
        db.commit()
    fresh = J.start_job(kind, "US", "AAPL", total=1)               # recent → must NOT be reaped

    assert J.reap_stale_jobs(kind, "US") == 1
    reaped = J.latest_job(kind, "US")  # latest is the fresh one; check the stale one directly
    with SessionLocal() as db:
        assert db.get(IngestionJob, jid).status == "error"
        assert "미완료" in (db.get(IngestionJob, jid).error or "")
        assert db.get(IngestionJob, fresh).status == "running"     # untouched
    assert reaped is not None and reaped["kind"] == kind


def test_start_job_truncates_long_spec_and_records_pipeline_error():
    # filing_text over 200-500 tickers used to join them all into `spec` (varchar(256)) → the INSERT
    # threw 'value too long' BEFORE the job row existed, failing the run in ~40ms with no record.
    from app.store.db import init_db
    from app.store import jobs as J

    init_db()
    kind = "spectest"                                  # unique kind → isolated from other tests
    huge = ",".join(f"T{i:05d}" for i in range(503))   # ~3.5k chars, like 503 US tickers
    assert len(huge) > 256
    jid = J.start_job(kind, "US", huge, total=503)
    row = next(j for j in J.list_jobs(120) if j["id"] == jid)
    assert len(row["spec"]) <= 256                      # truncated → never overflows the column

    # record_pipeline_error reuses the still-'running' row, finalizing it as error with the traceback
    eid = J.record_pipeline_error(kind, "US", "ValueError: boom traceback")
    assert eid == jid
    after = next(j for j in J.list_jobs(120) if j["id"] == jid)
    assert after["status"] == "error" and "boom" in (after["error"] or "")

    # with no running row for a kind/market, it creates a fresh error row (failure before start_job)
    eid2 = J.record_pipeline_error(kind, "KR", "boom-kr")
    assert eid2 != jid
    kr = J.latest_job(kind, "KR")
    assert kr["status"] == "error" and "boom-kr" in (kr["error"] or "")


def test_pipeline_activity_feed_logs_and_lists():
    # the live feed the admin renders: runners append milestone lines, list_activity returns them
    # newest-first, scoped by kind/job. (Best-effort — never raises.)
    from app.store.db import init_db
    from app.store import jobs as J

    init_db()
    J.log_activity("acttest", "KR", "▶ 시작 · 2종목", job_id=777)
    J.log_activity("acttest", "KR", "[005930] OpenDART → RAG 320 chunks ✓", job_id=777)
    J.log_activity("acttest", "KR", "[000660] 실패 — ReadTimeout", job_id=777, level="error")
    feed = J.list_activity(10, job_id=777)
    assert len(feed) == 3
    assert feed[0]["message"].startswith("[000660]") and feed[0]["level"] == "error"   # newest first
    assert all(r["kind"] == "acttest" for r in feed)
    # scoping by kind works; an unknown kind returns nothing
    assert len(J.list_activity(10, kind="acttest")) >= 3
    assert J.list_activity(10, kind="nope-kind") == []


@respx.mock
async def test_ingest_to_rag_batches_large_filing():
    # a filing yields hundreds of section docs; _ingest_to_rag must split them into bounded POSTs
    # (so RAG's synchronous embed never trips the client timeout) and sum the chunk counts.
    from app.store import news_ingest as N

    route = respx.post("http://rag.test/rag/ingest").mock(
        return_value=httpx.Response(200, json={"chunks": 40}))
    docs = [{"text": f"doc {i}", "source": "SEC", "doc_id": str(i)} for i in range(95)]
    total = await N._ingest_to_rag("http://rag.test", docs)
    # 95 docs / batch 40 → 3 POSTs (40 + 40 + 15), each reporting 40 chunks → summed
    assert route.call_count == 3
    assert total == 120


async def test_run_backfill_records_error(monkeypatch):
    from app.store.db import init_db
    from app.store import jobs as J

    init_db()

    async def boom(tickers=None, zip_path=None, limit=None, on_progress=None):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(J, "bulk_load_us", boom)
    out = await J.run_backfill("US", ["AAPL"], deep=True, limit=None)
    assert out["status"] == "error" and "upstream down" in out["error"]
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["status"] == "error" and "upstream down" in (row["error"] or "")


def test_admin_jobs_endpoint():
    r = client.get("/admin/jobs")
    assert r.status_code == 200 and "jobs" in r.json()


async def test_fetch_source_caches_and_never_fabricates(monkeypatch):
    from app.store import universes as U

    U._CACHE.clear()
    n = {"c": 0}

    async def good():
        n["c"] += 1
        return ["AAPL", "MSFT"]

    monkeypatch.setitem(U.SOURCES, "us_sp500", {**U.SOURCES["us_sp500"], "fetch": good})
    assert await U.fetch_source("us_sp500") == ["AAPL", "MSFT"]
    assert await U.fetch_source("us_sp500") == ["AAPL", "MSFT"] and n["c"] == 1  # 2nd call cached
    assert await U.fetch_source("unknown_id") == []

    # fetch failure with no cache → empty list, never a fabricated/stale-wrong universe
    U._CACHE.clear()

    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setitem(U.SOURCES, "us_sp500", {**U.SOURCES["us_sp500"], "fetch": boom})
    assert await U.fetch_source("us_sp500") == []


async def test_kr_universe_falls_back_to_opendart(monkeypatch):
    # pykrx scrapes KRX/Naver and is blocked on many cloud IPs → KR backfill was failing.
    # When pykrx yields nothing, fall back to the reliable (keyed) OpenDART corp list.
    from app.store import universes as U

    monkeypatch.setattr(U, "_kr_by_cap_sync", lambda market, n: [])  # pykrx blocked/empty

    async def fake_dart(n):
        codes = ["005930", "000660", "035420"]
        return codes[:n] if n else codes

    monkeypatch.setattr(U, "_kr_opendart_tickers", fake_dart)
    assert await U._fetch_kr("KOSPI", 2) == ["005930", "000660"]
    assert "kr_listed" in {u["id"] for u in U.list_presets()}  # the OpenDART-only KR source exists


async def test_commodities_snapshot_grouped_drops_failures(monkeypatch):
    # commodity panel: grouped metals/energy/agri via Yahoo; failures dropped, never faked.
    import app.store.commodities as C

    class _Snap:
        def __init__(self, price):
            self.price, self.day_change, self.day_change_percent, self.time = price, 1.2, 0.8, "2024-01-02 16:00"

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker in ("GC=F", "CL=F"):
                return _Snap(2000.0)
            raise RuntimeError("upstream blocked")  # others dropped
    monkeypatch.setattr(C, "get_prices_provider", lambda m: _Prov())
    data = await C.commodities_snapshot()
    members = [m for g in data["groups"] for m in g["members"]]
    assert {m["ticker"] for m in members} == {"GC=F", "CL=F"}  # only reachable kept
    assert data["source"] == "Yahoo Finance" and {g["name"] for g in data["groups"]} == {"귀금속", "에너지"}


async def test_themes_panel_broad_grouped(monkeypatch):
    # broad thematic coverage via ETF proxies, grouped; failures dropped, never faked.
    import app.store.themes as T

    class _Snap:
        def __init__(self, price):
            self.price, self.day_change, self.day_change_percent, self.time = price, 1.0, 1.5, "2024-01-02 16:00"

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker in ("SOXX", "ICLN", "EWY", "BTC-USD", "091160.KS"):
                return _Snap(100.0)
            raise RuntimeError("blocked")  # everything else dropped
    monkeypatch.setattr(T, "get_prices_provider", lambda m: _Prov())
    data = await T.themes_snapshot()
    members = {m["ticker"] for g in data["groups"] for m in g["members"]}
    assert members == {"SOXX", "ICLN", "EWY", "BTC-USD", "091160.KS"}  # incl. a KR theme ETF
    names = {g["name"] for g in data["groups"]}
    assert {"테크·AI", "지역·국가", "디지털자산", "KR 반도체·테크"} <= names and data["source"] == "Yahoo Finance"


async def test_semiconductor_proxy_panel(monkeypatch):
    # DRAM-spot proxy: SOX index + ETFs + memory makers via Yahoo; labelled NOT a spot price.
    import app.store.semiconductor as S

    class _Snap:
        def __init__(self, price):
            self.price, self.day_change, self.day_change_percent, self.time = price, 1.0, 2.04, "2024-01-02 16:00"

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker in ("^SOX", "MU"):
                return _Snap(14634.7)
            raise RuntimeError("blocked")  # others dropped
    monkeypatch.setattr(S, "get_prices_provider", lambda m: _Prov())
    data = await S.semiconductor_proxy()
    members = [m for g in data["groups"] for m in g["members"]]
    assert {m["ticker"] for m in members} == {"^SOX", "MU"}
    assert "DRAM 현물가" in data["note"] and "현물가가 아닙" in data["note"]


async def test_cross_asset_snapshot_drops_failures(monkeypatch):
    # CE-1: cross-asset snapshot keeps reachable proxies, DROPS failures (never fabricates).
    import app.store.cross_asset as CA

    class _Snap:
        def __init__(self, price):
            self.price, self.day_change, self.day_change_percent, self.time = price, 12.3, 0.45, "2024-01-02 16:00"

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker == "^GSPC":
                return _Snap(5000.0)
            raise RuntimeError("upstream blocked")  # all others fail → dropped

    monkeypatch.setattr(CA, "get_prices_provider", lambda m: _Prov())
    data = await CA.cross_asset_snapshot()
    members = [m for g in data["groups"] for m in g["members"]]
    assert data["source"] == "Yahoo Finance"
    assert any(m["ticker"] == "^GSPC" and m["price"] == 5000.0 for m in members)
    assert members and all(m["price"] is not None for m in members)  # failures omitted, not faked


async def test_ce2_sector_heatmap_ranks_and_drops_failures(monkeypatch):
    # CE-2: sector heatmap keeps reachable ETFs, ranks by day change, DROPS failures.
    import app.store.sectors as SE

    class _Snap:
        def __init__(self, price, pct):
            self.price, self.day_change, self.day_change_percent, self.time = price, 1.0, pct, "2024-01-02 16:00"

    pcts = {"XLK": 2.5, "XLF": -1.0, "XLE": 0.5}

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker in pcts:
                return _Snap(100.0, pcts[ref.ticker])
            raise RuntimeError("upstream blocked")  # all others fail → dropped

    monkeypatch.setattr(SE, "get_prices_provider", lambda m: _Prov())
    data = await SE.sector_heatmap()
    tickers = [s["ticker"] for s in data["sectors"]]
    assert tickers == ["XLK", "XLE", "XLF"]  # ranked by change_percent desc, failures omitted
    assert data["source"] == "Yahoo Finance" and all(s["price"] is not None for s in data["sectors"])


def test_ce2_sector_heatmap_route(monkeypatch):
    import app.routers.market as M

    async def _fake():
        return {"sectors": [{"sector": "기술", "ticker": "XLK", "price": 100.0,
                             "change": 2.0, "change_percent": 2.5, "as_of": "2024-01-02"}],
                "source": "Yahoo Finance", "as_of": "2024-01-02"}
    monkeypatch.setattr(M, "sector_heatmap", _fake)
    b = client.get("/market/sectors").json()
    assert b["source"] == "Yahoo Finance" and b["sectors"][0]["sector"] == "기술"


def test_universe_sources_listed_and_endpoint():
    from app.store.universes import SOURCES, list_presets
    ids = {u["id"] for u in list_presets()}
    assert {"us_sp500", "us_all", "kr_kospi200", "kr_kosdaq150"} <= ids
    assert SOURCES["us_sp500"]["market"] == "US" and SOURCES["kr_kospi200"]["market"] == "KR"
    r = client.get("/admin/universes")
    assert r.status_code == 200 and any(u["id"] == "us_sp500" for u in r.json()["universes"])


async def test_run_backfill_by_preset_sets_progress(monkeypatch):
    from app.store.db import init_db
    from app.store import jobs as J
    from app.store import universes as U

    init_db()

    async def fake_us(tickers=None, zip_path=None, limit=None, on_progress=None):
        for i, _t in enumerate(tickers, 1):
            if on_progress:
                on_progress(i, len(tickers))
        return {t: 10 for t in tickers}

    async def fake_fetch(sid):  # PH-PIPE: the universe is fetched dynamically now
        return ["AAPL", "MSFT", "NVDA"] if sid == "us_sp500" else []

    monkeypatch.setattr(J, "bulk_load_us", fake_us)
    monkeypatch.setattr(U, "fetch_source", fake_fetch)
    out = await J.run_backfill(preset="us_sp500")
    assert out["status"] == "success" and out["rows"] > 0
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["total"] == row["done"] and row["total"] == 3  # progressed to completion
    assert row["spec"].startswith("universe:us_sp500")
    # a source that resolves to nothing is rejected (never fabricated)
    assert (await J.run_backfill(preset="bogus"))["status"] == "error"


# --- PH-2b: news → RAG ingestion pipeline -------------------------------------
class _FakeNewsProvider:
    async def news(self, market, ticker, limit):
        from app.models.generated import News

        return [
            News(ticker=ticker, title=f"{ticker} ships record chips", source="Reuters",
                 date="2026-06-14", url="https://news.example/a"),
            News(ticker=ticker, title="", source="X"),  # no title → skipped
        ]


async def test_run_news_ingest_indexes_headlines(monkeypatch):
    from app.store.db import init_db
    from app.store import jobs as J
    from app.store import news_ingest as N

    init_db()
    captured = {}

    async def fake_ingest(rag_url, docs):
        captured["rag_url"], captured["docs"] = rag_url, docs
        return len(docs)

    monkeypatch.setattr(N, "get_news_provider", lambda mkt: _FakeNewsProvider())
    monkeypatch.setattr(N, "_ingest_to_rag", fake_ingest)
    out = await N.run_news_ingest("US", ["AAPL"], limit=5)

    assert out["status"] == "success" and out["rows"] == 1 and out["docs"] == 1
    doc = captured["docs"][0]
    assert doc["doc_type"] == "news" and doc["ticker"] == "AAPL" and doc["source"] == "Reuters"
    assert doc["text"].startswith("AAPL") and "tenant" not in doc  # global corpus
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["kind"] == "news" and row["status"] == "success" and row["done"] == row["total"]


async def test_run_news_ingest_records_error(monkeypatch):
    from app.store.db import init_db
    from app.store import jobs as J
    from app.store import news_ingest as N

    init_db()

    async def boom(rag_url, docs):
        raise RuntimeError("rag unreachable")

    monkeypatch.setattr(N, "get_news_provider", lambda mkt: _FakeNewsProvider())
    monkeypatch.setattr(N, "_ingest_to_rag", boom)
    out = await N.run_news_ingest("US", ["AAPL"])
    assert out["status"] == "error" and "rag unreachable" in out["error"]
    row = next(j for j in J.list_jobs(50) if j["id"] == out["job_id"])
    assert row["status"] == "error"


def test_admin_news_ingest_endpoint(monkeypatch):
    # the endpoint enqueues the `news` pipeline on the queue and returns started=True
    import app.routers.admin as A

    seen = {}

    async def fake_defer(market, tickers, pipeline_id):
        seen["call"] = (market, tuple(tickers), pipeline_id)
        return 1

    monkeypatch.setattr(A.Q, "defer_pipeline", fake_defer)
    r = client.post("/admin/news/ingest", json={"market": "US", "tickers": ["AAPL"]})
    assert r.status_code == 200 and r.json()["started"] is True and r.json()["deferred"] == 1
    assert seen["call"] == ("US", ("AAPL",), "news")


# --- PH-5: cheap universe-enumeration endpoints ---------------------------
_SEC_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}


@respx.mock
def test_ph5_filings_company_earnings_enumerations_us():
    from app.cache import cache
    cache.clear()  # ensure the respx-mocked SEC index is the one used
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=_SEC_TICKERS)
    )
    # filings/tickers + filings/ciks
    rt = client.get("/filings/tickers?market=US")
    assert rt.status_code == 200 and rt.json()["resource"] == "filings"
    assert set(rt.json()["tickers"]) == {"AAPL", "MSFT"}
    rc = client.get("/filings/ciks?market=US")
    assert rc.status_code == 200 and "0000320193" in rc.json()["ciks"]
    # company/facts/ciks + earnings/tickers reuse the same universe
    assert "0000789019" in client.get("/company/facts/ciks?market=US").json()["ciks"]
    assert "MSFT" in client.get("/earnings/tickers?market=US").json()["tickers"]


def test_ph5_kr_list_ciks_uses_corp_code(monkeypatch):
    import asyncio

    from app.providers.kr import opendart

    async def fake_corp_map():
        return {"005930": {"corp_code": "00126380", "corp_name": "삼성전자"}}

    monkeypatch.setattr(opendart, "_corp_map", fake_corp_map)
    assert asyncio.run(opendart.OpenDartProvider().list_ciks()) == ["00126380"]


def test_ph5_prices_snapshot_market_skips_unpriceable(monkeypatch):
    import app.routers.prices as P
    from app.models.generated import PriceSnapshot

    monkeypatch.setattr(P, "store_tickers", lambda market, limit: ["AAPL", "MSFT"])

    class _Prov:
        async def snapshot(self, ref):
            if ref.ticker == "MSFT":
                raise RuntimeError("no price")  # skipped, never fabricated
            return PriceSnapshot(ticker=ref.ticker, price=185.6)

    monkeypatch.setattr(P, "get_prices_provider", lambda market: _Prov())
    r = client.get("/prices/snapshot/market?market=US&limit=10")
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 1 and snaps[0]["ticker"] == "AAPL"


def test_ph5_endpoints_no_longer_scaffold_501(monkeypatch):
    # /prices/snapshot/market used to be a scaffolded 501; now a real route
    import app.routers.prices as P
    monkeypatch.setattr(P, "store_tickers", lambda market, limit: [])
    assert client.get("/prices/snapshot/market?market=US").status_code == 200


# --- PH-6: historical financial-metrics (store-backed ratios) -------------
def test_ph6_metrics_history_derives_ratios_and_growth():
    from datetime import date

    from sqlalchemy import delete

    from app.store.db import SessionLocal, init_db
    from app.store.metrics_history import metrics_history
    from app.store.models import FinancialFact

    init_db()

    def fact(rp, item, val, accn="0000320193-25-000079", cik="320193"):
        return FinancialFact(market="US", ticker="ZZTEST", statement="x", line_item=item,
                             value=val, period="annual", report_period=rp, source="SEC EDGAR",
                             accession_number=accn, cik=cik)

    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.ticker == "ZZTEST"))
        rows = []
        for rp, rev, gp, ni in [(date(2024, 12, 31), 100.0, 40.0, 10.0),
                                (date(2025, 12, 31), 120.0, 60.0, 24.0)]:
            rows += [fact(rp, "revenue", rev), fact(rp, "gross_profit", gp), fact(rp, "net_income", ni),
                     fact(rp, "total_assets", 200.0), fact(rp, "shareholders_equity", 80.0),
                     fact(rp, "current_assets", 50.0), fact(rp, "current_liabilities", 25.0)]
        db.add_all(rows)
        db.commit()

    hist = metrics_history("US", "ZZTEST", "annual", 8)
    assert len(hist) == 2
    latest = hist[0]  # newest first → 2025
    # each period ties to the exact filing its inputs came from (per-figure provenance)
    assert latest["accession_number"] == "0000320193-25-000079"
    assert latest["filing_url"].endswith("/0000320193-25-000079-index.htm")
    assert abs(latest["gross_margin"] - 0.5) < 1e-9       # 60/120
    assert abs(latest["net_margin"] - 0.2) < 1e-9         # 24/120
    assert abs(latest["return_on_equity"] - 0.3) < 1e-9   # 24/80
    assert abs(latest["current_ratio"] - 2.0) < 1e-9      # 50/25
    assert abs(latest["revenue_growth"] - 0.2) < 1e-9     # (120-100)/100
    assert hist[1].get("revenue_growth") is None          # oldest period has no prior → null, not faked

    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.ticker == "ZZTEST"))
        db.commit()


@respx.mock
def test_ph7_as_reported_returns_raw_xbrl():
    from app.cache import cache
    cache.clear()
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json={"facts": {"us-gaap": {
            "Revenues": {"label": "Revenues", "units": {"USD": [
                {"end": "2024-09-28", "start": "2023-10-01", "val": 391035000000, "form": "10-K",
                 "fy": 2024, "fp": "FY", "filed": "2024-11-01", "accn": "acc-1"}]}},
            "Assets": {"label": "Assets", "units": {"USD": [
                {"end": "2024-09-28", "val": 352755000000, "form": "10-K",
                 "fy": 2024, "fp": "FY", "filed": "2024-11-01", "accn": "acc-1"}]}},
        }}}))
    r = client.get("/financials/as-reported?ticker=AAPL&market=US&period=annual")
    assert r.status_code == 200  # was a scaffolded 501
    body = r.json()
    assert body["ticker"] == "AAPL" and body["periods"]
    p = body["periods"][0]
    assert p["report_period"] == "2024-09-28"
    concepts = {it["concept"]: it["value"] for it in p["line_items"]}
    assert concepts["Revenues"] == 391035000000.0 and concepts["Assets"] == 352755000000.0  # raw, as filed


def test_ph6_metrics_history_endpoint_no_longer_501(monkeypatch):
    import app.routers.metrics as M
    from app.models.generated import FinancialMetricsResponse

    monkeypatch.setattr(M, "metrics_history_models",
                        lambda market, ticker, period, limit: [FinancialMetricsResponse(ticker="AAPL", gross_margin=0.46)])
    r = client.get("/financial-metrics?ticker=AAPL&market=US&period=annual")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL" and body["period"] == "annual"
    assert body["metrics"][0]["gross_margin"] == 0.46


# --- one provider path with mocked upstream -------------------------------
@respx.mock
def test_us_company_facts_with_mocked_sec():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
    )
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Apple Inc.",
                "tickers": ["AAPL"],
                "exchanges": ["Nasdaq"],
                "sic": "3571",
                "sicDescription": "Electronic Computers",
                "category": "Large accelerated filer",
                "addresses": {"business": {"city": "CUPERTINO", "stateOrCountry": "CA"}},
            },
        )
    )
    r = client.get("/company/facts?ticker=AAPL&market=US")
    assert r.status_code == 200
    facts = r.json()["company_facts"]
    assert facts["name"] == "Apple Inc."
    assert facts["cik"] == "0000320193"
    assert facts["exchange"] == "Nasdaq"


# --- company search (U1-01) ----------------------------------------------
def test_rank_company_matches_orders_exact_prefix_substring():
    from app.providers.search_util import rank_company_matches

    rows = [
        {"ticker": "AAPL", "name": "Apple Inc."},
        {"ticker": "APP", "name": "Applovin Corp"},
        {"ticker": "MSFT", "name": "Microsoft (pineapple supplier)"},
    ]
    # "app": exact ticker (APP) first, then ticker-prefix (AAPL), then name-substring (MSFT).
    out = [r["ticker"] for r in rank_company_matches("app", rows)]
    assert out == ["APP", "AAPL", "MSFT"]
    # empty query → no matches; non-matching query → dropped.
    assert rank_company_matches("", rows) == []
    assert rank_company_matches("zzz", rows) == []


@respx.mock
def test_company_search_us_route():
    from app.cache import cache

    cache.clear()  # don't inherit a prior test's ticker index
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json={
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
            "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "Amazon Com Inc"},
        })
    )
    r = client.get("/company/search?q=app&market=US&limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "SEC EDGAR"
    assert body["query"] == "app"
    results = body["results"]
    assert results and results[0]["ticker"] == "AAPL"  # ticker-prefix beats name-substring
    assert results[0]["market"] == "US"
    assert results[0]["cik"] == "0000320193"
    cache.clear()


def test_company_search_kr_provider(monkeypatch):
    import asyncio

    from app.providers.kr import opendart

    async def fake_corp_map():
        return {
            "005930": {"corp_code": "00126380", "corp_name": "삼성전자"},
            "006400": {"corp_code": "00126186", "corp_name": "삼성SDI"},
            "000660": {"corp_code": "00164779", "corp_name": "SK하이닉스"},
        }

    monkeypatch.setattr(opendart, "_corp_map", fake_corp_map)
    results = asyncio.run(opendart.OpenDartProvider().search_companies("삼성", 10))
    names = [r.name for r in results]
    assert "삼성전자" in names and "삼성SDI" in names
    assert all(r.market == "KR" for r in results)
    assert "SK하이닉스" not in names  # not a 삼성 match
    # ticker lookup works too
    by_code = asyncio.run(opendart.OpenDartProvider().search_companies("005930", 10))
    assert by_code[0].name == "삼성전자"


# ==========================================================================
# Expanded coverage: helpers, providers, store, ops, and app-level integration
# ==========================================================================

# --- errors ---------------------------------------------------------------
def test_error_helper_status_codes():
    from app.errors import bad_request, not_found, not_implemented, service_unavailable, unauthorized

    assert bad_request("x").status_code == 400
    assert unauthorized().status_code == 401
    assert not_found("x").status_code == 404
    assert not_implemented("x").status_code == 501
    assert service_unavailable("x").status_code == 503


# --- cache ----------------------------------------------------------------
async def test_ttl_cache_caches():
    from app.cache import TTLCache

    c = TTLCache(60)
    calls = []

    async def factory():
        calls.append(1)
        return 42

    assert await c.get_or_set("k", factory) == 42
    assert await c.get_or_set("k", factory) == 42
    assert len(calls) == 1  # second call served from cache


async def test_ttl_cache_single_flight():
    # RF-06: concurrent cold-cache callers for one key invoke the factory exactly once; different
    # keys still load independently. (This is the guarantee the KIS token now relies on.)
    import asyncio as _aio

    from app.cache import TTLCache

    c = TTLCache(60)
    calls: list[str] = []

    async def factory(tag: str):
        calls.append(tag)
        await _aio.sleep(0.02)  # hold so concurrent callers pile up on the per-key lock
        return tag

    results = await _aio.gather(*[c.get_or_set("k", lambda: factory("k")) for _ in range(5)])
    assert results == ["k"] * 5 and calls.count("k") == 1     # factory ran once for the 5 callers
    await c.get_or_set("other", lambda: factory("other"))
    assert calls.count("other") == 1                          # a different key loads independently


# --- symbols --------------------------------------------------------------
def test_kr_market_suffix():
    from app.symbols import kr_market_suffix

    assert kr_market_suffix("005930", "KOSPI") == "005930.KS"
    assert kr_market_suffix("035720", "KOSDAQ") == "035720.KQ"


def test_build_ref_with_cik_only():
    r = build_ref(Market.US, cik="320193")
    assert r.cik == "320193" and r.ticker == ""


# --- US XBRL helpers ------------------------------------------------------
def test_ce12_kis_volume_rank_and_investor_flow(monkeypatch):
    # CE-12: KIS rankings + investor flows — mapped from the (verified) KIS output shapes.
    import asyncio

    import app.providers.kr.kis as K

    async def fake_get(path, tr_id, params):
        if "volume-rank" in path:
            return [{"data_rank": "1", "mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                     "stck_prpr": "337250", "prdy_ctrt": "-4.6", "acml_vol": "12345678", "acml_tr_pbmn": "4160000000000"}]
        if "ranking/fluctuation" in path:
            assert params["FID_RANK_SORT_CLS_CODE"] == "1"  # down → losers
            return [{"data_rank": "1", "stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
                     "stck_prpr": "100000", "prdy_ctrt": "-9.9", "acml_vol": "555"}]
        if "ranking/market-cap" in path:
            return [{"data_rank": "1", "mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
                     "stck_prpr": "334000", "prdy_ctrt": "-5.52", "stck_avls": "19526571", "mrkt_whol_avls_rlim": "24.03"}]
        if "etfetn" in path:
            return [{"hts_kor_isnm": "KODEX 200", "stck_prpr": "141675", "nav": "141792.70",
                     "dprt": "-0.06", "prdy_ctrt": "-4.50", "nav_prdy_ctrt": "-4.40"}]
        if "inquire-investor" in path:
            return [{"stck_bsop_date": "20260622", "stck_clpr": "353500",
                     "prsn_ntby_qty": "-100", "frgn_ntby_qty": "5000", "orgn_ntby_qty": "-2000"}]
        return []
    monkeypatch.setattr(K, "_get", fake_get)

    vr = asyncio.run(K.volume_rank(30))
    assert vr["ranking"] == "volume" and vr["results"][0]["ticker"] == "005930"
    assert vr["results"][0]["change_percent"] == -4.6 and vr["results"][0]["value"] == 4160000000000
    fl = asyncio.run(K.investor_flow("005930", 10))
    assert fl["flows"][0]["foreign_net"] == 5000 and fl["flows"][0]["institution_net"] == -2000
    # CE-12 extension: fluctuation ranking (losers) + ETF NAV/괴리율
    fr = asyncio.run(K.fluctuation_rank("down", 30))
    assert fr["direction"] == "down" and fr["results"][0]["ticker"] == "000660" and fr["results"][0]["change_percent"] == -9.9
    etf = asyncio.run(K.etf_nav("069500"))
    assert etf["nav"] == 141792.70 and etf["premium_discount_pct"] == -0.06 and etf["name"] == "KODEX 200"
    mc = asyncio.run(K.market_cap_rank(30))
    assert mc["ranking"] == "market_cap" and mc["results"][0]["ticker"] == "005930"
    assert mc["results"][0]["market_cap_eok"] == 19526571 and mc["results"][0]["market_weight_pct"] == 24.03

    # missing creds → clear error
    monkeypatch.setattr(K.settings, "kis_app_key", "", raising=False)
    import pytest
    with pytest.raises(Exception):
        K._creds()


def test_ce12_kis_prices_provider(monkeypatch):
    # KIS-PRICES: realtime snapshot + paginated daily OHLCV, as a drop-in PricesProvider.
    import asyncio
    from datetime import date as _date

    import app.providers.kr.kis as K
    from app.symbols import Market, build_ref

    async def fake_get(path, tr_id, params, output_key="output"):
        if "inquire-price" in path:
            return [{"stck_prpr": "338500", "prdy_vrss": "-15000", "prdy_ctrt": "-4.24"}]
        if "inquire-daily-itemchartprice" in path:
            end = params["FID_INPUT_DATE_2"]
            if end >= "20260610":  # first window → recent bars
                return [{"stck_bsop_date": "20260612", "stck_oprc": "100", "stck_hgpr": "110",
                         "stck_lwpr": "95", "stck_clpr": "105", "acml_vol": "1000"},
                        {"stck_bsop_date": "20260610", "stck_oprc": "98", "stck_hgpr": "102",
                         "stck_lwpr": "97", "stck_clpr": "100", "acml_vol": "900"}]
            return [{"stck_bsop_date": "20260602", "stck_oprc": "90", "stck_hgpr": "92",
                     "stck_lwpr": "88", "stck_clpr": "91", "acml_vol": "800"}]  # older window
        return []
    monkeypatch.setattr(K, "_get", fake_get)

    p = K.KisPricesProvider()
    ref = build_ref(Market.KR, "005930")
    snap = asyncio.run(p.snapshot(ref))
    assert snap.price == 338500.0 and snap.day_change_percent == -4.24 and snap.ticker == "005930"
    bars = asyncio.run(p.prices(ref, "day", _date(2026, 6, 1), _date(2026, 6, 23)))
    # paginated across two windows, deduped + sorted ascending by date
    assert [b.time for b in bars] == ["2026-06-02", "2026-06-10", "2026-06-12"]
    assert bars[0].close == 91.0 and bars[-1].high == 110.0 and bars[-1].volume == 1000


def test_ce11_fmp_estimates_and_earnings_calendar(monkeypatch):
    # CE-11: consensus estimates (mapped) + earnings calendar (client-side filtered by symbol).
    import asyncio

    import app.providers.us.fmp as F

    async def fake_get(path, params):
        if path == "analyst-estimates":
            return [{"date": "2026-09-27", "revenueAvg": 4.5e11, "epsAvg": 7.2, "netIncomeAvg": 1.1e11,
                     "numAnalystsRevenue": 30}]
        if path == "earnings-calendar":  # market-wide; provider filters to the symbol
            return [{"symbol": "MSFT", "date": "2026-04-29", "epsActual": 4.27, "epsEstimated": 4.06,
                     "revenueActual": 8.3e10, "revenueEstimated": 8.1e10},
                    {"symbol": "AAPL", "date": "2026-04-30", "epsActual": 2.01, "epsEstimated": 1.95,
                     "revenueActual": 1.11e11, "revenueEstimated": 1.09e11}]
        return []
    monkeypatch.setattr(F, "_get", fake_get)

    est = asyncio.run(F.consensus_estimates("AAPL", "annual", 5))
    assert est["estimates"][0]["revenue_avg"] == 4.5e11 and est["estimates"][0]["eps_avg"] == 7.2
    assert "컨센서스" in est["source"]
    cal = asyncio.run(F.earnings_calendar("AAPL", 8))
    assert [e["date"] for e in cal["events"]] == ["2026-04-30"]  # only AAPL kept
    assert abs(cal["events"][0]["eps_surprise"] - (2.01 - 1.95)) < 1e-9

    # missing key → clear error (not a crash)
    monkeypatch.setattr(F.settings, "fmp_api_key", "", raising=False)
    import pytest
    with pytest.raises(Exception):
        F._key()


def test_ce_health_upstream_probe(monkeypatch):
    # CE-HEALTH: classify each upstream — ok / degraded / down / key-missing.
    import asyncio

    import app.store.upstream_health as UH
    from app.config import settings

    class _Resp:
        def __init__(self, code):
            self.status_code = code

    class _Client:
        def __init__(self, mode):
            self.mode = mode

        async def get(self, url, **kw):
            if self.mode == "degraded":
                return _Resp(503)
            if self.mode == "down":
                raise RuntimeError("unreachable")
            return _Resp(200)

    keyless = {"id": "x", "name": "X", "url": "u", "key": None}
    assert asyncio.run(UH._probe(_Client("ok"), keyless))["status"] == "ok"
    assert asyncio.run(UH._probe(_Client("degraded"), keyless))["status"] == "degraded"
    down = asyncio.run(UH._probe(_Client("down"), keyless))
    assert down["status"] == "down" and down["reachable"] is False
    # required key absent → key-missing even if reachable; present → ok
    keyed = {"id": "od", "name": "OD", "url": "u", "key": "opendart_api_key"}
    monkeypatch.setattr(settings, "opendart_api_key", "", raising=False)
    r = asyncio.run(UH._probe(_Client("ok"), keyed))
    assert r["status"] == "key-missing" and r["key_present"] is False
    monkeypatch.setattr(settings, "opendart_api_key", "k", raising=False)
    assert asyncio.run(UH._probe(_Client("ok"), keyed))["status"] == "ok"


def test_ce9_macro_catalog_grouping_and_panel(monkeypatch):
    # CE-9: catalog browses by group/region; the panel snapshots a region's latest + change.
    import asyncio

    import app.providers.macro_indicators as MI

    cat = MI.list_indicators()
    assert all("group" in c for c in cat) and {"물가", "고용", "성장", "금리"} <= {c["group"] for c in cat}
    assert {c["slug"] for c in MI.list_indicators(group="물가")} >= {"cpi", "core_cpi"}
    assert all(c["region"] == "US" for c in MI.list_indicators(region="US"))
    assert "US" in MI.list_regions()

    # region_panel batches BLS (fresh, from BLS API) + DBnomics (rest) — patch both sources.
    async def fake_dbn(slug, limit=2):
        return [{"date": "2026-03", "value": 3.0}, {"date": "2026-04", "value": 3.2}]

    async def fake_bls(ids, years=3):
        return {i: [{"date": "2026-04", "value": 4.1}, {"date": "2026-05", "value": 4.3}] for i in ids}
    monkeypatch.setattr(MI, "_dbnomics_obs", fake_dbn)
    monkeypatch.setattr(MI.bls_api, "fetch_bls", fake_bls)
    panel = asyncio.run(MI.region_panel("US"))
    assert panel["region"] == "US" and panel["indicators"]
    by = {r["slug"]: r for r in panel["indicators"]}
    # BLS-backed series now read fresh from the BLS API (not the frozen DBnomics mirror)
    assert by["unemployment"]["source"] == "BLS" and by["unemployment"]["latest"] == 4.3
    assert by["unemployment"]["as_of"] == "2026-05" and by["unemployment"]["stale"] is False
    assert abs(by["unemployment"]["change"] - 0.2) < 1e-9
    # non-BLS series (BEA GDP) still come from DBnomics
    assert by["gdp_growth"]["source"] == "DBnomics" and by["gdp_growth"]["latest"] == 3.2


def test_ce7_backtest_over_store():
    # CE-7: buy-and-hold backtest over ingested PriceBar — descriptive past performance.
    from datetime import date as _date

    from sqlalchemy import delete

    from app.store.db import SessionLocal, init_db
    from app.store.models import PriceBar
    from app.store.backtest import run_backtest

    init_db()
    bars = {  # ticker → [(date, close)]
        "ZBA": [(_date(2024, 1, 2), 100.0), (_date(2024, 6, 3), 110.0), (_date(2025, 1, 2), 121.0)],
        "ZBB": [(_date(2024, 1, 2), 50.0), (_date(2024, 6, 3), 55.0), (_date(2025, 1, 2), 60.5)],
    }
    with SessionLocal() as db:
        db.execute(delete(PriceBar).where(PriceBar.market == "ZB"))
        for tk, rows in bars.items():
            for bd, close in rows:
                db.add(PriceBar(market="ZB", ticker=tk, interval="day", bar_date=bd, close=close, source="t"))
        db.commit()
    try:
        res = run_backtest("ZB", [{"ticker": "ZBA", "weight": 0.5}, {"ticker": "ZBB", "weight": 0.5}], initial=10000.0)
        assert abs(res["final"] - 12100.0) < 1e-6                  # both +21%
        assert abs(res["metrics"]["total_return"] - 0.21) < 1e-6
        assert res["metrics"]["max_drawdown"] <= 0 and res["curve"][0]["value"] == 10000.0
        # missing coverage → honest note, never fabricated
        no = run_backtest("ZB", [{"ticker": "NOPE", "weight": 1.0}])
        assert no["results"] is None and no["note"]
    finally:
        with SessionLocal() as db:
            db.execute(delete(PriceBar).where(PriceBar.market == "ZB"))
            db.commit()


def test_ce6_quant_factor_screen_over_store():
    # CE-6: compute factors from FinancialFact + PriceBar, then filter/rank.
    from datetime import date as _date, timedelta

    from sqlalchemy import delete

    from app.store.db import SessionLocal, init_db
    from app.store.models import FinancialFact, PriceBar
    from app.store.quant import compute_factors, run_quant_screen

    init_db()
    facts = {  # ticker → {line_item: value}
        "ZQA": {"revenue": 1000.0, "net_income": 200.0, "shareholders_equity": 800.0,
                "earnings_per_share": 4.0, "outstanding_shares": 50.0, "gross_profit": 600.0},
        "ZQB": {"revenue": 500.0, "net_income": 10.0, "shareholders_equity": 1000.0,
                "earnings_per_share": 0.2, "outstanding_shares": 50.0, "gross_profit": 100.0},
    }
    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.market == "ZQ"))
        db.execute(delete(PriceBar).where(PriceBar.market == "ZQ"))
        for tk, items in facts.items():
            for li, val in items.items():
                db.add(FinancialFact(market="ZQ", ticker=tk, statement="x", line_item=li, value=val,
                                     currency="USD", period="annual", report_period=_date(2025, 1, 1),
                                     accession_number="t", source="test"))
        # price window: ZQA rose 100→120, ZQB fell 50→40
        for tk, (p0, p1) in {"ZQA": (100.0, 120.0), "ZQB": (50.0, 40.0)}.items():
            db.add(PriceBar(market="ZQ", ticker=tk, interval="day", bar_date=_date.today() - timedelta(days=200), close=p0, source="t"))
            db.add(PriceBar(market="ZQ", ticker=tk, interval="day", bar_date=_date.today(), close=p1, source="t"))
        db.commit()
    try:
        f = compute_factors("ZQ")["ZQA"]
        assert abs(f["pe"] - 120.0 / 4.0) < 1e-6           # price/eps = 30
        assert abs(f["market_cap"] - 120.0 * 50.0) < 1e-6  # 6000
        assert abs(f["roe"] - 200.0 / 800.0) < 1e-6        # 0.25
        assert abs(f["return_window"] - (120.0 / 100.0 - 1)) < 1e-6  # +20%
        # screen: ROE > 20% → only ZQA; rank by roe desc
        res = run_quant_screen([{"field": "roe", "operator": "gt", "value": 0.2}],
                               sort="roe", order="desc", limit=10, market="ZQ")
        assert [r["ticker"] for r in res["results"]] == ["ZQA"] and res["count"] == 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(FinancialFact).where(FinancialFact.market == "ZQ"))
            db.execute(delete(PriceBar).where(PriceBar.market == "ZQ"))
            db.commit()


def test_ce5_valuation_models_math():
    # CE-5: transparent user-input calculators. Verify the arithmetic, not a forecast.
    from app.store import valuation as V

    # DCF: base FCF 100, g=10%, r=12%, 5y, terminal 2% — value must be positive + net-debt aware
    d = V.dcf(100.0, 10.0, 50.0, growth=0.10, discount=0.12, years=5, terminal_growth=0.02)
    assert d and len(d["rows"]) == 5 and abs(d["rows"][0]["fcf"] - 110.0) < 1e-6  # 100*1.1
    assert d["enterprise_value"] > d["pv_explicit"] > 0  # EV includes the terminal value
    assert abs(d["equity_value"] - (d["enterprise_value"] - 50.0)) < 1e-6
    assert abs(d["value_per_share"] - d["equity_value"] / 10.0) < 1e-6
    # guardrail of the math: discount must exceed terminal growth
    import pytest
    with pytest.raises(ValueError):
        V.dcf(100.0, 10.0, 0.0, growth=0.10, discount=0.02, years=5, terminal_growth=0.03)
    # DDM Gordon: D0=2, g=3%, r=8% → 2*1.03/(0.08-0.03)
    dd = V.ddm(2.0, growth=0.03, discount=0.08)
    assert abs(dd["value_per_share"] - (2.0 * 1.03 / 0.05)) < 1e-6
    # RIM: BVPS 50, ROE 15%, r 10% → value > book (positive residual income)
    r = V.rim(50.0, 0.15, discount=0.10, years=5, growth=0.04)
    assert r and r["value_per_share"] > 50.0 and len(r["rows"]) == 5
    # insufficient data → None (never fabricated)
    assert V.dcf(None, 10.0, 0.0, growth=0.1, discount=0.1, years=5, terminal_growth=0.02) is None
    assert V.ddm(None, growth=0.03, discount=0.08) is None


def test_ce5_valuation_endpoint(monkeypatch):
    import app.routers.valuation as VR

    async def fake_base(market, ticker):
        return {"base_fcf": 1000.0, "shares": 100.0, "net_debt": 200.0, "fcf_history": [1000.0],
                "equity": 5000.0, "net_income": 750.0, "bvps": 50.0, "roe": 0.15,
                "as_of": "2025-09-27", "accession": "acc1"}
    monkeypatch.setattr(VR.V, "base_inputs", fake_base)

    b = client.get("/valuation?ticker=AAPL&model=dcf&growth_rate=0.08&discount_rate=0.10&years=5").json()
    assert b["model"] == "dcf" and b["value_per_share"] and b["breakdown"]["rows"]
    assert "예측" in b["disclaimer"] and b["as_of"] == "2025-09-27"  # always labelled, sourced
    # DDM needs the user's dividend; without it → honest note, no fabrication
    nod = client.get("/valuation?ticker=AAPL&model=ddm&growth_rate=0.03&discount_rate=0.08").json()
    assert nod["value_per_share"] is None and nod["note"]
    yesd = client.get("/valuation?ticker=AAPL&model=ddm&dividend_per_share=2&growth_rate=0.03&discount_rate=0.08").json()
    assert yesd["value_per_share"]
    # bad math (discount <= terminal) → 400
    assert client.get("/valuation?ticker=AAPL&model=dcf&discount_rate=0.01&terminal_growth=0.03").status_code == 400


def test_kr_filings_rank_prioritizes_substantive_reports(monkeypatch):
    # the bug: DART date-order floods the list with 지분/소유 reports → 사업보고서 buried.
    # fix: rank so 정기보고서 surfaces first; filing_type post-filters by report name.
    import asyncio
    from app.providers.kr import opendart
    from app.symbols import Market, build_ref

    rows = [
        {"rcept_no": "1", "rcept_dt": "20260616", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"},
        {"rcept_no": "2", "rcept_dt": "20260615", "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서"},
        {"rcept_no": "3", "rcept_dt": "20260514", "report_nm": "분기보고서 (2026.03)"},
        {"rcept_no": "4", "rcept_dt": "20260331", "report_nm": "사업보고서 (2025.12)"},
        {"rcept_no": "5", "rcept_dt": "20260520", "report_nm": "주요사항보고서(유상증자결정)"},
    ]

    async def fake_dart_json(path, params):
        return {"status": "000", "list": rows}

    monkeypatch.setattr(opendart, "_dart_json", fake_dart_json)

    async def fake_corp_code(ref):
        return "00164779"
    monkeypatch.setattr(opendart, "_corp_code", fake_corp_code)

    ref = build_ref(Market.KR, "000660")
    out = asyncio.run(opendart.OpenDartProvider().filings(ref, None, 3))
    # 정기보고서(분기/사업) first, then 주요사항 — ownership reports pushed out of the top 3
    assert [f.filing_type for f in out][:2] == ["분기보고서 (2026.03)", "사업보고서 (2025.12)"]
    assert all("소유상황" not in (f.filing_type or "") for f in out)
    # filing_type filter → only the matching report names
    only = asyncio.run(opendart.OpenDartProvider().filings(ref, ["사업보고서"], 10))
    assert [f.filing_type for f in only] == ["사업보고서 (2025.12)"]


def test_ce14_ir_materials_filters_by_market(monkeypatch):
    # CE-14 IR 자료실: US → 8-K, KR → 주요사항보고서 (the IR vehicle per market).
    from app.models.generated import Filing
    import app.routers.filings as F

    captured = {}

    class _Fake:
        async def filings(self, ref, filing_types, limit):
            captured["types"] = filing_types
            return [Filing(cik=1, accession_number="a", filing_type=(filing_types or ["?"])[0],
                           filing_date="2026-06-01", ticker=ref.ticker, url="https://x")]
    monkeypatch.setattr(F, "get_filings_provider", lambda m: _Fake())

    us = client.get("/filings/ir?ticker=AAPL&market=US").json()
    assert captured["types"] == ["8-K"] and us["filings"][0]["filing_type"] == "8-K"
    client.get("/filings/ir?ticker=005930&market=KR").json()
    assert captured["types"] == ["주요사항보고서"]


def test_filing_search_ingests_on_demand_then_returns_passages(monkeypatch):
    # on-demand RAG ingest: corpus empty for a ticker → ingest its filings, then search again.
    import app.routers.filings as F
    calls = {"search": 0, "ingest": 0}
    hit = {"text": "공급망 다변화로 ... AI 데이터센터 수요 ...", "source": "OpenDART (FSS)",
           "doc_type": "filing", "ticker": "000660", "market": "KR",
           "accession": "20260331000123", "section": "p.42", "url": "https://dart.fss.or.kr/x"}

    async def fake_search(rag_url, query, ticker, market, top_k):
        calls["search"] += 1
        return [hit] if calls["ingest"] > 0 else []  # corpus empty until the ticker is ingested

    async def fake_ingest(market, ticker, limit=2, rag_url=None):
        calls["ingest"] += 1
        return 12
    monkeypatch.setattr("app.store.news_ingest._search_rag", fake_search)
    monkeypatch.setattr("app.store.filing_ingest.ingest_filing_text_for_ticker", fake_ingest)

    body = client.get("/filings/search?ticker=000660&query=공급망&market=KR").json()
    assert calls["ingest"] == 1 and calls["search"] == 2  # search → empty → ingest → search
    assert body["ingested"] is True and body["ticker"] == "000660"
    assert body["hits"] and body["hits"][0]["accession"] == "20260331000123"  # RAG `{hits}` shape → cited

    # already-ingested ticker: first search returns hits → no second search, no ingest (fast path)
    calls["search"] = 0
    again = client.get("/filings/search?ticker=000660&query=공급망&market=KR").json()
    assert calls["ingest"] == 1 and calls["search"] == 1 and again["ingested"] is False and again["hits"]


def test_filing_url_and_fiscal_label():
    from app.providers.us.sec_edgar import _filing_url, _fiscal_label

    # the filing *index page*, not the bare directory listing
    assert _filing_url("0000320193", "0000320193-25-000079") == \
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    assert _filing_url("1", None) is None
    assert _fiscal_label({"fy": 2025, "fp": "FY"}) == "2025-FY"
    assert _fiscal_label({}) is None


def test_filing_link_canonical_per_market():
    from app.store.provenance import filing_link
    # US → SEC index page (needs CIK)
    assert filing_link("US", "0000320193-25-000079", "320193") == \
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    assert filing_link("US", "0000320193-25-000079", None) is None  # no CIK → no SEC link
    # KR → DART rcpNo viewer (deterministic from the receipt number alone)
    assert filing_link("KR", "20260605000073") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260605000073"
    assert filing_link("US", None, "320193") is None


def test_days_between():
    from app.providers.us.sec_edgar import _days_between

    assert _days_between("2025-06-30", "2024-06-30") == 365
    assert _days_between("bad", "x") == 10**6


def test_latest_instant_rows():
    from app.providers.us.sec_edgar import BALANCE_MAP, _latest_instant_rows

    gaap = {"Assets": {"units": {"USD": [
        {"end": "2025-12-31", "val": 1000, "form": "10-Q", "fy": 2025},
        {"end": "2024-12-31", "val": 900, "form": "10-K", "fy": 2024},
    ]}}}
    rows = _latest_instant_rows(gaap, BALANCE_MAP, ["Assets"], 1)
    assert rows[0]["report_period"] == "2025-12-31" and rows[0]["total_assets"] == 1000


def test_parse_13f_no_namespace_and_putcall():
    from app.providers.us.sec_edgar import _parse_13f

    xml = (
        "<informationTable><infoTable><nameOfIssuer>X CORP</nameOfIssuer>"
        "<cusip>000000000</cusip><value>5</value>"
        "<shrsOrPrnAmt><sshPrnamt>2</sshPrnamt></shrsOrPrnAmt><putCall>Put</putCall></infoTable>"
        "</informationTable>"
    )
    rows = _parse_13f(xml, None, None, "13F-HR", "A")
    assert len(rows) == 1 and rows[0].name_of_issuer == "X CORP"
    assert rows[0].value_usd == 5 and rows[0].shares == 2 and rows[0].put_call is not None


# --- KR DART helpers ------------------------------------------------------
def test_kr_extract_balance_and_cashflow():
    from app.providers.kr.opendart import BALANCE_MAP as KB, CASHFLOW_MAP as KC, _extract

    b = [{"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "1,000"}]
    assert _extract(b, KB, {"BS"})["total_assets"] == 1000.0
    c = [{"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities", "thstrm_amount": "50"}]
    assert _extract(c, KC, {"CF"})["net_cash_flow_from_operations"] == 50.0


def test_kr_periods_annual_and_quarterly():
    from app.providers.kr.opendart import _periods

    annual = _periods("annual", 3)
    assert all(code == "11011" for _, code, _ in annual) and annual[0][2].endswith("-12-31")
    quarterly = _periods("quarterly", 2)
    assert any(code in ("11013", "11012", "11014") for _, code, _ in quarterly)


# --- yahoo / stooq / news / fred / ecos provider helpers ------------------
def test_yahoo_symbols_intervals_at():
    from app.providers.us.yahoo import _INTERVALS, _at, _symbols
    from app.symbols import SecurityRef

    assert _symbols(SecurityRef(Market.US, "AAPL")) == ["AAPL"]
    assert _symbols(SecurityRef(Market.KR, "005930")) == ["005930.KS", "005930.KQ"]
    assert _INTERVALS["day"] == "1d" and _INTERVALS["year"] == "1mo"
    assert _at([1, 2, 3], 1) == 2 and _at([1], 5) is None and _at(None, 0) is None


def test_stooq_parse_and_price():
    from app.providers.us.stooq import _parse_csv, _to_price

    rows = _parse_csv("Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,100\n")
    assert len(rows) == 1
    p = _to_price(rows[0])
    assert p.close == 1.5 and p.volume == 100 and p.time == "2024-01-02"
    assert _parse_csv("<html>blocked</html>") == []


def test_news_to_date():
    from app.providers.news import _to_date

    assert _to_date("Mon, 09 Jun 2026 12:00:00 GMT") == "2026-06-09"
    assert _to_date(None) is None and _to_date("garbage") is None


async def test_news_query_for_us():
    from app.providers.news import _query_for

    assert await _query_for(Market.US, "AAPL") == "AAPL stock"
    assert await _query_for(Market.US, None) == "stock market"


def test_fred_series_and_rate():
    import pytest

    from app.errors import APIError
    from app.providers.us.fred import _series, _to_rate

    assert _series("FED")[1] == "DFEDTARU"
    with pytest.raises(APIError):
        _series("NOPE")
    assert _to_rate("FED", "Fed", {"value": "5.5", "date": "2025-01-01"}).rate == 5.5
    assert _to_rate("FED", "Fed", {"value": ".", "date": "x"}) is None


def test_ecos_helpers():
    from datetime import date as _date

    from app.providers.kr.ecos import _bank, _fmt, _to_iso

    assert _to_iso("20250101", "D") == "2025-01-01"
    assert _to_iso("202501", "M") == "2025-01-01"
    assert _fmt(_date(2025, 1, 1), "D") == "20250101"
    assert _bank("BOK")[1] == "722Y001"


# --- PH-MACRO: DBnomics (BIS) cloud-safe macro + FRED fallback ------------
def test_dbnomics_helpers():
    import pytest

    from datetime import date as _date

    from app.errors import APIError
    from app.providers.us.dbnomics import _parse_period, _series, _to_rate

    assert _series("FED")[1] == "US"
    assert _series("ecb")[1] == "XM"  # case-insensitive; euro area
    with pytest.raises(APIError):
        _series("NOPE")
    assert _parse_period("2025-07-08") == _date(2025, 7, 8)
    assert _parse_period("2025-06") == _date(2025, 6, 1)  # monthly tolerated
    assert _parse_period("garbage") is None
    assert _to_rate("FED", "Fed", "2025-07-08", 4.375).rate == 4.375
    assert _to_rate("FED", "Fed", "2025-07-08", "NA") is None  # missing marker
    assert _to_rate("FED", "Fed", "bad-period", 4.0) is None


_DBN_URL = "https://api.db.nomics.world/v22/series/BIS/WS_CBPOL"


@respx.mock
async def test_dbnomics_provider_mocked():
    from datetime import date as _date

    from app.providers.us.dbnomics import DBnomicsProvider

    payload = {"series": {"docs": [{"series_code": "D.US",
                                    "period": ["2025-05-01", "2025-06-01", "2025-07-08"],
                                    "value": ["NA", 4.25, 4.375]}]}}
    respx.get(f"{_DBN_URL}/D.US").mock(return_value=httpx.Response(200, json=payload))

    p = DBnomicsProvider()
    rows = await p.interest_rates("FED", None, None)
    assert [r.rate for r in rows] == [4.25, 4.375]  # "NA" dropped, never faked
    assert rows[0].date == "2025-06-01" and rows[0].name == "U.S. Federal Reserve"
    snap = await p.snapshot("FED")
    assert snap[0].rate == 4.375 and snap[0].date == "2025-07-08"
    filtered = await p.interest_rates("FED", _date(2025, 7, 1), None)
    assert [r.rate for r in filtered] == [4.375]


@respx.mock
async def test_auto_macro_keyless_uses_dbnomics(monkeypatch):
    from app.config import settings
    from app.providers.us.macro_auto import AutoMacroProvider

    payload = {"series": {"docs": [{"period": ["2025-06-01", "2025-07-08"], "value": ["NA", 2.0]}]}}
    respx.get(f"{_DBN_URL}/D.XM").mock(return_value=httpx.Response(200, json=payload))

    monkeypatch.setattr(settings, "fred_api_key", "")
    rows = await AutoMacroProvider().interest_rates("ECB", None, None)
    assert [r.rate for r in rows] == [2.0]
    assert rows[0].name == "European Central Bank"


@respx.mock
async def test_auto_macro_falls_back_when_fred_bot_walled(monkeypatch):
    from app.config import settings
    from app.providers.us.macro_auto import AutoMacroProvider

    # FRED serves the JS bot-wall (200 but HTML, not JSON) → upstream_error → fallback.
    respx.get("https://api.stlouisfred.org/fred/series/observations").mock(
        return_value=httpx.Response(200, text="<html>window.location.replace('/x')</html>"))
    payload = {"series": {"docs": [{"period": ["2025-07-08"], "value": [4.375]}]}}
    respx.get(f"{_DBN_URL}/D.US").mock(return_value=httpx.Response(200, json=payload))

    monkeypatch.setattr(settings, "fred_api_key", "test-key")
    snap = await AutoMacroProvider().snapshot("FED")
    assert snap[0].rate == 4.375 and snap[0].bank == "FED"


# --- report_period filter operators ---------------------------------------
def test_report_period_filter_gt_lt():
    from datetime import date as _date

    from app.filters import ReportPeriodFilters

    rows = [type("R", (), {"report_period": _date(2022, 1, 1)})(), type("R", (), {"report_period": _date(2024, 1, 1)})()]
    gt = ReportPeriodFilters(None, None, None, _date(2023, 1, 1), None)
    assert [r.report_period.year for r in gt.apply(rows, 10)] == [2024]
    lt = ReportPeriodFilters(None, None, None, None, _date(2023, 1, 1))
    assert [r.report_period.year for r in lt.apply(rows, 10)] == [2022]


# --- screener operators + restatement -------------------------------------
def test_screener_operators():
    from app.store.screener import _OPS

    assert _OPS["gte"](5, 5) and _OPS["lt"](3, 5) and _OPS["lte"](5, 5)
    assert _OPS["gt"](6, 5) and _OPS["eq"](2, 2) and not _OPS["eq"](1, 2)


def test_screener_restatement_latest_filing_wins():
    from datetime import date as _date

    from sqlalchemy import delete

    from app.store.db import SessionLocal, init_db
    from app.store.models import FinancialFact
    from app.store.screener import run_line_items

    init_db()
    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.market == "ZR"))
        for accn, fdate, val in [("orig", _date(2025, 2, 1), 100.0), ("restated", _date(2025, 8, 1), 120.0)]:
            db.add(FinancialFact(
                market="ZR", ticker="ZR1", statement="income", line_item="revenue", value=val,
                currency="USD", period="annual", report_period=_date(2024, 12, 31),
                filing_date=fdate, accession_number=accn, source="t",
            ))
        db.commit()
    try:
        li = run_line_items(["ZR1"], ["revenue"], "annual", 1)
        assert li and li[0]["revenue"] == 120.0  # later filing wins (restatement)
    finally:
        with SessionLocal() as db:
            db.execute(delete(FinancialFact).where(FinancialFact.market == "ZR"))
            db.commit()


# --- scheduler ------------------------------------------------------------
async def test_resolve_universe_edges():
    from app.store.universes import resolve_universe

    assert await resolve_universe("") == []
    assert await resolve_universe("garbage") == []   # unknown source id → nothing (no fetch)
    assert await resolve_universe("US:") == []         # empty ticker list dropped
    u = await resolve_universe("us:aapl")
    assert u[0][0] is Market.US and u[0][1] == ["aapl"]


async def test_queue_sweep_defers_per_market(monkeypatch):
    # A periodic sweep resolves the configured universe and defers one run_pipeline job per market
    # (deduped by the queueing_lock). This replaces the old asyncio Scheduler._run_once dispatch.
    from app import queue as Q
    from app.store import universes as U

    async def fake_resolve(spec):
        return [(Market.US, ["AAPL"]), (Market.KR, ["005930"])]

    calls = []

    async def fake_defer(market, tickers, pipeline_id):
        calls.append((market, tuple(tickers), pipeline_id))
        return len(calls)

    monkeypatch.setattr(U, "resolve_universe", fake_resolve)
    monkeypatch.setattr(Q, "defer_pipeline", fake_defer)
    await Q._sweep("news")
    assert ("US", ("AAPL",), "news") in calls
    assert ("KR", ("005930",), "news") in calls


async def test_prices_pipeline_uses_configured_backfill_years(monkeypatch):
    # CE-0: the prices pipeline stores a deep history (settings.prices_backfill_years), not the 2y default.
    import app.pipelines as P
    import app.store.prices_ingest as PI
    from app.config import settings

    monkeypatch.setattr(settings, "prices_backfill_years", 7)
    seen = {}

    async def fake_run(market, tickers, years=2, retries=1):
        seen["years"] = years
        return {"status": "success"}

    monkeypatch.setattr(PI, "run_prices_ingest", fake_run)
    await P._run_prices("US", ["AAPL"])
    assert seen["years"] == 7


async def test_queue_defer_sweep_rejects_unknown_pipeline():
    # The admin "run now" defers a known pipeline's sweep; an unknown id is rejected, never queued.
    from app import queue as Q

    out = await Q.defer_sweep("does_not_exist")
    assert out["deferred"] is False and "unknown" in out["detail"]


def test_incremental_start_logic():
    from datetime import date

    from app.store._ingest_helpers import _incremental_start

    full = date(2020, 1, 1)
    # no prior data → full backfill
    assert _incremental_start(None, full, 5) == full
    # prior data → last − overlap (never before full_start)
    assert _incremental_start(date(2026, 6, 1), full, 5) == date(2026, 5, 27)
    assert _incremental_start(date(2020, 1, 2), full, 30) == full  # clamped to full_start


async def test_run_ticker_job_best_effort(monkeypatch):
    # RF-05: the shared per-ticker job orchestrator — skip failures (-1), sum rows, record a failed note.
    import app.store.jobs as J

    finished: dict = {}
    monkeypatch.setattr(J, "start_job", lambda *a, **k: 7)
    monkeypatch.setattr(J, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(J, "finish_job", lambda job, status, rows=0, error=None:
                        finished.update(job=job, status=status, rows=rows, error=error))

    async def ingest_one(t):
        if t == "BAD":
            raise RuntimeError("upstream down")
        return {"AAPL": 5, "MSFT": 3}[t]

    out = await J.run_ticker_job("prices", "US", "spec", ["AAPL", "BAD", "MSFT"], ingest_one)
    assert out["status"] == "success" and out["rows"] == 8
    assert out["per_ticker"] == {"AAPL": 5, "BAD": -1, "MSFT": 3} and out["failed"] == ["BAD"]
    assert finished == {"job": 7, "status": "success", "rows": 8, "error": "failed: ['BAD']"}


async def test_run_prices_ingest_is_incremental(monkeypatch):
    # prices fetches each ticker only since its last stored bar (− overlap); a fresh ticker with no
    # data falls back to the full PRICES_BACKFILL_YEARS window. (No upstream network — ingest stubbed.)
    from datetime import date, timedelta

    import app.store.prices_ingest as PI

    seen: dict[str, date] = {}

    async def fake_ingest(mk, t, start, end, retries=1):
        seen[t] = start
        return 1

    last = {"AAPL": date(2026, 6, 1), "NEW": None}
    monkeypatch.setattr(PI, "ingest_prices_ticker", fake_ingest)
    monkeypatch.setattr(PI, "_last_bar_date", lambda market, ticker: last.get(ticker))
    monkeypatch.setattr(PI, "init_db", lambda: None)
    # the IngestionJob lifecycle now lives in jobs.run_ticker_job (RF-05) → stub the jobs.* calls there
    import app.store.jobs as J
    monkeypatch.setattr(J, "start_job", lambda *a, **k: 1)
    monkeypatch.setattr(J, "finish_job", lambda *a, **k: None)
    monkeypatch.setattr(J, "update_progress", lambda *a, **k: None)

    await PI.run_prices_ingest("US", ["AAPL", "NEW"], years=5, overlap_days=5)

    today = date.today()
    full_start = (date(today.year - 5, today.month, today.day) if today.year - 5 > 0
                  else date(today.year - 5, 1, 1))
    assert seen["AAPL"] == date(2026, 6, 1) - timedelta(days=5)  # incremental
    assert seen["NEW"] == full_start                              # first-ever pull → full backfill


async def test_run_pipelines_dispatches_and_isolates_failures(monkeypatch):
    import app.pipelines as P

    ran = []

    async def ok_runner(market, tickers):
        ran.append(("ok", market))

    async def boom_runner(market, tickers):
        raise RuntimeError("upstream down")

    monkeypatch.setitem(P.PIPELINE_BY_ID["prices"], "runner", ok_runner)
    monkeypatch.setitem(P.PIPELINE_BY_ID["financials"], "runner", boom_runner)
    summary = await P.run_pipelines("US", ["AAPL"], ["financials", "prices"])
    assert summary["financials"].startswith("error")   # one failing pipeline…
    assert summary["prices"] == "ok"                     # …never sinks the others
    assert ("ok", "US") in ran


# --- selftest classifier --------------------------------------------------
def test_selftest_classifier_cases():
    from app.selftest import _classify

    assert _classify(200, "{}")[0] == "pass"
    assert _classify(402, "Active subscription required")[0] == "fail"
    assert _classify(503, "bot-verification challenge")[0] == "skipped"
    assert _classify(400, "OPENDART_API_KEY is not configured.")[0] == "skipped"
    assert _classify(500, "boom")[0] == "fail"


# --- app-level integration (no upstream network) --------------------------
def test_admin_queue_endpoint():
    # The queue overview always renders (fail-safe): even with the queue DB unreachable in unit
    # tests, it returns the DB-free cron-sweep schedule + the registered task names.
    body = client.get("/admin/queue").json()
    assert "periodic" in body and "tasks" in body
    assert {s["pipeline_id"] for s in body["periodic"]} >= {"news", "prices", "financials"}


def test_admin_store_stats_endpoint():
    body = client.get("/admin/store/stats").json()
    assert "total_facts" in body and "by_market" in body
    # PH-PIPE: prices + corporate-actions coverage is reported too
    assert "price_bars" in body and "corporate_actions" in body


def test_admin_pipelines_endpoint():
    body = client.get("/admin/pipelines").json()
    ids = {p["id"] for p in body["pipelines"]}
    assert {"financials", "prices", "corp_actions", "news"} <= ids
    prices = next(p for p in body["pipelines"] if p["id"] == "prices")
    assert prices["store"] == "price_bars" and "latest" in prices
    assert "queue" in body and "periodic" in body["queue"]


def test_admin_pipelines_run_dispatches(monkeypatch):
    import app.routers.admin as A

    seen = {}

    async def fake_defer(market, tickers, pipeline_id):
        seen["call"] = (market, tuple(tickers), pipeline_id)
        return 1

    monkeypatch.setattr(A.Q, "defer_pipeline", fake_defer)
    # explicit market+tickers → enqueues on the queue immediately (no dynamic fetch needed)
    r = client.post("/admin/pipelines/run", json={"market": "US", "tickers": ["AAPL", "MSFT"], "pipelines": ["prices"]})
    body = r.json()
    assert r.status_code == 200 and body["started"] is True and body["pipelines"] == ["prices"]
    assert body["universe"][0]["market"] == "US" and body["universe"][0]["count"] == 2
    assert body["deferred"] == 1 and seen["call"] == ("US", ("AAPL", "MSFT"), "prices")


async def test_prices_ingest_shapes_and_upserts(monkeypatch):
    import app.store.prices_ingest as PI
    from datetime import date

    class _Bar:
        def __init__(self, **k):
            self.__dict__.update(k)

        def model_dump(self):
            return dict(self.__dict__)

    class FakeProv:
        async def prices(self, ref, interval, start, end):
            return [_Bar(time="2024-01-02", open=10, high=11, low=9, close=10.5, volume=1000),
                    _Bar(time="bad-date", open=1, high=1, low=1, close=1, volume=1)]  # dropped

    monkeypatch.setattr(PI, "get_prices_provider", lambda m: FakeProv())
    added = []

    class FakeDB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _R:
                def scalar_one_or_none(self_inner):
                    return None
            return _R()

        def add(self, o):
            added.append(o)

        def commit(self):
            pass

    monkeypatch.setattr(PI, "SessionLocal", lambda: FakeDB())
    n = await PI.ingest_prices_ticker(PI.Market.US, "AAPL", date(2024, 1, 1), date(2024, 1, 3))
    assert n == 1  # the unparseable-date bar is dropped, never fabricated
    assert added[0].ticker == "AAPL" and added[0].close == 10.5 and added[0].bar_date == date(2024, 1, 2)


def test_bad_period_returns_400():
    r = client.get("/financials/income-statements?ticker=AAPL&market=US&period=monthly")
    assert r.status_code == 400 and r.json()["error"] == "Bad Request"


def test_missing_identifier_returns_400():
    r = client.get("/financials/income-statements?market=US&period=annual")
    assert r.status_code == 400


def test_invalid_market_enum_returns_400():
    r = client.get("/company/facts?ticker=AAPL&market=ZZ")
    assert r.status_code == 400


def test_invalid_interval_returns_400():
    r = client.get("/prices?ticker=AAPL&market=US&interval=hour&start_date=2024-01-01&end_date=2024-01-02")
    assert r.status_code == 400


def test_screener_no_match_returns_empty():
    r = client.post(
        "/financials/search/screener?market=US",
        json={"limit": 5, "filters": [{"field": "revenue", "operator": "gt", "value": 1e18}]},
    )
    assert r.status_code == 200 and r.json()["search_results"] == []


def test_institutional_requires_exactly_one_arg():
    r = client.get("/institutional-holdings")  # neither filer_cik nor ticker
    assert r.status_code == 400


def test_more_scaffold_501s():
    for path in ("/kpi/metrics", "/financials/segments?ticker=AAPL", "/institutional-holdings/tickers"):
        assert client.get(path).status_code == 501


# --- bulk backfill --------------------------------------------------------
def test_all_facts_from_companyfacts_multi_period():
    from app.providers.us.sec_edgar import all_facts_from_companyfacts

    gaap = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": "2020-01-01", "end": "2020-12-31", "val": 100, "form": "10-K", "fy": 2020, "fp": "FY", "accn": "a"},
            {"start": "2021-01-01", "end": "2021-12-31", "val": 110, "form": "10-K", "fy": 2021, "fp": "FY", "accn": "b"},
            {"start": "2021-07-01", "end": "2021-09-30", "val": 30, "form": "10-Q", "fy": 2021, "fp": "Q3", "accn": "c"},
        ]}},
        "Assets": {"units": {"USD": [{"end": "2021-12-31", "val": 500, "form": "10-K", "fy": 2021}]}},
    }}}
    rows = all_facts_from_companyfacts(gaap, "0000000001")
    periods = {(r["statement"], r["period"], r["report_period"]) for r in rows}
    assert ("income", "annual", "2020-12-31") in periods
    assert ("income", "annual", "2021-12-31") in periods
    assert ("income", "quarterly", "2021-09-30") in periods
    assert ("balance", "annual", "2021-12-31") in periods


async def test_bulk_zip_loads(monkeypatch, tmp_path):
    import json as _json
    import zipfile as _zip

    from sqlalchemy import delete, func, select

    from app.store import bulk
    from app.store.db import SessionLocal, init_db
    from app.store.models import FinancialFact

    async def fake_index():
        return {"ZBULK": {"cik_str": 9000001, "ticker": "ZBULK", "title": "Z"}}

    monkeypatch.setattr(bulk, "_ticker_index", fake_index)
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": "2022-01-01", "end": "2022-12-31", "val": 500, "form": "10-K", "fy": 2022, "fp": "FY", "accn": "x"},
    ]}}}}}
    zp = tmp_path / "cf.zip"
    with _zip.ZipFile(zp, "w") as zf:
        zf.writestr("CIK0009000001.json", _json.dumps(facts))
    init_db()
    with SessionLocal() as db:
        db.execute(delete(FinancialFact).where(FinancialFact.ticker == "ZBULK"))
        db.commit()
    try:
        res = await bulk.bulk_load_us(zip_path=str(zp))
        assert res.get("ZBULK", 0) > 0
        with SessionLocal() as db:
            n = db.scalar(select(func.count()).select_from(FinancialFact).where(FinancialFact.ticker == "ZBULK"))
        assert n and n > 0
    finally:
        with SessionLocal() as db:
            db.execute(delete(FinancialFact).where(FinancialFact.ticker == "ZBULK"))
            db.commit()


# --- connector catalog (P0) -----------------------------------------------
def test_catalog_manifests_valid():
    from app.connectors.catalog import get_catalog

    cons = get_catalog()
    assert cons, "catalog is empty"
    ids = [c.id for c in cons]
    assert len(ids) == len(set(ids)), "duplicate connector ids"
    for c in cons:
        assert c.id and c.name and c.domain and c.markets and c.resources
        assert c.license is not None and isinstance(c.license.redistribution, bool)
        for r in c.resources:
            assert r.name and r.path.startswith("/")
            assert r.provenance and r.provenance.source  # trust envelope present


def test_every_resource_has_a_valid_category():
    # The builder groups tools by user-facing category (not by API) — so EVERY tool, present or
    # future, must carry a known category. _apply_resource_meta() raises on a gap; assert it held.
    from app.connectors.catalog import get_catalog, get_categories
    from app.connectors.manifest import Category

    valid = {c["id"] for c in get_categories()}
    assert valid == {c.value for c in Category}  # metadata + enum stay in lockstep
    for c in get_catalog():
        for r in c.resources:
            assert r.category is not None, f"{c.id}__{r.name} has no category"
            assert r.category.value in valid


def test_catalog_endpoint_exposes_categories_and_resource_category():
    body = client.get("/catalog").json()
    cat_ids = {c["id"] for c in body["categories"]}
    assert {"market", "macro", "gurus", "fundamentals"} <= cat_ids
    # the new guru tools are categorized under 'gurus'
    sec = next(c for c in body["connectors"] if c["id"] == "sec_edgar")
    cats = {r["name"]: r["category"] for r in sec["resources"]}
    assert cats["guru_trades"] == "gurus" and cats["guru_common"] == "gurus"
    assert cats["company_facts"] == "fundamentals"


def test_ticker_is_required_where_a_company_is_mandatory():
    # A user/agent must name a company for price + fundamentals pulls — the manifest
    # (and thus the MCP/OpenAPI schema) must mark ticker required there, so clients
    # can't issue a doomed call. company_facts (cik alternative) + news stay optional.
    from app.connectors.catalog import get_catalog

    by_id = {c.id: c for c in get_catalog()}

    def ticker_required(connector_id: str, resource_name: str) -> bool:
        res = next(r for r in by_id[connector_id].resources if r.name == resource_name)
        return any(p.name == "ticker" and p.required for p in res.params)

    for cid, rname in [
        ("yahoo", "prices"), ("yahoo", "price_snapshot"),
        ("sec_edgar", "income_statements"), ("sec_edgar", "earnings"), ("sec_edgar", "metrics_snapshot"),
        ("opendart", "income_statements"), ("opendart", "earnings"), ("opendart", "metrics_snapshot"),
    ]:
        assert ticker_required(cid, rname), f"{cid}.{rname} must require ticker"

    # …but endpoints with an alternative or a general mode keep it optional
    assert not ticker_required("sec_edgar", "company_facts")  # cik works instead
    assert not ticker_required("google_news", "news")          # general feed


def test_catalog_resource_paths_are_real_routes():
    from fastapi.routing import APIRoute

    from app.connectors.catalog import all_resource_paths
    from app.main import app

    real = {(m, r.path) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
    for method, path in all_resource_paths():
        assert (method, path) in real, f"manifest references a non-existent route: {method} {path}"


def test_catalog_endpoints():
    body = client.get("/catalog").json()
    assert body["count"] > 0 and len(body["connectors"]) == body["count"]
    cid = body["connectors"][0]["id"]
    one = client.get(f"/catalog/{cid}")
    assert one.status_code == 200 and one.json()["id"] == cid
    assert client.get("/catalog/does-not-exist").status_code == 404


def test_catalog_restricted_license_flagged():
    # Yahoo + news must be marked non-redistributable (governance signal).
    from app.connectors.catalog import get_connector

    assert get_connector("yahoo").license.redistribution is False
    assert get_connector("google_news").license.redistribution is False
    assert get_connector("sec_edgar").license.redistribution is True


# --- RAG connector in the catalog (routed to the rag service) -------------
def test_catalog_has_rag_connector_routed_to_rag_service():
    from app.connectors.catalog import all_resource_paths, get_connector

    rag = get_connector("rag")
    assert rag is not None and rag.service == "rag"
    assert any(r.path == "/rag/search" and r.method == "POST" for r in rag.resources)
    # rag paths are served elsewhere, so excluded from the data-plane integrity set
    assert ("POST", "/rag/search") not in all_resource_paths()
    assert ("POST", "/rag/search") in all_resource_paths(service="rag")
    assert rag.license.redistribution is False


# --- filing HTML viewer: sanitize + serve the original markup -------------
def test_filing_html_sanitize_keeps_ixbrl_blocks_egress():
    from app.store.filing_html import sanitize

    raw = ('<html><head><title>F</title></head><body>'
           '<script>alert(1)</script>'
           '<ix:nonfraction name="us-gaap:Revenues" contextref="FY">383,285</ix:nonfraction>'
           '<img src="http://tracker/x.png"><p onclick="x()">Net sales</p></body></html>')
    out = sanitize(raw)
    assert "<script" not in out.lower()                 # active scripts stripped
    assert "Content-Security-Policy" in out and "default-src 'none'" in out  # CSP → no egress
    assert 'name="us-gaap:Revenues"' in out             # inline-XBRL tag preserved (viewer targets it)
    assert "383,285" in out                             # the cited figure preserved verbatim


async def test_get_filing_html_caches(tmp_path, monkeypatch):
    import app.store.filing_html as FH
    from app.config import settings

    monkeypatch.setattr(settings, "evidence_docs_dir", str(tmp_path))
    calls = {"n": 0}

    async def fake_source(market, accession, cik, fetch_url=None):
        calls["n"] += 1
        return '<html><body><script>x()</script><p>Net sales 383,285</p></body></html>'

    monkeypatch.setattr(FH, "_source_html", fake_source)
    h1 = await FH.get_filing_html("US", "0000-1", "320193")
    assert h1 and "<script" not in h1.lower() and "Content-Security-Policy" in h1
    assert (tmp_path / "html" / "US" / "0000-1.html").exists()        # cached to disk
    h2 = await FH.get_filing_html("US", "0000-1", "320193")           # second call
    assert h2 == h1 and calls["n"] == 1                               # fetched once, then served from cache


def test_ph_prov3e_admin_filings_ingest_endpoint(monkeypatch):
    import app.routers.admin as A

    fired: dict = {}

    async def fake_defer(market, tickers, pipeline_id):
        fired["call"] = (market, tuple(tickers), pipeline_id)
        return 1

    monkeypatch.setattr(A.Q, "defer_pipeline", fake_defer)
    # US → enqueues the filing_text pipeline on the queue
    assert client.post("/admin/filings/ingest", json={"market": "US", "tickers": ["AAPL"]}).json()["started"] is True
    assert fired["call"] == ("US", ("AAPL",), "filing_text")
    # an unsupported market is rejected, never queued
    assert client.post("/admin/filings/ingest", json={"market": "JP", "tickers": ["7203"]}).json()["started"] is False


# --- PH-8: index-fund / ETF holdings (SEC N-PORT) -------------------------
_NPORT = """<edgarSubmission><formData>
 <genInfo><regName>Test S&amp;P 500 ETF Trust</regName><regCik>0000884394</regCik><repPdDate>2026-03-31</repPdDate></genInfo>
 <fundInfo><totAssets>1000.00</totAssets><netAssets>950.00</netAssets></fundInfo>
 <invstOrSecs>
  <invstOrSec><name>Apple Inc</name><title>Apple Inc</title><cusip>037833100</cusip>
   <identifiers><isin value="US0378331005"/></identifiers>
   <balance>100.0</balance><units>NS</units><valUSD>500.0</valUSD><pctVal>52.6</pctVal><assetCat>EC</assetCat></invstOrSec>
  <invstOrSec><name>Microsoft Corp</name><cusip>594918104</cusip>
   <balance>50.0</balance><units>NS</units><valUSD>300.0</valUSD><pctVal>31.6</pctVal><assetCat>EC</assetCat></invstOrSec>
 </invstOrSecs>
</formData></edgarSubmission>"""


def test_ph8_nport_parse():
    from app.providers.us.sec_edgar import _parse_nport

    meta, holdings = _parse_nport(_NPORT)
    assert meta["name"] == "Test S&P 500 ETF Trust" and meta["cik"] == "0000884394"
    assert meta["as_of"] == "2026-03-31" and meta["net_assets"] == 950.0
    assert len(holdings) == 2
    aapl = holdings[0]
    assert aapl.cusip == "037833100" and aapl.isin == "US0378331005"
    assert aapl.shares == 100.0 and aapl.market_value == 500.0
    assert abs(aapl.weight - 0.526) < 1e-9      # pctVal 52.6 → fraction


def test_ph8_index_funds_endpoint(monkeypatch):
    import app.routers.funds as F
    from app.models.generated import Fund, FundHolding

    class _Fake:
        async def holdings(self, ref, limit):
            return (Fund(name="SPDR S&P 500 ETF Trust", cik="0000884394", source="SEC EDGAR (N-PORT)",
                         total_net_assets=6.5e11, total_holdings=2, returned=2),
                    [FundHolding(name="Apple Inc", cusip="037833100", weight=0.07,
                                 market_value=4.5e10, shares=1.0, asset_class="EC")])

    monkeypatch.setattr(F, "get_fund_provider", lambda m: _Fake())
    b = client.get("/index-funds?ticker=SPY&market=US").json()
    assert b["ticker"] == "SPY" and b["fund"]["name"].startswith("SPDR")
    assert b["holdings"][0]["cusip"] == "037833100"
    # reverse direction → 501; neither/both → 400
    assert client.get("/index-funds?holding=AAPL&market=US").status_code == 501
    assert client.get("/index-funds").status_code == 400
    t = client.get("/index-funds/tickers").json()
    assert "SPY" in t["tickers"] and t["resource"] == "index_funds"


# --- PH-DATA-1: superinvestor (거장) 13F portfolios -----------------------
def test_phdata1_gurus_list_and_holdings(monkeypatch):
    from app.models.generated import InstitutionalHolding
    import app.routers.gurus as G

    # list mode → curated registry (every entry has a verified CIK)
    body = client.get("/gurus").json()
    slugs = {g["slug"] for g in body["gurus"]}
    assert {"buffett", "burry", "ackman"} <= slugs
    assert all(g["cik"] and g["investor"] for g in body["gurus"])

    # slug mode → that filer's 13F holdings (provider mocked; each carries an accession)
    class _Fake:
        async def by_filer(self, cik, limit):
            assert cik == "0001067983"
            return [InstitutionalHolding(name_of_issuer="Apple Inc", cusip="037833100",
                                         value_usd=1000, accession_number="0001067983-26-000001")]
    monkeypatch.setattr(G, "get_institutional_provider", lambda m: _Fake())
    b = client.get("/gurus?slug=buffett&limit=5").json()
    assert b["guru"]["investor"] == "Warren Buffett" and b["filer_cik"] == "0001067983"
    assert b["holdings"][0]["cusip"] == "037833100"
    assert b["holdings"][0]["accession_number"] == "0001067983-26-000001"  # → provenance to SEC 13F

    # unknown slug → 404
    assert client.get("/gurus?slug=nobody").status_code == 404


# --- CE-3: 거장 매매 (quarter deltas) + 공통 보유종목 (intersection) ---------
def test_ce3_guru_trades_compute():
    from app.models.generated import InstitutionalHolding
    from app.providers.us.gurus import compute_trades

    def H(cusip, shares, value, accn):
        return InstitutionalHolding(name_of_issuer=cusip, cusip=cusip, ticker=cusip,
                                    shares=shares, value_usd=value, accession_number=accn)

    quarters = [
        {"report_period": "2026-03-31", "filing_date": "2026-05-15", "accession": "Q2",
         "holdings": [H("AAA", 200, 2000, "Q2"), H("BBB", 100, 1000, "Q2"), H("DDD", 50, 500, "Q2")]},
        {"report_period": "2025-12-31", "filing_date": "2026-02-14", "accession": "Q1",
         "holdings": [H("AAA", 100, 1000, "Q1"), H("BBB", 150, 1500, "Q1"), H("CCC", 80, 800, "Q1")]},
    ]
    res = compute_trades(quarters, limit=40)
    assert res["comparable"] is True and res["report_period"] == "2026-03-31"
    by = {t["cusip"]: t for t in res["trades"]}
    assert by["AAA"]["action"] == "added" and by["AAA"]["shares_change"] == 100
    assert by["BBB"]["action"] == "trimmed" and by["BBB"]["shares_change"] == -50
    assert by["CCC"]["action"] == "exited"
    assert by["DDD"]["action"] == "new"
    # single quarter (no prior) → everything is 'new', not comparable
    one = compute_trades(quarters[:1])
    assert one["comparable"] is False and all(t["action"] == "new" for t in one["trades"])


def test_ce3_guru_common_holdings():
    from app.models.generated import InstitutionalHolding
    from app.providers.us.gurus import common_holdings

    def H(cusip, value):
        return InstitutionalHolding(name_of_issuer=cusip, cusip=cusip, ticker=cusip,
                                    shares=10, value_usd=value, accession_number=cusip + "-a")

    per_guru = [
        {"guru": {"slug": "buffett", "investor": "Warren Buffett", "name": "Berkshire"},
         "holdings": [H("AAPL", 5000), H("KO", 2000)]},
        {"guru": {"slug": "ackman", "investor": "Bill Ackman", "name": "Pershing"},
         "holdings": [H("AAPL", 3000), H("CMG", 1500)]},
        {"guru": {"slug": "burry", "investor": "Michael Burry", "name": "Scion"},
         "holdings": [H("AAPL", 1000)]},
    ]
    rows = common_holdings(per_guru, min_holders=2, limit=40)
    assert len(rows) == 1  # only AAPL is held by ≥2 gurus
    aapl = rows[0]
    assert aapl["cusip"] == "AAPL" and aapl["holder_count"] == 3
    assert aapl["total_value_usd"] == 9000
    assert aapl["holders"][0]["value_usd"] == 5000  # sorted desc by value


def test_ce3_guru_trades_route(monkeypatch):
    from app.models.generated import InstitutionalHolding
    import app.routers.gurus as G

    class _Fake:
        async def by_filer_quarters(self, cik, quarters):
            assert cik == "0001067983" and quarters == 2
            return [
                {"report_period": "2026-03-31", "filing_date": "2026-05-15", "accession": "Q2",
                 "holdings": [InstitutionalHolding(cusip="AAA", ticker="AAA", shares=200,
                                                   value_usd=2000, accession_number="Q2")]},
                {"report_period": "2025-12-31", "filing_date": "2026-02-14", "accession": "Q1",
                 "holdings": [InstitutionalHolding(cusip="AAA", ticker="AAA", shares=100,
                                                   value_usd=1000, accession_number="Q1")]},
            ]
        async def by_filer(self, cik, limit):
            return [InstitutionalHolding(cusip="AAA", ticker="AAA", shares=10, value_usd=100,
                                         accession_number="X")]
    monkeypatch.setattr(G, "get_institutional_provider", lambda m: _Fake())
    b = client.get("/gurus/trades?slug=buffett").json()
    assert b["comparable"] is True and b["trades"][0]["action"] == "added"
    assert client.get("/gurus/trades?slug=nobody").status_code == 404

    c = client.get("/gurus/common?slugs=buffett,ackman&min_holders=2").json()
    assert c["resource"] == "gurus_common" and "buffett" in c["gurus_resolved"]


# --- PH-DATA-2: peer valuation comparables --------------------------------
def test_phdata2_comparables(monkeypatch):
    from app.models.generated import FinancialMetricSnapshot
    import app.routers.metrics as M

    class _Fake:
        async def metrics_snapshot(self, ref):
            return FinancialMetricSnapshot(ticker=ref.ticker, price_to_earnings_ratio=30.0,
                                           net_margin=0.25, return_on_equity=0.5)

    monkeypatch.setattr(M, "get_metrics_provider", lambda m: _Fake())
    b = client.get("/comparables?tickers=AAPL,MSFT,GOOGL&market=US").json()
    assert b["market"] == "US" and b["tickers"] == ["AAPL", "MSFT", "GOOGL"]
    assert len(b["comparables"]) == 3
    assert b["comparables"][0]["price_to_earnings_ratio"] == 30.0
    assert client.get("/comparables?tickers=&market=US").status_code == 400  # no tickers


# --- PH-DATA-3: corporate actions (dividends + splits) --------------------
@respx.mock
async def test_phdata3_yahoo_corporate_actions_parse():
    from datetime import date as _date

    from app.providers.us.yahoo import YahooProvider
    from app.symbols import Market, build_ref

    payload = {"chart": {"result": [{"meta": {"currency": "USD"}, "events": {
        "dividends": {"1": {"amount": 0.24, "date": 1700000000}, "2": {"amount": 0.25, "date": 1710000000}},
        "splits": {"1": {"date": 1598880600, "numerator": 4.0, "denominator": 1.0, "splitRatio": "4:1"}},
    }}]}}
    respx.get("https://query1.finance.yahoo.com/v8/finance/chart/AAPL").mock(
        return_value=httpx.Response(200, json=payload))
    data = await YahooProvider().corporate_actions(build_ref(Market.US, "AAPL"), _date(2020, 1, 1), _date(2026, 1, 1))
    assert data["currency"] == "USD"
    assert [d["amount"] for d in data["dividends"]] == [0.25, 0.24]  # newest first
    assert data["splits"][0]["ratio"] == "4:1"


def test_phdata3_corporate_actions_endpoint(monkeypatch):
    import app.routers.corporate_actions as C

    class _Fake:
        async def corporate_actions(self, ref, start, end):
            return {"currency": "USD",
                    "dividends": [{"ex_date": "2026-02-07", "amount": 0.25}],
                    "splits": [{"date": "2020-08-31", "ratio": "4:1", "numerator": 4.0, "denominator": 1.0}]}

    monkeypatch.setattr(C, "get_prices_provider", lambda m: _Fake())
    b = client.get("/corporate-actions?ticker=AAPL&market=US&years=10").json()
    assert b["ticker"] == "AAPL" and b["currency"] == "USD"
    assert b["dividends"][0]["amount"] == 0.25 and b["splits"][0]["ratio"] == "4:1"


# --- PH-DATA-4 / PH-FRESH-1: economic indicators (BLS direct + DBnomics) --
@respx.mock
async def test_phdata4_indicators_fetch():
    from app.providers.macro_indicators import fetch_indicator, list_indicators

    # BLS path: `cpi` is a BLS series → read FRESH from the BLS public API (the DBnomics BLS
    # mirror froze at 2025-01). M13 (annual avg) is skipped; observations come back ascending.
    bls_payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": "CUSR0000SA0", "data": [
            {"year": "2026", "period": "M05", "value": "322.0"},
            {"year": "2026", "period": "M04", "value": "321.0"},
            {"year": "2026", "period": "M13", "value": "999"}]}]}}  # annual avg → dropped
    respx.post("https://api.bls.gov/publicAPI/v2/timeseries/data/").mock(
        return_value=httpx.Response(200, json=bls_payload))
    res = await fetch_indicator("cpi", 24)
    assert res["source"] == "BLS" and res["source_url"].endswith("CUSR0000SA0")
    assert res["observations"][-1] == {"date": "2026-05", "value": 322.0}  # newest last, M13 gone
    assert res["as_of"] == "2026-05" and res["stale"] is False             # fresh → not flagged

    # DBnomics path: a non-BLS series (BEA GDP) still reads keyless DBnomics.
    dbn = {"series": {"docs": [{"period": ["2026-Q1", "2026-Q2"], "value": ["NA", 2.5]}]}}
    respx.get("https://api.db.nomics.world/v22/series/BEA/NIPA-T10101/A191RL-Q").mock(
        return_value=httpx.Response(200, json=dbn))
    g = await fetch_indicator("gdp_growth", 24)
    assert g["source"] == "DBnomics" and g["observations"] == [{"date": "2026-Q2", "value": 2.5}]
    assert any(i["slug"] == "cpi" for i in list_indicators())
    assert await fetch_indicator("nope") is None


@respx.mock
async def test_bls_provider_parses_and_batches():
    from app.providers.us.bls import fetch_bls, series_page

    payload = {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": "LNS14000000", "data": [
            {"year": "2026", "period": "M05", "value": "4.3"},
            {"year": "2026", "period": "M04", "value": "4.1"}]},
        {"seriesID": "CES0000000001", "data": [
            {"year": "2026", "period": "M05", "value": "159001"},
            {"year": "2025", "period": "M13", "value": "1"}]}]}}  # M13 annual avg → dropped
    route = respx.post("https://api.bls.gov/publicAPI/v2/timeseries/data/").mock(
        return_value=httpx.Response(200, json=payload))
    out = await fetch_bls(["LNS14000000", "CES0000000001"])      # both series in ONE request
    assert out["LNS14000000"][-1] == {"date": "2026-05", "value": 4.3}   # ascending, latest last
    assert out["CES0000000001"] == [{"date": "2026-05", "value": 159001.0}]  # M13 dropped, never faked
    assert series_page("LNS14000000").endswith("LNS14000000")
    assert route.called


async def test_macro_stale_value_flagged(monkeypatch):
    # Honesty (PH-FRESH-1): a present-but-old value is FLAGGED stale, never shown as if current —
    # so if an upstream freezes again (like the DBnomics BLS mirror did at 2025-01) the UI shows it.
    import app.providers.macro_indicators as MI

    async def frozen(ids, years=3):
        return {i: [{"date": "2025-01", "value": 4.0}] for i in ids}  # ~17mo old
    monkeypatch.setattr(MI.bls_api, "fetch_bls", frozen)
    res = await MI.fetch_indicator("unemployment", 24)
    assert res["as_of"] == "2025-01" and res["stale"] is True and res["source"] == "BLS"


def test_phdata4_indicators_endpoint():
    b = client.get("/macro/indicators").json()  # list mode, no upstream
    assert b["resource"] == "economic_indicators"
    assert {"cpi", "unemployment", "gdp_growth"} <= {i["slug"] for i in b["indicators"]}
    assert client.get("/macro/indicators?indicator=nope").status_code == 404  # unknown → 404


# --- PH-DATA-6: technical indicators (descriptive, computed from prices) ----
def test_phdata6_compute_funcs():
    from app.store.technical import _sma, _ema, _rsi, _macd, _bbands, _volatility
    c = [float(x) for x in range(1, 41)]  # strictly rising
    sma = _sma(c, 5)
    assert sma[3] is None and sma[4] == 3.0          # mean(1..5) = 3
    ema = _ema(c, 5)
    assert ema[3] is None and ema[4] == 3.0          # seeded with the SMA
    rsi = _rsi(c, 14)
    assert rsi[13] is None and rsi[14] == 100.0      # all gains → RSI 100
    macd, signal, hist = _macd(c)
    assert macd[24] is None and macd[25] is not None  # defined once both EMAs exist
    assert signal[-1] is not None and hist[-1] is not None
    up, mid, lo = _bbands(c, 5)
    assert up[4] > mid[4] > lo[4]                     # bands straddle the middle
    assert _volatility(c, 20)[-1] is not None


async def test_phdata6_endpoint(monkeypatch):
    import app.store.technical as T
    from datetime import date, timedelta
    from app.models.generated import Price
    base = date(2024, 1, 1)
    series = [Price(time=str(base + timedelta(days=i)), open=100.0 + i, high=100.0 + i,
                    low=100.0 + i, close=100.0 + i, volume=1000) for i in range(40)]

    class _FakeP:
        async def prices(self, ref, interval, start, end):
            return series
    monkeypatch.setattr(T, "get_prices_provider", lambda m: _FakeP())

    b = client.get("/technical-indicators?ticker=AAPL&indicators=sma_5,rsi_14,macd&interval=day").json()
    assert b["ticker"] == "AAPL" and b["source"].startswith("Technical indicators")
    assert b["as_of"] == str(base + timedelta(days=39)) and b["note"]  # descriptive label present
    assert {i["key"] for i in b["indicators"]} == {"sma_5", "rsi_14", "macd"}
    sma = next(i for i in b["indicators"] if i["key"] == "sma_5")
    assert sma["pane"] == "price" and sma["lines"][0]["latest"] is not None  # sourced latest, not faked
    macd = next(i for i in b["indicators"] if i["key"] == "macd")
    assert {ln["label"] for ln in macd["lines"]} == {"MACD", "Signal", "Histogram"}


def test_phdata6_bad_interval_400():
    assert client.get("/technical-indicators?ticker=AAPL&interval=minute").status_code == 400


# --- periodicity (cadence) classification ---------------------------------
# Every datasource is classified periodic vs one-shot in the catalog. The product gates the
# pin→alert flow on it: only a periodic source can carry a notification bot. The map is enforced
# at import (a missing/stale entry raises), so importing the catalog already proves completeness —
# these assertions pin down the contract + a few representative classifications.

def test_every_resource_has_a_cadence():
    from app.connectors.catalog import CONNECTORS

    for c in CONNECTORS:
        for r in c.resources:
            assert r.cadence is not None, f"{c.id}.{r.name} has no cadence"


def test_cadence_exposed_in_catalog_endpoint():
    body = client.get("/catalog").json()
    cadences = {
        (con["id"], res["name"]): res.get("cadence")
        for con in body["connectors"] for res in con["resources"]
    }
    assert cadences[("yahoo", "prices")] == "daily"
    assert cadences[("sec_edgar", "filings")] == "event"
    assert cadences[("fred", "interest_rates")] == "scheduled"
    assert cadences[("google_news", "news")] == "streaming"
    assert cadences[("sec_edgar", "company_facts")] == "one_shot"


def test_periodic_vs_one_shot_partition():
    from app.connectors.catalog import CONNECTORS
    from app.connectors.manifest import Cadence

    periodic, one_shot = set(), set()
    for c in CONNECTORS:
        for r in c.resources:
            (periodic if r.cadence.periodic else one_shot).add((c.id, r.name))

    # representative periodic (alertable) sources
    assert ("yahoo", "prices") in periodic            # daily price series → price threshold
    assert ("opendart", "filings") in periodic        # KR disclosures → new-filing alert
    assert ("kis", "volume_rank") in periodic         # realtime ranking
    # representative one-shot (a value you pin and glance at — no bell)
    assert ("datasets_store", "valuation") in one_shot
    assert ("datasets_store", "backtest") in one_shot
    assert ("sec_edgar", "comparables") in one_shot
    assert Cadence.one_shot.periodic is False and Cadence.daily.periodic is True


# --- filing text → RAG corpus (extracted from the original HTML) ----------
def test_html_to_docs_sections_filing_text():
    """Filing HTML → section-sized RAG IngestDocs carrying accession + section (s.N) so a hit
    points back to a region for the in-app viewer to highlight. Script text is not indexed."""
    from app.store.filing_ingest import _html_to_docs

    html = ("<html><body><script>track()</script>"
            "<h1>Item 7. MD&A</h1>"
            "<p>Net sales were 391,035 and supply concentration remains a key risk.</p>"
            "<table><tr><td>Revenue</td><td>391,035</td></tr></table></body></html>")
    docs = _html_to_docs(html, "US", "AAPL", "0000320193-24-000123", "SEC EDGAR", "https://sec.gov/x")
    assert docs and docs[0]["accession"] == "0000320193-24-000123"
    assert docs[0]["section"].startswith("s.") and docs[0]["doc_type"] == "filing"
    assert docs[0]["ticker"] == "AAPL" and docs[0]["market"] == "US"
    joined = " ".join(d["text"] for d in docs)
    assert "Net sales" in joined and "391,035" in joined
    assert "track()" not in joined  # <script> text excluded from the corpus
