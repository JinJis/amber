"""US company news via Finnhub `/company-news` — real-time, keyed, production-grade.

Finnhub is purpose-built for programmatic access (free tier 60 req/min, explicit rate limits,
real article URLs + publisher names), so it scales without the datacenter-IP soft-blocking that
keyless Google News RSS hits. US only; the auto news provider falls back to Google News when the
key is unset or a symbol returns nothing. Each article carries the REAL publisher URL, which the
in-app evidence viewer renders directly (no Google-News interstitial decode).
"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import settings
from app.http import fetch_json
from app.models.generated import News
from app.symbols import Market

_URL = "https://finnhub.io/api/v1/company-news"
_LOOKBACK_DAYS = 14   # the recent window company-news is queried over (from → to)


def configured() -> bool:
    return bool(settings.finnhub_api_key)


class FinnhubNewsProvider:
    """`/company-news?symbol=AAPL&from=…&to=…&token=…` → recent articles, newest first."""

    async def news(self, market: Market, ticker: str | None, limit: int) -> list[News]:
        # Finnhub company-news is US-symbol + ticker-scoped; no ticker / non-US → let the caller
        # fall back (broad-market news isn't this endpoint's job).
        if market is not Market.US or not ticker or not settings.finnhub_api_key:
            return []
        today = date.today()
        params = {"symbol": ticker.upper(),
                  "from": (today - timedelta(days=_LOOKBACK_DAYS)).isoformat(),
                  "to": today.isoformat(),
                  "token": settings.finnhub_api_key}
        try:
            data = await fetch_json("finnhub", _URL, params=params)
        except Exception:  # noqa: BLE001 — upstream/rate-limit → empty, caller falls back
            return []
        if not isinstance(data, list):
            return []
        # newest first (Finnhub returns by datetime; sort defensively)
        rows = sorted((a for a in data if isinstance(a, dict) and a.get("headline") and a.get("url")),
                      key=lambda a: a.get("datetime") or 0, reverse=True)
        out: list[News] = []
        for a in rows[:limit]:
            out.append(News(
                ticker=ticker.upper(),
                title=str(a.get("headline"))[:300],
                source=a.get("source") or "Finnhub",
                date=_epoch_to_date(a.get("datetime")),
                url=a.get("url"),
            ))
        return out


def _epoch_to_date(epoch) -> str | None:
    try:
        return date.fromtimestamp(int(epoch)).isoformat() if epoch else None
    except (TypeError, ValueError, OSError):
        return None
