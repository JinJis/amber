"""Era news via the NYT Archive API (free key; headlines + abstracts by month, back to 1851).

    https://api.nytimes.com/svc/archive/v1/{year}/{month}.json?api-key=…

Covers the pre-2017 regimes (dot-com, GFC) that GDELT can't. The Archive endpoint returns a
WHOLE month of articles; we filter to the requested window + query terms client-side. NYT asks
for ≤5 req/min and ≤500/day, so a small async rate limiter spaces the monthly calls. Key-gated:
with no NYT_API_KEY the resource returns a 501-style honest gap (handled at the router).
"""

from __future__ import annotations

import asyncio
from datetime import date

from app.config import settings
from app.errors import not_implemented
from app.http import fetch_json

_BASE = "https://api.nytimes.com/svc/archive/v1"


class _RateLimiter:
    """Token-spacing limiter: at most `rate` calls per `per` seconds, awaited before each call.
    Async-safe (one lock); process-local (enough for the one-shot ingest pipeline)."""

    def __init__(self, rate: int, per: float) -> None:
        self.min_interval = per / max(1, rate)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, now: float | None = None, sleep=asyncio.sleep) -> None:
        async with self._lock:
            import time
            t = now if now is not None else time.monotonic()
            wait = self._last + self.min_interval - t
            if wait > 0:
                await sleep(wait)
                t += wait
            self._last = t


_LIMITER = _RateLimiter(rate=5, per=60.0)  # NYT guidance: ≤5 req/min


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _matches(doc: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    hay = " ".join(str(doc.get(k, "")) for k in ("abstract", "snippet")).lower()
    head = ((doc.get("headline") or {}).get("main") or "").lower()
    hay += " " + head
    return any(t in hay for t in terms)


class NytArchiveProvider:
    def available(self) -> bool:
        return bool(settings.nyt_api_key)

    async def era_news(self, query: str, start: date, end: date, limit: int = 40,
                       limiter: _RateLimiter | None = None) -> dict:
        if not self.available():
            raise not_implemented("NYT_API_KEY가 설정되지 않아 시대 뉴스(NYT Archive)를 사용할 수 없습니다.")
        lim = limiter or _LIMITER
        terms = [t.lower() for t in (query or "").split() if t]
        out: list[dict] = []
        for (y, m) in _months(start, end):
            await lim.acquire()
            data = await fetch_json("nyt_archive", f"{_BASE}/{y}/{m}.json",
                                    params={"api-key": settings.nyt_api_key})
            docs = ((data.get("response") or {}).get("docs") or []) if isinstance(data, dict) else []
            for d in docs:
                pub = str(d.get("pub_date") or "")[:10]
                if not pub or not (start.isoformat() <= pub <= end.isoformat()):
                    continue
                if not _matches(d, terms):
                    continue
                out.append({
                    "title": (d.get("headline") or {}).get("main"),
                    "abstract": d.get("abstract") or d.get("snippet"),
                    "date": pub, "url": d.get("web_url"),
                    "section": d.get("section_name"),
                })
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        out.sort(key=lambda a: a["date"])
        return {"source": "The New York Times Archive", "query": query, "method": "nyt-archive-v1",
                "from": start.isoformat(), "to": end.isoformat(), "articles": out}
