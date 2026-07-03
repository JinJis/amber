"""Corporate-actions ingestion (PH-PIPE).

Collects dividend/split events (``CorporateAction``) for a universe and persists them — the second
of the two "data we serve but didn't store" gaps. Each run records an ``IngestionJob`` (kind
``corp_actions``); best-effort per ticker, bounded retries. Shares ``_ingest_helpers`` and the
``run_ticker_job`` lifecycle with the sibling ``prices_ingest`` module (split out in RF-05).
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select

from app.providers.registry import get_prices_provider
from app.store._ingest_helpers import _incremental_start, _num, _retry, _to_date
from app.store.db import SessionLocal, init_db
from app.store.jobs import run_ticker_job
from app.store.models import CorporateAction
from app.symbols import Market, build_ref


def _last_event_date(market: str, ticker: str) -> date | None:
    with SessionLocal() as db:
        return db.scalar(select(func.max(CorporateAction.event_date)).where(
            CorporateAction.market == market, CorporateAction.ticker == ticker))


async def ingest_corp_actions_ticker(market: Market, ticker: str, start: date, end: date, retries: int = 1) -> int:
    ref = build_ref(market, ticker)
    provider = get_prices_provider(market)
    data = await _retry(lambda: provider.corporate_actions(ref, start, end), retries)
    rows = []
    for d in (data.get("dividends") or []):
        ed = _to_date(d.get("ex_date"))
        if ed is not None:
            rows.append({"market": market.value, "ticker": ref.ticker, "kind": "dividend",
                         "event_date": ed, "amount": _num(d.get("amount")), "ratio": None, "source": "yahoo"})
    for s in (data.get("splits") or []):
        ed = _to_date(s.get("date"))
        if ed is not None:
            rows.append({"market": market.value, "ticker": ref.ticker, "kind": "split",
                         "event_date": ed, "amount": None, "ratio": s.get("ratio"), "source": "yahoo"})

    def _write() -> int:
        with SessionLocal() as db:
            for r in rows:
                existing = db.execute(
                    select(CorporateAction).where(
                        CorporateAction.market == r["market"], CorporateAction.ticker == r["ticker"],
                        CorporateAction.kind == r["kind"], CorporateAction.event_date == r["event_date"],
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.amount, existing.ratio = r["amount"], r["ratio"]
                else:
                    db.add(CorporateAction(**r))
            db.commit()
        return len(rows)

    return await asyncio.to_thread(_write)


async def run_corp_actions_ingest(market: str, tickers: list[str], years: int = 10, retries: int = 1,
                                  overlap_days: int = 35) -> dict:
    """Collect dividends + splits for ``tickers`` into ``CorporateAction``; recorded as a job.

    Incremental: each ticker is fetched only from its last stored event (minus ``overlap_days``);
    a ticker with no events yet gets the full ``years``-year backfill."""
    init_db()
    mk = Market(market)
    end = date.today()
    full_start = date(end.year - years, 1, 1)

    async def _one(t: str) -> int:
        nt = build_ref(mk, t).ticker
        last = await asyncio.to_thread(_last_event_date, market, nt)
        start = _incremental_start(last, full_start, overlap_days)
        return await ingest_corp_actions_ticker(mk, t, start, end, retries)

    return await run_ticker_job("corp_actions", market, f"corp_actions · {len(tickers)} tickers (증분)", tickers, _one)
