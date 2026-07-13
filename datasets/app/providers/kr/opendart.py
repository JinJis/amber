"""KR provider backed by OpenDART (금융감독원 전자공시 Open API).

* corp_code map   /api/corpCode.xml   (zip of stock_code <-> corp_code)
* company facts   /api/company.json
* financials      /api/fnlttSinglAcntAll.json
* filings         /api/list.json

OpenDART keys statements by an 8-digit ``corp_code``; the public-facing symbol
is the 6-digit ``stock_code``. The resolver below bridges the two and is cached.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date
from xml.etree import ElementTree

from app.cache import cache
from app.config import settings
from app.errors import bad_request, not_found, upstream_error
from app.http import fetch_bytes, fetch_json
from app.models.generated import (
    BalanceSheet,
    CashFlowStatement,
    CompanyFacts,
    CompanySearchResult,
    EarningsRecord,
    EarningsTimeDimension,
    Filing,
    FinancialMetricSnapshot,
    IncomeStatement,
    InsiderTrade,
)
from app.providers.search_util import rank_company_matches
from app.symbols import SecurityRef

# Concept maps + parsing/period helpers live in siblings; re-exported here so
# existing imports (registry, tests) keep resolving them via this module.
from app.providers.kr.opendart_concepts import (  # noqa: F401
    BALANCE_MAP,
    CASHFLOW_MAP,
    INCOME_MAP,
)
from app.providers.kr.opendart_parse import (  # noqa: F401
    _ANNUAL,
    _amount,
    _extract,
    _fiscal_period,
    _kr_date,
    _periods,
)

log = logging.getLogger(__name__)

_BASE = "https://opendart.fss.or.kr/api"
_CORP_CLS = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "ETC"}


def _key() -> str:
    """The first currently-available key (rotation-aware). Raises when none configured, or an
    honest quota error when every key is spent for the day."""
    if not _keys():
        raise bad_request("OPENDART_API_KEY is not configured.")
    avail = available_keys()
    if not avail:
        raise upstream_error("opendart", "020: 사용한도를 초과하였습니다 (all keys quota-blocked)")
    return avail[0]


_CORP_MAP_TTL = 24 * 3600     # the registry changes ~daily; the bulk zip is meant to be fetched ~once/day

# Read-API response caches — the daily quota is shared by ingestion, the feeds AND the evidence
# viewer, so every repeat download is quota stolen from the product. Filed statements are
# immutable (long TTL); the filings list moves with new disclosures (short TTL).
_DART_CACHE_TTLS = {
    "fnlttSinglAcntAll.json": 6 * 3600,   # statements — /financials used to re-fetch the SAME
                                          # payload for income/balance/cashflow (3× waste)
    "company.json": 24 * 3600,            # company profile — effectively static
    "list.json": 1800,                    # filings list — refresh every 30 min is plenty
}


def _parse_error_envelope(content: bytes) -> tuple[str | None, str | None]:
    """OpenDART signals errors as HTTP 200 + a small XML envelope — (status, message) or None."""
    if len(content) > 2048 or not content.lstrip().startswith(b"<?xml"):
        return None, None
    try:
        root = ElementTree.fromstring(content.decode("utf-8", errors="replace"))
    except ElementTree.ParseError:
        return None, None
    return root.findtext("status"), root.findtext("message")


# --- key pool: N keys (comma-separated), rotate on daily-quota exhaustion ------------------
# OPENDART_API_KEYS="key1,key2,…" (falls back to the single OPENDART_API_KEY). A key that
# returns status 020 is blocked until the next KST midnight (the daily 사용한도 window) and
# requests transparently continue on the next available key.
_blocked_keys: dict[str, float] = {}   # key → monotonic deadline while 사용한도-초과


def _keys() -> list[str]:
    raw = getattr(settings, "opendart_api_keys", "") or settings.opendart_api_key
    return [k.strip() for k in str(raw or "").split(",") if k.strip()]


def _seconds_to_kst_midnight() -> float:
    """OpenDART's daily quota resets at 00:00 KST — block a spent key exactly until then."""
    from datetime import datetime, timedelta, timezone

    kst_now = datetime.now(timezone(timedelta(hours=9)))
    nxt = (kst_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - kst_now).total_seconds() + 60.0   # +60s safety past the reset


# SC-2.4: the daily quota is a per-KEY OpenDART limit shared across ALL replicas — so a key spent on
# one replica must be treated as spent everywhere, else the others keep burning calls against it (020)
# and the evidence viewer that shares the quota starves. When REDIS_URL is set the block lives in Redis
# (auto-expiring at the KST reset); unset → the in-process dict, single-node correct.
def _block_key(k: str) -> str:
    return f"opendart:block:{k}"


def available_keys() -> list[str]:
    ks = _keys()
    from app import redisstate
    r = redisstate.sync_client()
    if r is not None:
        try:
            if not ks:
                return []
            flags = r.mget([_block_key(k) for k in ks])   # one round-trip for the whole pool
            return [k for k, blocked in zip(ks, flags) if not blocked]
        except Exception:  # noqa: BLE001 — redis down → in-process
            pass
    now = time.monotonic()
    return [k for k in ks if _blocked_keys.get(k, 0.0) <= now]


def mark_quota_blocked(key: str | None = None) -> None:
    """Block ``key`` (or, with no argument, every configured key) until the KST-midnight quota
    reset, so consumers fail fast / rotate instead of burning calls against a spent key."""
    targets = ([key] if key else _keys()) or ["_unconfigured_"]
    from app import redisstate
    r = redisstate.sync_client()
    if r is not None:
        try:
            ttl = int(_seconds_to_kst_midnight())
            for k in targets:
                r.set(_block_key(k), "1", ex=ttl)
            return
        except Exception:  # noqa: BLE001 — redis down → in-process
            pass
    until = time.monotonic() + _seconds_to_kst_midnight()
    for k in targets:
        _blocked_keys[k] = until


def quota_blocked() -> bool:
    """True iff keys are configured and ALL of them are quota-blocked."""
    ks = _keys()
    return bool(ks) and not available_keys()


def reset_quota_blocks() -> None:
    """Test hook — clear the per-key block state (in-process AND, if configured, Redis)."""
    _blocked_keys.clear()
    from app import redisstate
    r = redisstate.sync_client()
    if r is not None:
        try:
            keys = [_block_key(k) for k in (_keys() or [])] + [_block_key("_unconfigured_")]
            r.delete(*keys)
        except Exception:  # noqa: BLE001 — best-effort
            pass


async def _record_call(key: str, n: int = 1) -> None:
    """Best-effort per-key daily usage tick (admin quota panel) — off the event loop."""
    import asyncio

    from app.store.ingest_state import record_upstream_call

    try:
        await asyncio.to_thread(record_upstream_call, "opendart", key, n)
    except Exception:  # noqa: BLE001 — accounting never blocks a data call
        pass


async def _corp_map() -> dict[str, dict]:
    """stock_code(6) -> {corp_code, corp_name}.

    Cached for a day (not the global 15-min TTL): every KR request needs this map, and
    re-downloading the multi-MB corpCode.xml zip hundreds of times a day both trips OpenDART's
    bulk-abuse throttling and burns the daily quota the evidence viewer shares. A quota error
    (status 020/021) is negative-cached briefly so a blocked key fails fast with the honest
    message instead of hammering the endpoint on every request."""

    async def _load() -> dict[str, dict]:
        last_exc: Exception | None = None
        for _ in range(max(1, len(_keys()))):
            key = _key()   # first available — raises honestly when all are spent
            content = await fetch_bytes(
                "opendart", f"{_BASE}/corpCode.xml", params={"crtfc_key": key}
            )
            await _record_call(key)
            try:
                zf = zipfile.ZipFile(io.BytesIO(content))
                xml = zf.read(zf.namelist()[0])
            except (zipfile.BadZipFile, IndexError) as exc:
                status, message = _parse_error_envelope(content)
                if status == "020":   # this key's daily quota is spent → rotate to the next
                    mark_quota_blocked(key)
                    last_exc = upstream_error("opendart", f"{status}: {message}")
                    continue
                raise upstream_error(
                    "opendart",
                    f"{status}: {message}" if status else f"corpCode.xml not a zip: {exc}")
            root = ElementTree.fromstring(xml)
            out: dict[str, dict] = {}
            for node in root.iter("list"):
                stock = (node.findtext("stock_code") or "").strip()
                if stock:
                    out[stock.zfill(6)] = {
                        "corp_code": (node.findtext("corp_code") or "").strip(),
                        "corp_name": (node.findtext("corp_name") or "").strip(),
                    }
            return out
        raise last_exc or upstream_error("opendart", "020: 사용한도를 초과하였습니다 (all keys quota-blocked)")

    if quota_blocked():
        raise upstream_error("opendart", "020: 사용한도를 초과하였습니다 (all keys quota-blocked)")
    return await cache.get_or_set("dart:corp_map", _load, ttl_seconds=_CORP_MAP_TTL)


async def _corp_code(ref: SecurityRef) -> str:
    if ref.cik:
        return ref.cik
    cmap = await _corp_map()
    row = cmap.get(ref.ticker.zfill(6))
    if not row:
        raise not_found(f"Unknown KR issue code '{ref.ticker}'.")
    return row["corp_code"]


async def _dart_json_uncached(path: str, params: dict) -> dict:
    """One OpenDART JSON call with quota-aware key rotation: a key answering 020 (daily
    사용한도 초과) is blocked until KST midnight and the request retries on the next key."""
    last_exc: Exception | None = None
    for _ in range(max(1, len(_keys()))):
        key = _key()   # raises honestly when no key is configured / all are spent
        data = await fetch_json("opendart", f"{_BASE}/{path}", params={"crtfc_key": key, **params})
        await _record_call(key)
        status = data.get("status")  # type: ignore[union-attr]
        if status == "013":  # no data
            return {"status": status, "list": []}
        if status == "020":   # this key's daily quota is spent → rotate to the next
            mark_quota_blocked(key)
            log.warning("opendart key …%s quota-blocked (020) — %d key(s) still available",
                        key[-4:], len(available_keys()))
            last_exc = upstream_error("opendart", f"{status}: {data.get('message')}")
            continue
        if status and status != "000":
            raise upstream_error("opendart", f"{status}: {data.get('message')}")
        return data  # type: ignore[return-value]
    raise last_exc or upstream_error("opendart", "020: 사용한도를 초과하였습니다 (all keys quota-blocked)")


async def _dart_json(path: str, params: dict) -> dict:
    """Cache-aware OpenDART JSON: read endpoints are cached per exact params (see
    ``_DART_CACHE_TTLS``) so ingestion, the feeds and chat don't re-spend quota on the same
    payload — e.g. /financials used to download the identical fnlttSinglAcntAll response three
    times (income/balance/cashflow extract different rows from ONE payload)."""
    ttl = _DART_CACHE_TTLS.get(path)
    if not ttl:
        return await _dart_json_uncached(path, params)
    ck = f"dart:{path}:" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    return await cache.get_or_set(ck, lambda: _dart_json_uncached(path, params), ttl_seconds=ttl)


# Report-name → rank (lower = more substantive, surfaced first). DART lists newest-first;
# a STABLE sort by rank keeps date order within each tier. 지분/소유 reports are the noise the
# date sort otherwise floods the list with, so they rank last.
def _filing_rank(report_nm: str | None) -> int:
    nm = report_nm or ""
    if any(k in nm for k in ("사업보고서", "반기보고서", "분기보고서")):
        return 0  # 정기보고서 — the narrative-bearing reports (위험요소·사업의 내용)
    if any(k in nm for k in ("주요사항보고", "감사보고서", "검토보고서")):
        return 1
    if any(k in nm for k in ("소유상황보고", "소유주식", "지분", "특정증권등")):
        return 3  # 지분/소유 disclosures — high-frequency noise → last
    return 2


class OpenDartProvider:
    async def company_facts(self, ref: SecurityRef) -> CompanyFacts:
        corp = await _corp_code(ref)
        data = await _dart_json("company.json", {"corp_code": corp})
        return CompanyFacts(
            ticker=(data.get("stock_code") or ref.ticker).strip() or ref.ticker,
            name=data.get("corp_name"),
            cik=corp,
            industry=data.get("induty_code"),
            sector=data.get("induty_code"),
            exchange=_CORP_CLS.get(data.get("corp_cls", ""), None),
            is_active=True,
            location=data.get("adres"),
            sic_code=data.get("induty_code"),
        )

    async def list_tickers(self) -> list[str]:
        cmap = await _corp_map()
        return sorted(cmap.keys())

    async def list_ciks(self) -> list[str]:
        # KR's "CIK" equivalent is the OpenDART corp_code.
        cmap = await _corp_map()
        return sorted({row.get("corp_code") for row in cmap.values() if row.get("corp_code")})

    async def as_reported(self, ref, period: str = "annual", limit: int = 4) -> list[dict]:
        # KR as-reported (raw DART XBRL) is a separate, heavier parse — deferred to PH-7b.
        return []

    async def search_companies(self, query: str, limit: int) -> list[CompanySearchResult]:
        cmap = await _corp_map()
        rows = (
            {"ticker": stock, "name": row.get("corp_name"), "cik": row.get("corp_code")}
            for stock, row in cmap.items()
        )
        ranked = rank_company_matches(query, rows)
        return [
            CompanySearchResult(name=r["name"], ticker=r["ticker"], market="KR", cik=r["cik"])
            for r in ranked[:limit]
        ]

    async def _statements(self, ref, period, limit, field_map, sj_divs, model):
        corp = await _corp_code(ref)
        out = []
        for year, code, rp in _periods(period, limit):
            data = await _dart_json(
                "fnlttSinglAcntAll.json",
                {"corp_code": corp, "bsns_year": str(year), "reprt_code": code, "fs_div": "CFS"},
            )
            rows = data.get("list") or []
            if not rows:
                data = await _dart_json(
                    "fnlttSinglAcntAll.json",
                    {"corp_code": corp, "bsns_year": str(year), "reprt_code": code, "fs_div": "OFS"},
                )
                rows = data.get("list") or []
            fields = _extract(rows, field_map, sj_divs)
            if fields:
                rcept_no = rows[0].get("rcept_no") if rows else None
                out.append(
                    model(
                        ticker=ref.ticker,
                        report_period=rp,
                        fiscal_period=_fiscal_period(year, code),
                        period=period,
                        currency="KRW",
                        accession_number=rcept_no,
                        filing_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else None,
                        **fields,
                    )
                )
            if len(out) >= limit:
                break
        if not out:
            raise not_found(f"No OpenDART financial data for '{ref.ticker}'.")
        return out

    async def income_statements(self, ref: SecurityRef, period: str, limit: int) -> list[IncomeStatement]:
        return await self._statements(ref, period, limit, INCOME_MAP, {"IS", "CIS"}, IncomeStatement)

    async def balance_sheets(self, ref: SecurityRef, period: str, limit: int) -> list[BalanceSheet]:
        return await self._statements(ref, period, limit, BALANCE_MAP, {"BS"}, BalanceSheet)

    async def cash_flow_statements(self, ref: SecurityRef, period: str, limit: int) -> list[CashFlowStatement]:
        return await self._statements(ref, period, limit, CASHFLOW_MAP, {"CF"}, CashFlowStatement)

    async def filings(self, ref: SecurityRef, filing_types: list[str] | None, limit: int) -> list[Filing]:
        corp = await _corp_code(ref)
        this_year = date.today().year
        # Pull a WIDE window (DART returns newest-first), then rank so substantive reports
        # (정기보고서·주요사항·감사) surface ahead of the high-frequency 지분/소유 noise that
        # otherwise dominates by date. `filing_type` (if given) post-filters by report name.
        data = await _dart_json(
            "list.json",
            {
                "corp_code": corp,
                "bgn_de": f"{this_year - 2}0101",
                "end_de": f"{this_year}1231",
                "page_count": "100",
            },
        )
        rows = list(data.get("list") or [])
        if filing_types:
            wanted = [t for t in filing_types if t]
            rows = [r for r in rows if any(w in (r.get("report_nm") or "") for w in wanted)]
        rows.sort(key=lambda r: _filing_rank(r.get("report_nm")))  # stable → keeps date order within a rank
        out: list[Filing] = []
        for row in rows:
            rcp = row.get("rcept_no")
            rcept_dt = row.get("rcept_dt")  # YYYYMMDD
            fdate = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}" if rcept_dt else None
            out.append(
                Filing(
                    cik=int(corp),
                    accession_number=rcp,
                    filing_type=row.get("report_nm"),
                    filing_date=fdate,
                    report_date=fdate,
                    ticker=ref.ticker,
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}",
                )
            )
            if len(out) >= limit:
                break
        if not out:
            raise not_found(f"No OpenDART filings for '{ref.ticker}'.")
        return out

    async def earnings_disclosures(self, ref: SecurityRef, limit: int) -> list[dict]:
        """Recent 잠정실적 공정공시 — '영업(잠정)실적(공정공시)' / '연결재무제표기준영업(잠정)실적(공정공시)'.
        The KR earnings ANNOUNCEMENT (management's preliminary results + commentary), filed on DART:
        the free analog of a US earnings press release/call (KR has no free transcript/audio API).
        DART lists newest-first → returns the most recent matches as {rcept_no, report_nm, date, url}."""
        corp = await _corp_code(ref)
        this_year = date.today().year
        data = await _dart_json(
            "list.json",
            {"corp_code": corp, "bgn_de": f"{this_year - 2}0101",
             "end_de": f"{this_year}1231", "page_count": "100"},
        )
        out: list[dict] = []
        for row in data.get("list") or []:
            nm = row.get("report_nm") or ""
            # 잠정실적 공정공시 only — excludes the other 공정공시 (공급계약 등) the date sort floods in.
            if not ("공정공시" in nm and "실적" in nm):
                continue
            rcp = row.get("rcept_no")
            dt = row.get("rcept_dt")  # YYYYMMDD
            out.append({
                "rcept_no": rcp, "report_nm": nm,
                "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}" if dt and len(dt) >= 8 else None,
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp}",
            })
            if len(out) >= limit:
                break
        return out


class OpenDartMetricsProvider:
    """KR metrics derived from OpenDART fundamentals + the live (Yahoo) price.

    market_cap = issued shares x price; P/E = price / EPS; P/B = market_cap /
    equity. Requires OPENDART_API_KEY. Fields that can't be derived are null."""

    async def _shares(self, ref: SecurityRef) -> float | None:
        corp = await _corp_code(ref)
        this_year = date.today().year
        for year in range(this_year - 1, this_year - 4, -1):
            data = await _dart_json(
                "stockTotqySttus.json",
                {"corp_code": corp, "bsns_year": str(year), "reprt_code": _ANNUAL},
            )
            rows = data.get("list") or []
            # prefer the 합계 (total) row, then 보통주 (common)
            for want in ("합계", "보통주"):
                for row in rows:
                    if (row.get("se") or "").strip().startswith(want):
                        n = _amount(row.get("istc_totqy"))
                        if n:
                            return n
        return None

    async def metrics_snapshot(self, ref: SecurityRef) -> FinancialMetricSnapshot:
        from app.providers.us.yahoo import YahooProvider

        provider = OpenDartProvider()
        snap = FinancialMetricSnapshot(ticker=ref.ticker)
        try:
            price = (await YahooProvider().snapshot(ref)).price
        except Exception:
            price = None

        incomes = await provider.income_statements(ref, "annual", 1)
        balances = await provider.balance_sheets(ref, "annual", 1)
        eps = incomes[0].earnings_per_share if incomes else None
        equity = balances[0].shareholders_equity if balances else None
        shares = await self._shares(ref)

        if price and shares:
            snap.market_cap = price * shares
        if price and eps:
            snap.price_to_earnings_ratio = round(price / eps, 4)
        if snap.market_cap and equity:
            snap.price_to_book_ratio = round(snap.market_cap / equity, 4)
        return snap


class OpenDartEarningsProvider:
    """KR earnings actuals from DART quarterly statements.

    KR reports have no SEC form type; ``source_type`` uses the closest analog
    (분기/반기 → 10-Q, 사업보고서 → 10-K). Consensus/surprise fields are null."""

    async def earnings(self, ref: SecurityRef, limit: int) -> list[EarningsRecord]:
        provider = OpenDartProvider()
        incomes = await provider.income_statements(ref, "quarterly", limit)
        out: list[EarningsRecord] = []
        for s in incomes:
            accn = s.accession_number
            fdate = (
                f"{accn[:4]}-{accn[4:6]}-{accn[6:8]}" if accn and len(accn) >= 8 else str(s.report_period)
            )
            source = "10-K" if (s.fiscal_period or "").endswith("FY") else "10-Q"
            dim = EarningsTimeDimension(
                revenue=s.revenue,
                earnings_per_share=s.earnings_per_share,
                gross_profit=s.gross_profit,
                operating_income=s.operating_income,
                net_income=s.net_income,
            )
            out.append(
                EarningsRecord(
                    ticker=ref.ticker,
                    report_period=s.report_period,
                    fiscal_period=s.fiscal_period,
                    currency="KRW",
                    source_type=source,
                    filing_date=fdate,
                    filing_url=s.filing_url,
                    accession_number=accn or "",
                    quarterly=dim,
                )
            )
        if not out:
            raise not_found(f"No OpenDART earnings for '{ref.ticker}'.")
        return out


class OpenDartInsiderProvider:
    """KR insider activity from DART 임원·주요주주 특정증권등 소유상황보고 (elestock)."""

    async def insider_trades(self, ref: SecurityRef, limit: int) -> list[InsiderTrade]:
        corp = await _corp_code(ref)
        this_year = date.today().year
        data = await _dart_json(
            "elestock.json",
            {"corp_code": corp, "bgn_de": f"{this_year - 2}0101", "end_de": f"{this_year}1231"},
        )
        out: list[InsiderTrade] = []
        for row in data.get("list") or []:
            fdate = _kr_date(row.get("rcept_dt"))
            change = _amount(row.get("sp_stock_lmp_irds_cnt"))  # 소유 증감수
            after = _amount(row.get("sp_stock_lmp_cnt"))  # 특정증권등 소유수
            txn_type = None
            if change is not None:
                txn_type = "취득" if change > 0 else "처분" if change < 0 else "변동없음"
            out.append(
                InsiderTrade(
                    ticker=ref.ticker,
                    issuer=row.get("corp_name"),
                    name=row.get("repror"),
                    title=row.get("isu_exctv_ofcps"),
                    is_board_director=(row.get("isu_exctv_rgist_at") == "등기임원"),
                    transaction_date=fdate,
                    transaction_shares=change,
                    shares_owned_after_transaction=after,
                    transaction_type=txn_type,
                    filing_date=fdate,
                )
            )
            if len(out) >= limit:
                break
        if not out:
            raise not_found(f"No OpenDART insider (elestock) data for '{ref.ticker}'.")
        return out[:limit]
