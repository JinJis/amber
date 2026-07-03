"""US provider backed by the free SEC EDGAR APIs.

* ticker -> CIK     https://www.sec.gov/files/company_tickers.json
* company facts     https://data.sec.gov/submissions/CIK##########.json
* financials (XBRL) https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
* filings           (submissions "recent" block)

SEC requires a descriptive User-Agent and rate-limits to ~10 req/s. Responses
are cached (TTL) so repeat calls don't re-hit EDGAR.
"""

from __future__ import annotations

from xml.etree import ElementTree

from app.cache import cache
from app.config import settings
from app.errors import not_found, not_implemented
from app.http import fetch_json, fetch_text
from app.models.generated import (
    BalanceSheet,
    CashFlowStatement,
    CompanyFacts,
    CompanySearchResult,
    EarningsRecord,
    EarningsTimeDimension,
    Filing,
    FinancialMetricSnapshot,
    Fund,
    FundHolding,
    IncomeStatement,
    InsiderTrade,
    InstitutionalHolding,
)
from app.providers._parse_utils import parse_float as _num
from app.providers.search_util import rank_company_matches
from app.symbols import SecurityRef
from app.providers.us.sec_edgar_concepts import BALANCE_MAP, CASHFLOW_MAP, INCOME_MAP
from app.providers.us.sec_edgar_xbrl import (
    _assemble,
    _latest_instant_rows,
    _observations,
    _period_ok,
    _ttm_rows,
)

# Re-exported (not used directly here): app.store.bulk imports all_facts_from_companyfacts;
# tests import the assembly/url helpers via this module.
from app.providers.us.sec_edgar_xbrl import (  # noqa: F401
    _days_between,
    _filing_url,
    _fiscal_label,
    _ttm_value,
    all_facts_from_companyfacts,
)

# SEC Form 4 transaction codes -> human-readable description.
_TXN_CODES = {
    "P": "Open market purchase",
    "S": "Open market sale",
    "A": "Grant or award",
    "D": "Disposition to issuer",
    "F": "Payment of exercise/tax by shares",
    "G": "Gift",
    "M": "Exercise of derivative",
    "X": "Exercise of in-the-money derivative",
    "C": "Conversion of derivative",
    "J": "Other acquisition or disposition",
}

_UA = {"User-Agent": settings.sec_edgar_user_agent}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def _cik10(cik: str | int) -> str:
    return str(int(cik)).zfill(10)


async def _ticker_index() -> dict[str, dict]:
    async def _load() -> dict[str, dict]:
        data = await fetch_json("sec_edgar", _TICKERS_URL, headers=_UA)
        out: dict[str, dict] = {}
        for row in data.values():  # type: ignore[union-attr]
            out[row["ticker"].upper()] = row
        return out

    return await cache.get_or_set("sec:ticker_index", _load)


async def _resolve_cik(ref: SecurityRef) -> str:
    if ref.cik:
        return _cik10(ref.cik)
    idx = await _ticker_index()
    row = idx.get(ref.ticker.upper())
    if not row:
        raise not_found(f"Unknown US ticker '{ref.ticker}'.")
    return _cik10(row["cik_str"])


async def _submissions(cik10: str) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    return await cache.get_or_set(
        f"sec:submissions:{cik10}", lambda: fetch_json("sec_edgar", url, headers=_UA)
    )  # type: ignore[return-value]


async def _company_facts_raw(cik10: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    return await cache.get_or_set(
        f"sec:facts:{cik10}", lambda: fetch_json("sec_edgar", url, headers=_UA)
    )  # type: ignore[return-value]


class SecEdgarProvider:
    # --- company ---------------------------------------------------------
    async def company_facts(self, ref: SecurityRef) -> CompanyFacts:
        cik10 = await _resolve_cik(ref)
        sub = await _submissions(cik10)
        tickers = sub.get("tickers") or []
        exchanges = sub.get("exchanges") or []
        addr = (sub.get("addresses") or {}).get("business") or {}
        location = ", ".join(
            x for x in [addr.get("city"), addr.get("stateOrCountry")] if x
        ) or None
        return CompanyFacts(
            ticker=(tickers[0] if tickers else ref.ticker).upper(),
            name=sub.get("name"),
            cik=cik10,
            industry=sub.get("sicDescription"),
            sector=sub.get("sicDescription"),
            category=sub.get("category"),
            exchange=exchanges[0] if exchanges else None,
            is_active=True,
            location=location,
            sec_filings_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik10}&type=&dateb=&owner=include&count=40",
            sic_code=sub.get("sic"),
            sic_industry=sub.get("sicDescription"),
            sic_sector=sub.get("sicDescription"),
        )

    async def list_tickers(self) -> list[str]:
        idx = await _ticker_index()
        return sorted(idx.keys())

    async def list_ciks(self) -> list[str]:
        idx = await _ticker_index()
        return sorted({_cik10(row["cik_str"]) for row in idx.values()})

    async def as_reported(self, ref: SecurityRef, period: str = "annual", limit: int = 4) -> list[dict]:
        """Financials EXACTLY as filed in XBRL — every us-gaap concept for each of the
        most recent ``limit`` report periods (not normalised to our model). Keeps the
        latest-filed value per concept; gaps stay absent, never fabricated (PH-7)."""
        cik10 = await _resolve_cik(ref)
        raw = await _company_facts_raw(cik10)
        gaap = (raw.get("facts") or {}).get("us-gaap") or {}
        periods: dict[str, dict] = {}
        for concept, node in gaap.items():
            label = node.get("label")
            for unit, rows in (node.get("units") or {}).items():
                for row in rows:
                    if not isinstance(row.get("val"), (int, float)):
                        continue
                    if not _period_ok(row, period, instant="start" not in row):
                        continue
                    end = row.get("end")
                    if not end:
                        continue
                    bucket = periods.setdefault(end, {})
                    prev = bucket.get(concept)
                    if prev is None or (row.get("filed") or "") > (prev.get("filed") or ""):
                        bucket[concept] = {
                            "concept": concept, "label": label, "value": float(row["val"]),
                            "unit": unit, "form": row.get("form"),
                            "accession_number": row.get("accn"), "filed": row.get("filed"),
                        }
        out: list[dict] = []
        for end in sorted(periods, reverse=True)[:limit]:
            items = sorted(periods[end].values(), key=lambda x: x["concept"])
            out.append({"report_period": end, "period": period, "line_items": items})
        return out

    async def search_companies(self, query: str, limit: int) -> list[CompanySearchResult]:
        idx = await _ticker_index()
        rows = (
            {"ticker": tk, "name": row.get("title"), "cik": _cik10(row["cik_str"])}
            for tk, row in idx.items()
        )
        ranked = rank_company_matches(query, rows)
        return [
            CompanySearchResult(name=r["name"], ticker=r["ticker"], market="US", cik=r["cik"])
            for r in ranked[:limit]
        ]

    # --- financial statements -------------------------------------------
    async def income_statements(self, ref: SecurityRef, period: str, limit: int) -> list[IncomeStatement]:
        cik10 = await _resolve_cik(ref)
        facts = await _company_facts_raw(cik10)
        gaap = facts.get("facts", {}).get("us-gaap", {})
        spine = INCOME_MAP["revenue"] + INCOME_MAP["net_income"]
        if period == "ttm":
            rows = _ttm_rows(gaap, INCOME_MAP, spine)
        else:
            rows = _assemble(gaap, INCOME_MAP, spine, period, limit, instant=False, cik10=cik10)
        return [
            IncomeStatement(ticker=ref.ticker, period=period, currency="USD", **r)
            for r in rows
        ]

    async def balance_sheets(self, ref: SecurityRef, period: str, limit: int) -> list[BalanceSheet]:
        cik10 = await _resolve_cik(ref)
        facts = await _company_facts_raw(cik10)
        gaap = facts.get("facts", {}).get("us-gaap", {})
        if period == "ttm":
            rows = _latest_instant_rows(gaap, BALANCE_MAP, ["Assets"], limit)
        else:
            rows = _assemble(gaap, BALANCE_MAP, ["Assets"], period, limit, instant=True, cik10=cik10)
        out = []
        for r in rows:
            cur, noncur = r.get("current_debt"), r.get("non_current_debt")
            if cur is not None or noncur is not None:
                r["total_debt"] = (cur or 0) + (noncur or 0)
            out.append(BalanceSheet(ticker=ref.ticker, period=period, currency="USD", **r))
        return out

    async def cash_flow_statements(self, ref: SecurityRef, period: str, limit: int) -> list[CashFlowStatement]:
        cik10 = await _resolve_cik(ref)
        facts = await _company_facts_raw(cik10)
        gaap = facts.get("facts", {}).get("us-gaap", {})
        spine = ["NetCashProvidedByUsedInOperatingActivities"]
        if period == "ttm":
            rows = _ttm_rows(gaap, CASHFLOW_MAP, spine)
        else:
            rows = _assemble(gaap, CASHFLOW_MAP, spine, period, limit, instant=False, cik10=cik10)
        out = []
        for r in rows:
            ops, capex = r.get("net_cash_flow_from_operations"), r.get("capital_expenditure")
            if ops is not None and capex is not None:
                r["free_cash_flow"] = ops - capex
            out.append(CashFlowStatement(ticker=ref.ticker, period=period, currency="USD", **r))
        return out

    # --- filings ---------------------------------------------------------
    async def filings(self, ref: SecurityRef, filing_types: list[str] | None, limit: int) -> list[Filing]:
        cik10 = await _resolve_cik(ref)
        sub = await _submissions(cik10)
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accns = recent.get("accessionNumber") or []
        fdates = recent.get("filingDate") or []
        rdates = recent.get("reportDate") or []
        prim = recent.get("primaryDocument") or []
        wanted = {t.upper() for t in filing_types} if filing_types else None
        out: list[Filing] = []
        for i in range(len(forms)):
            if wanted and forms[i].upper() not in wanted:
                continue
            accn = accns[i]
            nodash = accn.replace("-", "")
            doc = prim[i] if i < len(prim) and prim[i] else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{nodash}/{doc}"
            out.append(
                Filing(
                    cik=int(cik10),
                    accession_number=accn,
                    filing_type=forms[i],
                    report_date=rdates[i] or None if i < len(rdates) else None,
                    filing_date=fdates[i] if i < len(fdates) else None,
                    ticker=ref.ticker,
                    url=url,
                )
            )
            if len(out) >= limit:
                break
        return out


class SecEdgarMetricsProvider:
    """Best-effort US metrics snapshot derived from XBRL + the EOD price feed.

    market_cap = latest reported shares outstanding x latest close. A handful of
    valuation ratios are derived from the most recent annual statement. Fields
    that cannot be derived are left null (honest gaps, not zeros)."""

    async def metrics_snapshot(self, ref: SecurityRef) -> FinancialMetricSnapshot:
        from app.providers.us.yahoo import YahooProvider

        cik10 = await _resolve_cik(ref)
        facts = await _company_facts_raw(cik10)
        gaap = facts.get("facts", {}).get("us-gaap", {})

        shares = _latest(gaap, ["CommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding"])
        eps = _latest(gaap, ["EarningsPerShareDiluted", "EarningsPerShareBasic"])
        equity = _latest(gaap, ["StockholdersEquity"])

        snap = FinancialMetricSnapshot(ticker=ref.ticker)
        try:
            price_snap = await YahooProvider().snapshot(ref)
            price = price_snap.price
        except Exception:
            price = None
        if price and shares:
            snap.market_cap = price * shares
        if price and eps:
            snap.price_to_earnings_ratio = round(price / eps, 4) if eps else None
        if snap.market_cap and equity:
            snap.price_to_book_ratio = round(snap.market_cap / equity, 4)
        return snap


def _latest(gaap: dict, concepts: list[str]) -> float | None:
    best = None
    best_end = ""
    for concept in concepts:
        for row in _observations(gaap, concept):
            end = row.get("end", "")
            if end > best_end and row.get("val") is not None:
                best, best_end = row["val"], end
    return best


# --- XML / number helpers (insider + 13F) — `_num` = shared parse_float (RF-02) ----------
def _local(tag: str) -> str:
    return tag.split("}")[-1]


async def _filing_meta(cik10: str) -> dict[str, tuple[str, str]]:
    """accession_number -> (filing_date, form) from the submissions 'recent' block."""
    sub = await _submissions(cik10)
    recent = (sub.get("filings") or {}).get("recent") or {}
    accns = recent.get("accessionNumber") or []
    fdates = recent.get("filingDate") or []
    forms = recent.get("form") or []
    return {accns[i]: (fdates[i] if i < len(fdates) else None, forms[i] if i < len(forms) else None) for i in range(len(accns))}


class SecEdgarEarningsProvider:
    """Earnings actuals from XBRL (revenue/EPS/margins by reported period).

    Consensus estimates and surprise fields are intentionally null — there is no
    free estimates feed; we never fabricate them."""

    async def earnings(self, ref: SecurityRef, limit: int) -> list[EarningsRecord]:
        cik10 = await _resolve_cik(ref)
        facts = await _company_facts_raw(cik10)
        gaap = facts.get("facts", {}).get("us-gaap", {})
        meta = await _filing_meta(cik10)
        spine = INCOME_MAP["revenue"] + INCOME_MAP["net_income"]
        rows = _assemble(gaap, INCOME_MAP, spine, "quarterly", limit, instant=False, cik10=cik10)
        out: list[EarningsRecord] = []
        for r in rows:
            accn = r.get("accession_number")
            fdate, form = meta.get(accn, (None, None))
            source = form if form in ("8-K", "10-Q", "10-K", "20-F") else "10-Q"
            dim = EarningsTimeDimension(
                revenue=r.get("revenue"),
                earnings_per_share=r.get("earnings_per_share"),
                gross_profit=r.get("gross_profit"),
                operating_income=r.get("operating_income"),
                net_income=r.get("net_income"),
                weighted_average_shares=r.get("weighted_average_shares"),
                weighted_average_shares_diluted=r.get("weighted_average_shares_diluted"),
            )
            out.append(
                EarningsRecord(
                    ticker=ref.ticker.upper(),
                    report_period=r["report_period"],
                    fiscal_period=r.get("fiscal_period"),
                    currency="USD",
                    source_type=source,
                    filing_date=fdate or r["report_period"],
                    filing_url=r.get("filing_url"),
                    accession_number=accn or "",
                    quarterly=dim,
                )
            )
        if not out:
            raise not_found(f"No earnings data for '{ref.ticker}'.")
        return out


def _parse_form4(xml: str, ticker: str) -> tuple[str | None, str | None, str | None, bool | None, list[dict]]:
    root = ElementTree.fromstring(xml)
    issuer = root.findtext("issuer/issuerName")
    owner = root.findtext("reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    is_dir = None
    title = None
    if rel is not None:
        is_dir = rel.findtext("isDirector") in ("1", "true")
        title = rel.findtext("officerTitle")
    txns: list[dict] = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        shares = _num(tx.findtext("transactionAmounts/transactionShares/value"))
        price = _num(tx.findtext("transactionAmounts/transactionPricePerShare/value"))
        ad = tx.findtext("transactionAmounts/transactionAcquiredDisposedCode/value")
        signed = (shares * (-1 if ad == "D" else 1)) if shares is not None else None
        txns.append(
            {
                "code": tx.findtext("transactionCoding/transactionCode"),
                "date": tx.findtext("transactionDate/value"),
                "shares": signed,
                "price": price,
                "owned_after": _num(tx.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value")),
                "security": tx.findtext("securityTitle/value"),
            }
        )
    return issuer, owner, title, is_dir, txns


class SecEdgarInsiderProvider:
    """Insider transactions parsed from SEC Form 4 XML."""

    async def _xml_name(self, cik10: str, nodash: str, primary: str) -> str:
        raw = primary.split("/")[-1] if primary else ""
        if raw.endswith(".xml"):
            return raw
        idx = await fetch_json(
            "sec_edgar", f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{nodash}/index.json", headers=_UA
        )
        for item in (idx.get("directory", {}) or {}).get("item", []):
            n = item.get("name", "")
            if n.endswith(".xml") and not n.startswith("xsl"):
                return n
        return ""

    async def insider_trades(self, ref: SecurityRef, limit: int) -> list[InsiderTrade]:
        cik10 = await _resolve_cik(ref)
        sub = await _submissions(cik10)
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accns = recent.get("accessionNumber") or []
        fdates = recent.get("filingDate") or []
        prim = recent.get("primaryDocument") or []
        out: list[InsiderTrade] = []
        for i in range(len(forms)):
            if forms[i] != "4":
                continue
            accn = accns[i]
            nodash = accn.replace("-", "")
            name = await self._xml_name(cik10, nodash, prim[i] if i < len(prim) else "")
            if not name:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{nodash}/{name}"
            try:
                xml = await fetch_text("sec_edgar", url, headers=_UA)
                issuer, owner, title, is_dir, txns = _parse_form4(xml, ref.ticker)
            except Exception:
                continue
            for t in txns:
                value = t["shares"] * t["price"] if t["shares"] is not None and t["price"] else None
                out.append(
                    InsiderTrade(
                        ticker=ref.ticker.upper(),
                        issuer=issuer,
                        name=owner,
                        title=title,
                        is_board_director=is_dir,
                        transaction_date=t["date"],
                        transaction_shares=t["shares"],
                        transaction_price_per_share=t["price"],
                        transaction_value=value,
                        shares_owned_after_transaction=t["owned_after"],
                        security_title=t["security"],
                        transaction_type=_TXN_CODES.get(t["code"], t["code"]),
                        filing_date=fdates[i] if i < len(fdates) else None,
                    )
                )
            if len(out) >= limit:
                break
        if not out:
            raise not_found(f"No insider Form 4 data for '{ref.ticker}'.")
        return out[:limit]


def _parse_13f(xml: str, report_period: str | None, filing_date: str | None, form: str, accn: str) -> list[InstitutionalHolding]:
    root = ElementTree.fromstring(xml)
    out: list[InstitutionalHolding] = []
    for info in root.iter():
        if _local(info.tag) != "infoTable":
            continue
        d = {_local(c.tag): c for c in info}
        ssh = None
        shrs = d.get("shrsOrPrnAmt")
        if shrs is not None:
            for c in shrs:
                if _local(c.tag) == "sshPrnamt":
                    ssh = _num(c.text)
        value = _num(d["value"].text) if "value" in d else None
        put_call = d["putCall"].text if "putCall" in d else None
        out.append(
            InstitutionalHolding(
                ticker=None,
                name_of_issuer=d["nameOfIssuer"].text if "nameOfIssuer" in d else None,
                cusip=d["cusip"].text if "cusip" in d else None,
                report_period=report_period,
                filing_date=filing_date,
                form_type=form if form in ("13F-HR", "13F-HR/A") else "13F-HR",
                accession_number=accn,
                title_of_class=d["titleOfClass"].text if "titleOfClass" in d else None,
                put_call=put_call or None,
                shares=int(ssh) if ssh is not None else None,
                value_usd=int(value) if value is not None else None,
            )
        )
    return out


class SecEdgar13FProvider:
    """13F holdings parsed from a filer's latest information table (filer_cik mode)."""

    async def _infotable_name(self, cik10: str, nodash: str) -> str:
        idx = await fetch_json(
            "sec_edgar", f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{nodash}/index.json", headers=_UA
        )
        xmls = [
            item.get("name", "")
            for item in (idx.get("directory", {}) or {}).get("item", [])
            if item.get("name", "").endswith(".xml")
        ]
        for n in xmls:
            low = n.lower()
            if "info" in low or "table" in low:
                return n
        for n in xmls:
            if n.lower() != "primary_doc.xml":
                return n
        return ""

    @staticmethod
    def _13f_filings(sub: dict) -> list[tuple[int, str, str | None, str | None, str]]:
        """Indices of 13F-HR/A filings in the submissions 'recent' block, newest first,
        as (idx, accession, report_date, filing_date, form)."""
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accns = recent.get("accessionNumber") or []
        fdates = recent.get("filingDate") or []
        rdates = recent.get("reportDate") or []
        out: list[tuple[int, str, str | None, str | None, str]] = []
        for i, f in enumerate(forms):
            if f in ("13F-HR", "13F-HR/A"):
                out.append((i, accns[i], rdates[i] if i < len(rdates) else None,
                            fdates[i] if i < len(fdates) else None, f))
        return out

    async def _holdings_for(self, cik10: str, accn: str, rdate: str | None,
                            fdate: str | None, form: str) -> list[InstitutionalHolding]:
        nodash = accn.replace("-", "")
        name = await self._infotable_name(cik10, nodash)
        if not name:
            raise not_found(f"No 13F information table found for accession {accn}.")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{nodash}/{name}"
        xml = await fetch_text("sec_edgar", url, headers=_UA)
        holdings = _parse_13f(xml, rdate, fdate, form, accn)
        holdings.sort(key=lambda h: h.value_usd or 0, reverse=True)
        return holdings

    async def by_filer(self, filer_cik: str, limit: int) -> list[InstitutionalHolding]:
        cik10 = _cik10(filer_cik)
        sub = await _submissions(cik10)
        filings = self._13f_filings(sub)
        if not filings:
            raise not_found(f"No 13F-HR filing for CIK {filer_cik}.")
        _, accn, rdate, fdate, form = filings[0]
        holdings = await self._holdings_for(cik10, accn, rdate, fdate, form)
        return holdings[:limit]

    async def by_filer_quarters(self, filer_cik: str, quarters: int) -> list[dict]:
        """The `quarters` most recent distinct 13F reporting periods for a filer, newest
        first. Each entry: {report_period, filing_date, accession, form, holdings}.
        Powers quarter-over-quarter delta ('거장 매매') without a stored history."""
        cik10 = _cik10(filer_cik)
        sub = await _submissions(cik10)
        filings = self._13f_filings(sub)
        if not filings:
            raise not_found(f"No 13F-HR filing for CIK {filer_cik}.")
        out: list[dict] = []
        seen: set[str] = set()
        for _, accn, rdate, fdate, form in filings:
            key = rdate or accn
            if key in seen:  # skip amendments of an already-captured quarter
                continue
            seen.add(key)
            holdings = await self._holdings_for(cik10, accn, rdate, fdate, form)
            out.append({"report_period": rdate, "filing_date": fdate,
                        "accession": accn, "form": form, "holdings": holdings})
            if len(out) >= quarters:
                break
        return out

    async def by_ticker(self, ref: SecurityRef, limit: int) -> list[InstitutionalHolding]:
        raise not_implemented(
            "Ticker-mode 13F (which filers hold a security) requires a reverse CUSIP/holdings "
            "index that this build does not maintain yet. Use ?filer_cik=... for a filer's holdings."
        )


# --- Index funds / ETF holdings (SEC N-PORT) -----------------------------
_NPORT_FORMS = ("NPORT-P", "NPORT-P/A")


def _parse_nport(xml: str) -> tuple[dict, list[FundHolding]]:
    """Parse an SEC N-PORT primary_doc.xml → (fund header, holdings). Each portfolio
    position is an ``<invstOrSec>`` (name/title/cusip/isin/balance/valUSD/pctVal/assetCat)."""
    root = ElementTree.fromstring(xml)
    meta: dict = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "regName" and "name" not in meta:
            meta["name"] = (el.text or "").replace("(R)", "").strip() or None
        elif tag == "seriesName" and el.text and el.text.strip() not in ("", "N/A"):
            meta["series"] = el.text.strip()
        elif tag == "regCik" and "cik" not in meta:
            meta["cik"] = el.text
        elif tag == "repPdDate" and "as_of" not in meta:
            meta["as_of"] = el.text
        elif tag == "netAssets" and "net_assets" not in meta:
            meta["net_assets"] = _num(el.text)

    holdings: list[FundHolding] = []
    for inv in root.iter():
        if _local(inv.tag) != "invstOrSec":
            continue
        d = {_local(c.tag): c for c in inv}
        isin = None
        ids = d.get("identifiers")
        if ids is not None:
            isin = next((c.get("value") for c in ids if _local(c.tag) == "isin"), None)
        units = d["units"].text if "units" in d else ""
        bal = _num(d["balance"].text) if "balance" in d else None
        pct = _num(d["pctVal"].text) if "pctVal" in d else None
        holdings.append(FundHolding(
            ticker=None,  # N-PORT carries CUSIP/ISIN, not ticker (no reliable reverse map)
            name=(d["name"].text if "name" in d else (d["title"].text if "title" in d else None)),
            cusip=d["cusip"].text if "cusip" in d else None,
            isin=isin,
            weight=pct / 100 if pct is not None else None,   # pctVal is a percent → fraction
            market_value=_num(d["valUSD"].text) if "valUSD" in d else None,
            shares=bal if units == "NS" else None,           # NS = number of shares
            asset_class=d["assetCat"].text if "assetCat" in d else None,
        ))
    return {"name": meta.get("series") or meta.get("name"), "cik": meta.get("cik"),
            "as_of": meta.get("as_of"), "net_assets": meta.get("net_assets")}, holdings


class SecEdgarFundProvider:
    """ETF / fund portfolio holdings from the fund's latest SEC N-PORT filing."""

    async def holdings(self, ref: SecurityRef, limit: int) -> tuple[Fund, list[FundHolding]]:
        cik10 = await _resolve_cik(ref)
        sub = await _submissions(cik10)
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        accns = recent.get("accessionNumber") or []
        fdates = recent.get("filingDate") or []
        idx = next((i for i, f in enumerate(forms) if f in _NPORT_FORMS), None)
        if idx is None:
            raise not_found(f"No N-PORT (fund holdings) filing for '{ref.ticker}'.")
        accn = accns[idx]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accn.replace('-', '')}/primary_doc.xml"
        xml = await fetch_text("sec_edgar", url, headers=_UA)
        meta, holdings = _parse_nport(xml)
        holdings.sort(key=lambda h: h.market_value or 0, reverse=True)
        out = holdings[:limit]
        fund = Fund(
            name=meta["name"], cik=meta["cik"] or cik10, asset_class=None,
            as_of=meta["as_of"], filing_date=fdates[idx] if idx < len(fdates) else None,
            source="SEC EDGAR (N-PORT)", total_net_assets=meta["net_assets"],
            total_holdings=len(holdings), returned=len(out), offset=0,
        )
        return fund, out


