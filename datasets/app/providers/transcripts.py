"""Earnings-call transcripts — the spoken record of a quarterly call, with the analyst Q&A.

Source: **API Ninjas** (`API_NINJAS_KEY`, premium, REQUIRED) — full transcripts by
(ticker, year, quarter), US **and KR** (e.g. ``005930.KS`` — Samsung's calls are held in English),
~5y depth on the Developer tier. The raw text is newline-separated ``Speaker: content`` turns,
which we parse into the same segment shape the RAG ingester and the in-app HTML preview consume.

Without `API_NINJAS_KEY` the feature stays dark — every call returns None (never fabricated).
(The old Alpha Vantage US free fallback was removed 2026-07-12: at 25 calls/day it covered almost
nothing yet added a wasted round-trip per API-Ninjas miss.)
"""

from __future__ import annotations

import datetime
import logging
import re

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_NINJAS_URL = "https://api.api-ninjas.com/v1/earningstranscript"
_TIMEOUT = 25.0

# "Tim Cook: …" turn starts at line starts. Speaker names are short human/role labels; the cap
# keeps a stray "Note:" inside prose from being misread as a speaker mid-paragraph.
_TURN_RE = re.compile(r"^([A-Z][A-Za-z.\-'’ ]{1,60}):\s", re.M)


def recent_quarters(n: int, today: datetime.date | None = None) -> list[str]:
    """The last ``n`` completed quarter labels ('2024Q3'), newest first. API Ninjas quarters are
    the company's FISCAL quarters, so walking a couple extra calendar quarters back (callers pass
    a slack window) still sweeps every call."""
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


def parse_turns(text: str) -> list[dict]:
    """Split a raw ``Speaker: content`` transcript into segments (same shape AV used). A text
    with no recognizable turns becomes one speaker-less segment — never dropped."""
    text = (text or "").strip()
    if not text:
        return []
    parts = _TURN_RE.split(text)
    # parts = [preamble, speaker1, content1, speaker2, content2, …]
    segments: list[dict] = []
    if parts[0].strip():
        segments.append({"speaker": "", "title": "", "content": parts[0].strip(), "sentiment": None})
    for i in range(1, len(parts) - 1, 2):
        content = parts[i + 1].strip()
        if content:
            segments.append({"speaker": parts[i].strip(), "title": "", "content": content, "sentiment": None})
    return segments


async def _fetch_ninjas(ticker: str, quarter: str) -> dict | None:
    """API Ninjas: (ticker, '2024Q2') → transcript dict or None. KR codes ride as e.g. 005930.KS."""
    key = settings.api_ninjas_key
    if not key:
        return None
    m = re.fullmatch(r"(\d{4})Q([1-4])", quarter or "")
    if not m:
        return None
    params = {"ticker": ticker.upper(), "year": m.group(1), "quarter": m.group(2)}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(_NINJAS_URL, params=params, headers={"X-Api-Key": key})
        if r.status_code != 200:
            if r.status_code != 404:
                log.info("transcript ninjas %s %s: HTTP %s %s", ticker, quarter, r.status_code, r.text[:120])
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("transcript ninjas fetch failed %s %s: %s", ticker, quarter, exc)
        return None
    if not isinstance(data, dict) or not data.get("transcript"):
        return None
    segments = parse_turns(str(data["transcript"]))
    if not segments:
        return None
    return {"ticker": ticker.upper(), "quarter": quarter,
            "as_of": data.get("date"),   # the actual call date (better than a quarter label)
            "source": "API Ninjas (earnings call)", "segments": segments}




def has_transcript_key() -> bool:
    """Transcripts require API Ninjas (the only source). No key → the feature stays dark."""
    return bool(settings.api_ninjas_key)


def transcripts_cover_kr() -> bool:
    """KR earnings calls are served by API Ninjas (same key covers US + KR)."""
    return bool(settings.api_ninjas_key)


async def fetch_transcript(ticker: str, quarter: str) -> dict | None:
    """One earnings-call transcript for (ticker, '2024Q2'), or None — API Ninjas only. Shape:
    ``{ticker, quarter, as_of?, source, segments: [{speaker, title, content, sentiment}]}``."""
    return await _fetch_ninjas(ticker, quarter)


async def recent_transcripts(ticker: str, limit: int = 4) -> list[dict]:
    """Up to ``limit`` recent quarterly transcripts (best-effort). Walks a slack window of
    quarters (fiscal quarters can straddle calendar labels) and stops once ``limit`` are found."""
    out: list[dict] = []
    for q in recent_quarters(max(1, limit) + 3):
        if len(out) >= limit:
            break
        t = await fetch_transcript(ticker, q)
        if t:
            out.append(t)
    return out
