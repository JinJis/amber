"""Keyless EOD prices via the Yahoo Finance chart API.

Works for both markets — US symbols are used as-is (``AAPL``); KR symbols are
tried with the ``.KS`` (KOSPI) then ``.KQ`` (KOSDAQ) suffix. Prices are delayed,
consistent with the default licensing posture.

    https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.cache import cache
from app.config import settings
from app.errors import not_found
from app.http import fetch_json
from app.models.generated import Price, PriceSnapshot
from app.symbols import Market, SecurityRef

_UA = {"User-Agent": "Mozilla/5.0 (compatible; ValueGraphDatasets/0.1)"}
_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
_INTERVALS = {"day": "1d", "week": "1wk", "month": "1mo", "year": "1mo"}


def _symbols(ref: SecurityRef) -> list[str]:
    if ref.market is Market.KR:
        return [f"{ref.ticker}.KS", f"{ref.ticker}.KQ"]
    return [ref.ticker.upper()]


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


async def _chart(symbol: str, params: dict) -> dict | None:
    # CR-9: short shared TTL cache with per-key single-flight — N concurrent users on one ticker
    # collapse to ONE Yahoo call, and a .KS/.KQ miss is cached so it isn't re-fetched each request.
    key = "yahoo:chart:" + symbol + ":" + "&".join(f"{k}={params[k]}" for k in sorted(params))

    async def _load() -> dict | None:
        data = await fetch_json("yahoo", f"{_BASE}/{symbol}", params=params, headers=_UA)
        results = (data.get("chart") or {}).get("result")  # type: ignore[union-attr]
        return results[0] if results else None

    return await cache.get_or_set(key, _load, ttl_seconds=settings.yahoo_price_cache_ttl_seconds)


def _d(ts) -> str | None:
    """Yahoo event epoch → ISO date."""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


class YahooProvider:
    async def prices(self, ref: SecurityRef, interval: str, start: date, end: date) -> list[Price]:
        iv = _INTERVALS.get(interval, "1d")
        params = {"period1": _epoch(start), "period2": _epoch(end) + 86400, "interval": iv}
        result = None
        for symbol in _symbols(ref):
            result = await _chart(symbol, params)
            if result and result.get("timestamp"):
                break
        if not result or not result.get("timestamp"):
            raise not_found(f"No Yahoo price data for '{ref.ticker}'.")
        ts = result["timestamp"]
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        out: list[Price] = []
        for i, t in enumerate(ts):
            close = _at(q.get("close"), i)
            if close is None:
                continue
            vol = _at(q.get("volume"), i)
            out.append(
                Price(
                    time=datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"),
                    open=_at(q.get("open"), i),
                    high=_at(q.get("high"), i),
                    low=_at(q.get("low"), i),
                    close=close,
                    volume=int(vol) if vol is not None else None,
                )
            )
        if not out:
            raise not_found(f"No Yahoo price data for '{ref.ticker}'.")
        return out

    async def corporate_actions(self, ref: SecurityRef, start: date, end: date) -> dict:
        """Dividends + splits over a range, from the Yahoo chart `events` feed (both markets).
        Returns plain dicts (no source document → data-card evidence: values + Yahoo + as_of)."""
        params = {"period1": _epoch(start), "period2": _epoch(end) + 86400,
                  "interval": "1d", "events": "div,split"}
        divs: list[dict] = []
        splits: list[dict] = []
        currency: str | None = None
        for symbol in _symbols(ref):
            result = await _chart(symbol, params)
            if not result:
                continue
            currency = (result.get("meta") or {}).get("currency") or currency
            events = result.get("events") or {}
            for ev in (events.get("dividends") or {}).values():
                d, amt = _d(ev.get("date")), ev.get("amount")
                if d and amt is not None:
                    divs.append({"ex_date": d, "amount": float(amt)})
            for ev in (events.get("splits") or {}).values():
                d = _d(ev.get("date"))
                if not d:
                    continue
                num, den = ev.get("numerator"), ev.get("denominator")
                splits.append({"date": d, "numerator": num, "denominator": den,
                               "ratio": ev.get("splitRatio") or (f"{num:g}:{den:g}" if num and den else None)})
            if divs or splits:
                break  # first suffix (.KS/.KQ or US) that returned actions
        divs.sort(key=lambda x: x["ex_date"], reverse=True)
        splits.sort(key=lambda x: x["date"], reverse=True)
        return {"currency": currency, "dividends": divs, "splits": splits}

    async def snapshot(self, ref: SecurityRef) -> PriceSnapshot:
        params = {"range": "5d", "interval": "1d"}
        result = None
        for symbol in _symbols(ref):
            result = await _chart(symbol, params)
            if result:
                break
        if not result:
            raise not_found(f"No Yahoo snapshot for '{ref.ticker}'.")
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        closes = [c for c in ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
        if price is None and closes:
            price = closes[-1]
        if price is None:
            raise not_found(f"No Yahoo snapshot for '{ref.ticker}'.")
        snap = PriceSnapshot(ticker=ref.ticker, price=price)
        mt = meta.get("regularMarketTime")
        if mt:
            snap.time = datetime.fromtimestamp(mt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            snap.time_milliseconds = mt * 1000
        prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
        if prev:
            snap.day_change = round(price - prev, 4)
            snap.day_change_percent = round((price - prev) / prev * 100, 4)
        return snap


def _at(arr, i):
    if arr is None or i >= len(arr):
        return None
    return arr[i]
