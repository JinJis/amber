"""Render an earnings-call transcript as a sanitized HTML page for the in-app viewer.

Transcripts arrive as JSON (speaker + paragraph). We render our OWN clean HTML (so there's nothing
untrusted to strip) under the same strict CSP the filing viewer uses, and cache it by (ticker,
quarter). It is served through the EXISTING /evidence/html route via a synthetic accession
``TR:{ticker}:{quarter}`` — so the whole evidence chain (viewer · highlight · BFF · gateway) is
reused unchanged. The viewer highlights a cited passage by text search over the <p> bodies.
"""

from __future__ import annotations

import asyncio
import html
import logging
import pathlib

from app.config import settings
from app.providers.transcripts import fetch_transcript

log = logging.getLogger(__name__)

_CSP = ('<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src data:">')
_STYLE = ("<style>body{font:15px/1.7 -apple-system,system-ui,sans-serif;color:#1a1a1a;max-width:820px;"
          "margin:0 auto;padding:24px}h2{font-size:20px;margin:0 0 4px}.src{color:#888;font-size:12px;"
          "margin-bottom:20px}section{margin:0 0 18px;padding:0 0 4px;border-bottom:1px solid #eee}"
          ".spk{font-weight:600;color:#0b4a8f;margin-bottom:4px}.spk .ttl{color:#888;font-weight:400}"
          "p{margin:0}</style>")


def parse_accession(accession: str) -> tuple[str, str] | None:
    """`TR:AAPL:2024Q3` → ('AAPL', '2024Q3'); None if not a transcript accession."""
    if not accession or not accession.startswith("TR:"):
        return None
    parts = accession.split(":")
    return (parts[1], parts[2]) if len(parts) >= 3 else None


def make_accession(ticker: str, quarter: str) -> str:
    return f"TR:{ticker.upper()}:{quarter}"


def render(transcript: dict) -> str:
    """Build the readable, sanitized HTML page from a transcript dict (provider shape)."""
    tk, q = transcript.get("ticker", ""), transcript.get("quarter", "")
    segs = transcript.get("segments") or []
    body = []
    for s in segs:
        spk = html.escape(str(s.get("speaker") or "발언자"))
        ttl = s.get("title")
        head = spk + (f' <span class="ttl">· {html.escape(str(ttl))}</span>' if ttl else "")
        body.append(f'<section><div class="spk">{head}</div><p>{html.escape(str(s.get("content") or ""))}</p></section>')
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">{_CSP}'
            f'<title>{html.escape(tk)} {html.escape(q)} earnings call</title>{_STYLE}</head><body>'
            f'<h2>{html.escape(tk)} · {html.escape(q)} 어닝콜 트랜스크립트</h2>'
            f'<div class="src">{html.escape(str(transcript.get("source") or "earnings call"))}</div>'
            f'{"".join(body)}</body></html>')


def _cache_path(ticker: str, quarter: str) -> pathlib.Path:
    return pathlib.Path(settings.evidence_docs_dir) / "transcript" / f"{ticker.upper()}_{quarter}.html"


async def get_transcript_html(ticker: str, quarter: str) -> str | None:
    """Cache-first sanitized transcript HTML (fetches + renders + caches on a miss). None if the
    transcript isn't available (no key / not found) — the viewer then degrades gracefully."""
    path = _cache_path(ticker, quarter)
    if path.exists():
        return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
    t = await fetch_transcript(ticker, quarter)
    if not t:
        return None
    return await store_transcript_html(t)


async def store_transcript_html(transcript: dict) -> str:
    """Render + cache a transcript's HTML (called during ingest so the preview is warm). Returns it."""
    path = _cache_path(transcript["ticker"], transcript["quarter"])
    out = render(transcript)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, out, encoding="utf-8")
    return out
