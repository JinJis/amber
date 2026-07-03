"""Prices ingestion (PH-PIPE).

Periodically collects end-of-day OHLCV (``PriceBar``) for a universe and persists it — one of the
two big "data we serve but didn't store" gaps. Each run records an ``IngestionJob`` (kind ``prices``)
so the admin console shows coverage + errors, and keeps going per-ticker (one failure never sinks the
run; bounded retries per ticker). Dividend/split events live in the sibling ``corp_actions_ingest``
module; both share ``_ingest_helpers`` and the ``run_ticker_job`` lifecycle (RF-05).
"""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select

from app.providers.registry import get_prices_provider
from app.store._ingest_helpers import _incremental_start, _num, _retry, _to_date
from app.store.db import SessionLocal, init_db
from app.store.jobs import run_ticker_job
from app.store.models import CorporateAction, PriceBar
from app.symbols import Market, build_ref

_INTERVAL = "day"


# --- incremental: only fetch since the last stored row (a small overlap re-checks recent dates
#     for corrections / weekend gaps). First-ever pull falls back to the full history window. -----
def _last_bar_date(market: str, ticker: str) -> date | None:
    with SessionLocal() as db:
        return db.scalar(select(func.max(PriceBar.bar_date)).where(
            PriceBar.market == market, PriceBar.ticker == ticker, PriceBar.interval == _INTERVAL))


async def ingest_prices_ticker(market: Market, ticker: str, start: date, end: date, retries: int = 1) -> int:
    ref = build_ref(market, ticker)
    provider = get_prices_provider(market)
    bars = await _retry(lambda: provider.prices(ref, _INTERVAL, start, end), retries)
    rows = []
    for p in bars:
        d = p.model_dump() if hasattr(p, "model_dump") else dict(p)
        bd = _to_date(d.get("time"))
        if bd is None:
            continue
        rows.append({
            "market": market.value, "ticker": ref.ticker, "interval": _INTERVAL, "bar_date": bd,
            "open": _num(d.get("open")), "high": _num(d.get("high")), "low": _num(d.get("low")),
            "close": _num(d.get("close")), "volume": _num(d.get("volume")), "source": "yahoo",
        })

    def _write() -> int:
        with SessionLocal() as db:
            for r in rows:
                existing = db.execute(
                    select(PriceBar).where(
                        PriceBar.market == r["market"], PriceBar.ticker == r["ticker"],
                        PriceBar.interval == r["interval"], PriceBar.bar_date == r["bar_date"],
                    )
                ).scalar_one_or_none()
                if existing:
                    existing.open, existing.high, existing.low = r["open"], r["high"], r["low"]
                    existing.close, existing.volume = r["close"], r["volume"]
                else:
                    db.add(PriceBar(**r))
            db.commit()
        return len(rows)

    return await asyncio.to_thread(_write)


async def run_prices_ingest(market: str, tickers: list[str], years: int = 2, retries: int = 1,
                            overlap_days: int = 5) -> dict:
    """Collect daily OHLCV for ``tickers`` into ``PriceBar``; recorded as an IngestionJob.

    Incremental: each ticker is fetched only from its last stored bar (minus ``overlap_days`` to
    re-check recent corrections); a ticker with no data yet gets the full ``years``-year backfill."""
    init_db()
    mk = Market(market)
    end = date.today()
    full_start = date(end.year - years, end.month, end.day) if end.year - years > 0 else date(end.year - years, 1, 1)

    async def _one(t: str) -> int:
        nt = build_ref(mk, t).ticker
        last = await asyncio.to_thread(_last_bar_date, market, nt)
        start = _incremental_start(last, full_start, overlap_days)
        return await ingest_prices_ticker(mk, t, start, end, retries)

    return await run_ticker_job("prices", market, f"prices · {len(tickers)} tickers (증분)", tickers, _one)


def price_coverage() -> dict:
    """Store coverage for prices + corporate actions (for the admin Data view)."""
    with SessionLocal() as db:
        bars = db.scalar(select(func.count()).select_from(PriceBar)) or 0
        price_tickers = db.scalar(select(func.count(func.distinct(PriceBar.ticker))).select_from(PriceBar)) or 0
        actions = db.scalar(select(func.count()).select_from(CorporateAction)) or 0
        return {"price_bars": bars, "price_tickers": price_tickers, "corporate_actions": actions}
