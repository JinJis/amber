"""Earnings-call transcripts (Alpha Vantage) — the spoken record of a quarterly earnings call, with
the analyst Q&A. Free, clean JSON (speaker + paragraph + per-paragraph sentiment) — no PDF parsing,
low copyright risk (transcript of a public call). We index these into RAG alongside filings so the
agent can quote management/analyst remarks WITH provenance + an in-app transcript preview.

US coverage. Needs ``ALPHAVANTAGE_API_KEY`` (a free key works; rate-limited). Without a key every
call returns None → the feature simply stays dark (honesty: never fabricated).
"""

from __future__ import annotations

import datetime
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_URL = "https://www.alphavantage.co/query"
_TIMEOUT = 25.0


def recent_quarters(n: int, today: datetime.date | None = None) -> list[str]:
    """The last ``n`` completed quarter labels (AV format, e.g. '2024Q3'), newest first."""
    d = today or datetime.date.today()
    y, q = d.year, (d.month - 1) // 3 + 1
    q -= 1  # the current quarter isn't reported yet → start from the previous one
    if q == 0:
        q, y = 4, y - 1
    out: list[str] = []
    for _ in range(max(1, n)):
        out.append(f"{y}Q{q}")
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return out


async def fetch_transcript(ticker: str, quarter: str) -> dict | None:
    """One earnings-call transcript for (ticker, quarter), or None if unavailable/no key. Shape:
    ``{ticker, quarter, source, segments: [{speaker, title, content, sentiment}]}``."""
    key = settings.alphavantage_api_key
    if not key:
        return None
    params = {"function": "EARNINGS_CALL_TRANSCRIPT", "symbol": ticker.upper(),
              "quarter": quarter, "apikey": key}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_URL, params=params)
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("transcript fetch failed %s %s: %s", ticker, quarter, exc)
        return None
    rows = data.get("transcript")
    if not isinstance(rows, list) or not rows:
        # AV signals rate-limit / empty via a "Note"/"Information" message rather than 4xx
        if data.get("Note") or data.get("Information"):
            log.info("transcript AV throttled/empty %s %s: %s", ticker, quarter,
                     str(data.get("Note") or data.get("Information"))[:120])
        return None
    segments = [
        {"speaker": s.get("speaker") or "", "title": s.get("title") or "",
         "content": s.get("content") or "", "sentiment": s.get("sentiment")}
        for s in rows if isinstance(s, dict) and s.get("content")
    ]
    if not segments:
        return None
    return {"ticker": ticker.upper(), "quarter": quarter, "source": "Alpha Vantage (earnings call)",
            "segments": segments}


async def recent_transcripts(ticker: str, limit: int = 4) -> list[dict]:
    """Up to ``limit`` recent quarterly transcripts for a ticker (best-effort; skips missing ones)."""
    out: list[dict] = []
    for q in recent_quarters(limit):
        t = await fetch_transcript(ticker, q)
        if t:
            out.append(t)
    return out
