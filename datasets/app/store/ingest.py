"""Ingest statement line-items into the store.

This slice reuses the on-demand financials providers and persists what they
return, which proves the schema + query path end-to-end. The production path is
the same writer fed by a **bulk** loader (SEC ``companyfacts.zip`` / DART batch)
instead of per-ticker calls — far more periods, no per-request rate limits.
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import select

from app.config import settings
from app.providers.registry import get_company_provider, get_financials_provider
from app.store.db import SessionLocal, init_db
from app.store.models import Company, FinancialFact
from app.symbols import Market, build_ref

# (statement key, provider method)
_STATEMENTS = [
    ("income", "income_statements"),
    ("balance", "balance_sheets"),
    ("cashflow", "cash_flow_statements"),
]
# non-numeric / structural fields that are not line items
_SKIP = {"ticker", "report_period", "fiscal_period", "period", "currency", "accession_number", "filing_url"}


def _source(market: Market) -> str:
    return "sec_edgar" if market is Market.US else "opendart"


def _upsert_fact(db, row: dict) -> None:
    existing = db.execute(
        select(FinancialFact).where(
            FinancialFact.market == row["market"],
            FinancialFact.ticker == row["ticker"],
            FinancialFact.statement == row["statement"],
            FinancialFact.line_item == row["line_item"],
            FinancialFact.period == row["period"],
            FinancialFact.report_period == row["report_period"],
            FinancialFact.accession_number == row["accession_number"],
        )
    ).scalar_one_or_none()
    if existing:
        existing.value = row["value"]
        existing.filing_date = row["filing_date"]
    else:
        db.add(FinancialFact(**row))


async def ingest_ticker(market: Market, ticker: str, period: str = "annual", limit: int = 8) -> int:
    ref = build_ref(market, ticker)
    fin = get_financials_provider(market)
    rows: list[dict] = []
    for statement, method in _STATEMENTS:
        try:
            statements = await getattr(fin, method)(ref, period, limit)
        except Exception:
            continue
        for s in statements:
            d = s.model_dump()
            rp = d.get("report_period")
            if not rp:
                continue
            rp = rp if isinstance(rp, date) else date.fromisoformat(str(rp)[:10])
            filing = d.get("filing_date")
            for line_item, value in d.items():
                if line_item in _SKIP or value is None or not isinstance(value, (int, float)):
                    continue
                rows.append(
                    {
                        "market": market.value, "ticker": ref.ticker, "cik": ref.cik,
                        "statement": statement, "line_item": line_item, "value": float(value),
                        "currency": d.get("currency"), "period": period, "report_period": rp,
                        "fiscal_period": d.get("fiscal_period"),
                        "filing_date": filing if isinstance(filing, date) else None,
                        "accession_number": d.get("accession_number") or "", "source": _source(market),
                    }
                )
    try:
        facts = await get_company_provider(market).company_facts(ref)
    except Exception:
        facts = None

    def _write() -> None:
        with SessionLocal() as db:
            for r in rows:
                _upsert_fact(db, r)
            if facts:
                comp = db.get(Company, (market.value, ref.ticker)) or Company(market=market.value, ticker=ref.ticker)
                comp.name, comp.cik = facts.name, facts.cik
                comp.sector, comp.exchange = facts.sector, facts.exchange
                comp.currency = "USD" if market is Market.US else "KRW"
                db.merge(comp)
            db.commit()

    await asyncio.to_thread(_write)
    return len(rows)


async def ingest_universe(market: Market, tickers: list[str], period: str = "annual", limit: int = 8) -> dict:
    init_db()
    out: dict[str, int] = {}
    for t in tickers:
        try:
            out[t] = await ingest_ticker(market, t, period, limit)
        except Exception as exc:  # keep going across the universe
            out[t] = -1
            print(f"  ! {market.value}:{t} failed: {exc}")
    return out
