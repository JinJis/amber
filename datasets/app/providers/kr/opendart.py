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

_BASE = "https://opendart.fss.or.kr/api"
_CORP_CLS = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "ETC"}


def _key() -> str:
    if not settings.opendart_api_key:
        raise bad_request("OPENDART_API_KEY is not configured.")
    return settings.opendart_api_key


async def _corp_map() -> dict[str, dict]:
    """stock_code(6) -> {corp_code, corp_name}."""

    async def _load() -> dict[str, dict]:
        content = await fetch_bytes(
            "opendart", f"{_BASE}/corpCode.xml", params={"crtfc_key": _key()}
        )
        try:
            zf = zipfile.ZipFile(io.BytesIO(content))
            xml = zf.read(zf.namelist()[0])
        except (zipfile.BadZipFile, IndexError) as exc:
            raise upstream_error("opendart", f"corpCode.xml not a zip: {exc}")
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

    return await cache.get_or_set("dart:corp_map", _load)


async def _corp_code(ref: SecurityRef) -> str:
    if ref.cik:
        return ref.cik
    cmap = await _corp_map()
    row = cmap.get(ref.ticker.zfill(6))
    if not row:
        raise not_found(f"Unknown KR issue code '{ref.ticker}'.")
    return row["corp_code"]


async def _dart_json(path: str, params: dict) -> dict:
    params = {"crtfc_key": _key(), **params}
    data = await fetch_json("opendart", f"{_BASE}/{path}", params=params)
    status = data.get("status")  # type: ignore[union-attr]
    if status == "013":  # no data
        return {"status": status, "list": []}
    if status and status != "000":
        raise upstream_error("opendart", f"{status}: {data.get('message')}")
    return data  # type: ignore[return-value]


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
