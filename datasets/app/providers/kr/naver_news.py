"""KR company news via the Naver Search Open API `/v1/search/news` — native, keyed, real-time.

Naver is Korea's dominant news portal, so its search covers KR company news far better than any
Western API or Google News. Free tier ~25,000 calls/day (shared across Naver search categories);
auth is Client ID + Secret in the request headers. We search by the company's Korean name (resolved
from the cached OpenDART corp map, same as the Google News path) sorted newest-first.

Crucially, each item carries ``originallink`` — the REAL publisher article URL — so the in-app
evidence viewer renders the source directly (no Google-News interstitial to decode). The auto news
provider falls back to Google News when the keys are unset or a query returns nothing.
"""

from __future__ import annotations

import html
import re
from email.utils import parsedate_to_datetime

from app.config import settings
from app.http import fetch_json
from app.models.generated import News
from app.symbols import Market

_URL = "https://openapi.naver.com/v1/search/news.json"
_TAG_RE = re.compile(r"<[^>]+>")   # Naver wraps matched terms in <b>…</b>


def configured() -> bool:
    return bool(settings.naver_client_id and settings.naver_client_secret)


def _clean(s: str | None) -> str | None:
    """Strip Naver's <b> highlight tags + unescape HTML entities in title/description."""
    if not s:
        return None
    return html.unescape(_TAG_RE.sub("", s)).strip() or None


def _to_date(pubdate: str | None) -> str | None:
    if not pubdate:
        return None
    try:
        return parsedate_to_datetime(pubdate).strftime("%Y-%m-%d")   # RFC-822
    except (TypeError, ValueError):
        return None


async def _query_for(ticker: str | None) -> str:
    if not ticker:
        return "증시"
    try:
        from app.providers.kr.opendart import _corp_map

        row = (await _corp_map()).get(ticker.zfill(6))
        if row and row.get("corp_name"):
            return row["corp_name"]
    except Exception:  # noqa: BLE001 — no corp map (no key/quota) → search the bare code
        pass
    return f"{ticker} 주가"


class NaverNewsProvider:
    """`/v1/search/news.json?query={회사명}&sort=date&display={limit}` → recent KR articles."""

    async def news(self, market: Market, ticker: str | None, limit: int) -> list[News]:
        if market is not Market.KR or not configured():
            return []
        query = await _query_for(ticker)
        headers = {"X-Naver-Client-Id": settings.naver_client_id,
                   "X-Naver-Client-Secret": settings.naver_client_secret}
        params = {"query": query, "display": min(max(1, limit), 100), "sort": "date"}
        try:
            data = await fetch_json("naver_news", _URL, params=params, headers=headers)
        except Exception:  # noqa: BLE001 — upstream/quota → empty, caller falls back
            return []
        items = data.get("items") if isinstance(data, dict) else None
        if not items:
            return []
        out: list[News] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = _clean(it.get("title"))
            if not title:
                continue
            # originallink = the publisher's own article URL (best for the evidence viewer);
            # naver 'link' (n.news.naver.com) is the fallback.
            url = it.get("originallink") or it.get("link")
            out.append(News(
                ticker=ticker,
                title=title[:300],
                source=_publisher(url) or "네이버뉴스",
                date=_to_date(it.get("pubDate")),
                url=url,
            ))
            if len(out) >= limit:
                break
        return out


def _publisher(url: str | None) -> str | None:
    """A readable publisher label from the article host (naver gives no explicit source name)."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        return (urlparse(url).hostname or "").replace("www.", "") or None
    except ValueError:
        return None
