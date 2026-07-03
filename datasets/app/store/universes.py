"""Dynamic universe sources (PH-PIPE).

Resolve a backfill/scheduler universe by FETCHING constituents from upstream at run
time — NO hardcoded ticker lists:

  · us_sp500     — S&P 500 constituents (datahub maintained CSV)
  · us_all       — the full US universe (SEC company_tickers.json)
  · kr_kospi200  — top 200 KOSPI by market cap (pykrx)
  · kr_kosdaq150 — top 150 KOSDAQ by market cap (pykrx)
  · kr_kospi_all / kr_kosdaq_all — every listed name on that board (pykrx)

Results are cached with a TTL so a sweep doesn't re-fetch each tick. On fetch failure
we serve a stale cache if we have one, else an empty list — we never fabricate tickers
(honesty over fake data). ``resolve_universe`` also still accepts the legacy explicit
form ``"US:AAPL,MSFT;KR:005930"`` (no fetch) for ad-hoc backfills.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time

import httpx

from app.config import settings
from app.symbols import Market

logger = logging.getLogger(__name__)

_TTL_SECONDS = 6 * 3600
_CACHE: dict[str, tuple[float, list[str]]] = {}  # id -> (expires_at_monotonic, tickers)

_SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"


# --- fetchers ------------------------------------------------------------
async def _fetch_sp500() -> list[str]:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
        r = await c.get(_SP500_CSV, headers={"User-Agent": settings.sec_edgar_user_agent})
        r.raise_for_status()
    out: list[str] = []
    for row in csv.DictReader(io.StringIO(r.text)):
        sym = (row.get("Symbol") or row.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym.replace(".", "-"))  # BRK.B → BRK-B (Yahoo/SEC style)
    return out


async def _fetch_us_all() -> list[str]:
    from app.providers.us.sec_edgar import _ticker_index
    idx = await _ticker_index()
    return sorted({(r.get("ticker") or "").upper() for r in idx.values() if r.get("ticker")})


def _kr_by_cap_sync(market: str, n: int | None) -> list[str]:
    """pykrx top-N by market cap. Whole body guarded — pykrx scrapes KRX/Naver and often fails
    on cloud IPs (like FRED), so it must NEVER raise (→ empty, then we fall back to OpenDART).

    pykrx prints its own failures straight to stdout/stderr ("KRX 로그인 실패", "Error occurred
    in …") on every miss. We redirect its stdout/stderr into a throwaway buffer for the duration
    of the call so that noise stays out of our logs — our logging handler holds the real stdout
    captured at setup, so it's unaffected. (Its broken root-logger call is dropped separately by
    the _DropNoise filter in logging_config.)"""
    import contextlib
    import io

    try:
        from pykrx import stock
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            d = stock.get_nearest_business_day_in_a_week()
            try:
                cap = stock.get_market_cap_by_ticker(d, market=market)
                if cap is not None and not cap.empty:
                    col = "시가총액" if "시가총액" in cap.columns else cap.columns[0]
                    ranked = cap.sort_values(col, ascending=False)
                    idx = ranked.index.tolist() if n is None else ranked.head(n).index.tolist()
                    return [str(t) for t in idx]
            except Exception as exc:  # noqa: BLE001
                logger.warning("pykrx market-cap list failed (%s); trying ticker list", exc)
            lst = stock.get_market_ticker_list(d, market=market) or []
            return [str(t) for t in lst][: (n or len(lst))]
    except Exception as exc:  # noqa: BLE001 — import/network/etc. → empty → OpenDART fallback
        logger.warning("pykrx KR universe unavailable for %s: %s", market, exc)
        return []


async def _kr_opendart_tickers(n: int | None) -> list[str]:
    """Reliable KR fallback via OpenDART (keyed): all listed 6-digit stock codes. No board split
    or cap ranking (corpCode.xml lacks them), but it WORKS where pykrx is blocked."""
    try:
        from app.providers.kr.opendart import _corp_map
        cmap = await _corp_map()
        codes = sorted(c for c in cmap.keys() if c and c.isdigit())
        return codes[:n] if n else codes
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenDART KR fallback failed: %s", exc)
        return []


async def _fetch_kr(market: str, n: int | None):
    tickers = await asyncio.to_thread(_kr_by_cap_sync, market, n)
    if tickers:
        return tickers
    # pykrx blocked/empty (common on cloud) → OpenDART list so KR backfill still runs
    logger.info("KR universe: pykrx empty for %s, using OpenDART corp list", market)
    return await _kr_opendart_tickers(n)


# id -> {label, market, approx, fetch}
SOURCES: dict[str, dict] = {
    "us_sp500": {"label": "S&P 500 (동적)", "market": "US", "approx": 503, "fetch": _fetch_sp500},
    "us_all": {"label": "US 전체 (SEC, 수천)", "market": "US", "approx": 10000, "fetch": _fetch_us_all},
    "kr_kospi200": {"label": "코스피 200 (동적·시총)", "market": "KR", "approx": 200,
                    "fetch": lambda: _fetch_kr("KOSPI", 200)},
    "kr_kosdaq150": {"label": "코스닥 150 (동적·시총)", "market": "KR", "approx": 150,
                     "fetch": lambda: _fetch_kr("KOSDAQ", 150)},
    "kr_kospi_all": {"label": "코스피 전체 (동적)", "market": "KR", "approx": 800,
                     "fetch": lambda: _fetch_kr("KOSPI", None)},
    "kr_kosdaq_all": {"label": "코스닥 전체 (동적)", "market": "KR", "approx": 1600,
                      "fetch": lambda: _fetch_kr("KOSDAQ", None)},
    "kr_listed": {"label": "KR 상장 전체 (OpenDART, 안정적)", "market": "KR", "approx": 2800,
                  "fetch": lambda: _kr_opendart_tickers(None)},
}


async def fetch_source(source_id: str) -> list[str]:
    """Fetch a source's tickers, cached (TTL). On failure: stale cache if any, else []."""
    src = SOURCES.get(source_id)
    if not src:
        return []
    now = time.monotonic()
    hit = _CACHE.get(source_id)
    if hit and hit[0] > now:
        return hit[1]
    try:
        tickers = await src["fetch"]()
    except Exception as exc:  # noqa: BLE001 — never fabricate; degrade to stale/empty
        logger.warning("universe source %s fetch failed: %s", source_id, exc)
        return hit[1] if hit else []
    if tickers:
        _CACHE[source_id] = (now + _TTL_SECONDS, tickers)
    return tickers


def list_presets() -> list[dict]:
    """Universe sources for the admin dropdown (id/label/market + approx count)."""
    return [{"id": k, "label": v["label"], "market": v["market"], "count": v["approx"]} for k, v in SOURCES.items()]


async def resolve_universe(spec: str) -> list[tuple[Market, list[str]]]:
    """Resolve a spec into [(Market, tickers), …], FETCHING dynamic sources by id.

    Accepts (a) comma-separated source ids, e.g. ``"us_sp500,kr_kospi200"``; and/or
    (b) the legacy explicit form ``"US:AAPL,MSFT;KR:005930"`` (no fetch). Both may be
    mixed, separated by ``;``. Tickers for the same market are merged + de-duplicated.
    """
    by_market: dict[Market, list[str]] = {}

    def _add(mkt: Market, syms: list[str]) -> None:
        cur = by_market.setdefault(mkt, [])
        for s in syms:
            if s and s not in cur:
                cur.append(s)

    for group in (spec or "").split(";"):
        group = group.strip()
        if not group:
            continue
        if ":" in group:  # legacy explicit "US:AAPL,MSFT"
            mkt, tickers = group.split(":", 1)
            try:
                m = Market(mkt.strip().upper())
            except ValueError:
                continue
            _add(m, [t.strip() for t in tickers.split(",") if t.strip()])
        else:  # comma-separated dynamic source ids
            for sid in group.split(","):
                src = SOURCES.get(sid.strip())
                if src:
                    _add(Market(src["market"]), await fetch_source(sid.strip()))
    return [(m, t) for m, t in by_market.items() if t]


async def resolve_one(preset_or_spec: str) -> tuple[str | None, list[str]]:
    """Convenience for single-market callers (legacy backfill/evidence): the first group."""
    groups = await resolve_universe(preset_or_spec)
    if not groups:
        return None, []
    market, tickers = groups[0]
    return market.value, tickers
