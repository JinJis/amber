"""Company / market news via the Google News RSS feed (keyless, both markets).

    https://news.google.com/rss/search?q=<query>&hl=..&gl=..&ceid=..

For a KR ticker we query by the company's Korean name (resolved from the cached
OpenDART corp map when a key is available, else the bare code). With no ticker we
return broad market news.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from app.cache import TTLCache
from app.http import fetch_text
from app.models.generated import News
from app.symbols import Market

_UA = {"User-Agent": "Mozilla/5.0 (compatible; ValueGraphDatasets/0.1)"}
_LOCALE = {
    Market.US: "hl=en-US&gl=US&ceid=US:en",
    Market.KR: "hl=ko&gl=KR&ceid=KR:ko",
}


async def _query_for(market: Market, ticker: str | None) -> str:
    if not ticker:
        return "stock market" if market is Market.US else "증시"
    if market is Market.KR:
        try:
            from app.providers.kr.opendart import _corp_map

            row = (await _corp_map()).get(ticker.zfill(6))
            if row and row.get("corp_name"):
                return row["corp_name"]
        except Exception:
            pass
        return f"{ticker} 주가"
    return f"{ticker} stock"


def _to_date(pubdate: str | None) -> str | None:
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


class GoogleNewsProvider:
    async def news(self, market: Market, ticker: str | None, limit: int) -> list[News]:
        query = await _query_for(market, ticker)
        locale = _LOCALE.get(market, _LOCALE[Market.US])
        url = f"https://news.google.com/rss/search?q={query}&{locale}"
        text = await fetch_text("google_news", url, headers=_UA)
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            return []
        from app.store.gnews_resolve import decode_local

        out: list[News] = []
        for item in root.iter("item"):
            title = item.findtext("title")
            source_el = item.find("source")
            # The RSS <link> is a news.google.com interstitial (client-side JS redirect), which
            # the in-app evidence viewer can never render. When the article id still base64-embeds
            # the publisher URL, decode it locally (zero network cost) so the citation carries the
            # REAL article; opaque ids keep the google link (resolved lazily at evidence-view time).
            link = item.findtext("link")
            url = (decode_local(link) if link else None) or link
            out.append(
                News(
                    ticker=ticker.upper() if ticker and market is Market.US else ticker,
                    title=title,
                    source=source_el.text if source_el is not None else None,
                    date=_to_date(item.findtext("pubDate")),
                    url=url,
                )
            )
            if len(out) >= limit:
                break
        return out


# Short dedup cache (NOT staleness): company news 2-3 min old is still "latest", but a 2-min TTL
# collapses the concurrent per-user storm (N users asking about the same ticker in the same window)
# into ONE upstream call — the production scale fix that preserves freshness.
_NEWS_CACHE_TTL = 120.0


def _dumps_news(v: object) -> str:
    import json
    return json.dumps([n.model_dump(mode="json") for n in v])  # type: ignore[union-attr]


def _loads_news(s: str) -> object:
    import json
    return [News.model_validate(d) for d in json.loads(s)]


# SC-2.4: a dedicated Redis-namespaced cache (not the shared heterogeneous `cache`) so the 2-min
# dedup window is shared across replicas when REDIS_URL is set — otherwise each replica makes its own
# upstream call per window. News are pydantic models → explicit model_dump/model_validate serde.
_news_cache = TTLCache(int(_NEWS_CACHE_TTL), redis_ns="news", dumps=_dumps_news, loads=_loads_news)


class AutoNewsProvider:
    """Market-routed real-time news with a keyless fallback (mirrors the prices auto chain):
      KR → Naver Search API (native, best KR coverage) · US → Finnhub (real-time, keyed)
      → Google News RSS fallback when the keyed source is unset OR returns nothing.
    Google News (keyless, no SLA, IP-rate-limited) stops being the load-bearing production source
    and becomes the safety net. A 2-min dedup cache keeps freshness while absorbing user scale."""

    async def news(self, market: Market, ticker: str | None, limit: int) -> list[News]:
        key = f"news:{getattr(market, 'value', market)}:{ticker or '_'}:{limit}"
        return await _news_cache.get_or_set(
            key, lambda: self._fetch(market, ticker, limit), ttl_seconds=_NEWS_CACHE_TTL)

    async def _fetch(self, market: Market, ticker: str | None, limit: int) -> list[News]:
        primary = _primary_provider(market)
        if primary is not None:
            try:
                out = await primary.news(market, ticker, limit)
            except Exception:  # noqa: BLE001 — a keyed-source failure never sinks the request
                out = []
            if out:
                return out
        return await GoogleNewsProvider().news(market, ticker, limit)


def _primary_provider(market: Market):
    """The keyed real-time source for a market, or None (→ Google News fallback)."""
    if market is Market.US:
        from app.providers.us.finnhub_news import FinnhubNewsProvider, configured
        return FinnhubNewsProvider() if configured() else None
    if market is Market.KR:
        from app.providers.kr.naver_news import NaverNewsProvider, configured
        return NaverNewsProvider() if configured() else None
    return None
