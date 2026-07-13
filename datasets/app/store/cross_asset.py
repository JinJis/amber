"""CE-1: cross-asset snapshot (자산군) — a market dashboard across asset classes.

Descriptive only (latest level + day change), sourced to Yahoo Finance. Built entirely on the
EXISTING prices provider via well-known proxy tickers (indices/rates/commodities/FX/crypto) — no
new upstream. Each member is fetched best-effort; a ticker that fails is DROPPED (gap drawn,
never fabricated). No forecasts.
"""

from __future__ import annotations

import asyncio
import logging

from app.providers.registry import get_prices_provider
from app.symbols import Market, build_ref

logger = logging.getLogger(__name__)

SOURCE = "Yahoo Finance"

# (group name, [(label, Yahoo proxy ticker)]). US-market tickers (uppercased as-is).
GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("주가지수", [("S&P 500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI"), ("러셀 2000", "^RUT"),
                ("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"), ("닛케이 225", "^N225"), ("항셍", "^HSI"),
                ("유로스톡스 50", "^STOXX50E")]),
    ("금리·채권", [("미 13주", "^IRX"), ("미 5년", "^FVX"), ("미 10년", "^TNX"), ("미 30년", "^TYX")]),
    ("원자재", [("금", "GC=F"), ("WTI 원유", "CL=F"), ("천연가스", "NG=F"), ("구리", "HG=F"), ("은", "SI=F")]),
    ("환율", [("달러인덱스", "DX-Y.NYB"), ("USD/KRW", "KRW=X"), ("EUR/USD", "EURUSD=X"),
            ("USD/JPY", "JPY=X"), ("USD/CNY", "CNY=X")]),
    ("가상자산", [("비트코인", "BTC-USD"), ("이더리움", "ETH-USD")]),
]


async def _one(label: str, ticker: str) -> dict | None:
    try:
        snap = await get_prices_provider(Market.US).snapshot(build_ref(Market.US, ticker))
        if snap is None or snap.price is None:
            return None
        return {"label": label, "ticker": ticker, "price": snap.price,
                "change": snap.day_change, "change_percent": snap.day_change_percent,
                "as_of": (snap.time or "")[:10] or None}
    except Exception as exc:  # noqa: BLE001 — best-effort per member; drop on failure
        logger.info("cross-asset %s (%s) failed: %s", label, ticker, exc)
        return None


async def cross_asset_snapshot() -> dict:
    """Snapshot every group concurrently; drop members that fail (never fabricate)."""
    from app.config import settings

    sem = asyncio.Semaphore(settings.price_fanout_concurrency)  # CR-9: bound the fan-out burst

    async def _bounded(lbl: str, tk: str):
        async with sem:
            return await _one(lbl, tk)

    out: list[dict] = []
    for name, members in GROUPS:
        rows = await asyncio.gather(*[_bounded(lbl, tk) for lbl, tk in members])
        kept = [r for r in rows if r]
        if kept:
            out.append({"name": name, "members": kept})
    asof = max((m["as_of"] for g in out for m in g["members"] if m.get("as_of")), default=None)
    return {"groups": out, "source": SOURCE, "as_of": asof}
