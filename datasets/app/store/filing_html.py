"""Serve the ORIGINAL filing as sanitized, self-contained HTML for the in-app viewer.

We already fetch the source markup (US: the SEC iXBRL primary document; KR: the OpenDART
``document.xml`` ZIP). Instead of rendering it to a PDF and screenshotting a crop, we sanitize
that HTML once and serve it as-is — the browser renders the *real* document (perfect quality,
scrollable, zoomable) and the viewer highlights the cited element in the DOM (US: the inline-XBRL
tag for the concept; text: the cited passage). No Chromium, no PyMuPDF.

Sanitize keeps the document structure intact (including the inline-XBRL `<ix:…>` tags the viewer
targets) and makes it safe to drop into a sandboxed iframe: scripts are stripped and a strict CSP
blocks every external load (no egress) — only inline styles and data: images render.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import re

from app.config import settings
from app.http import fetch_text
from app.providers.kr.dart_document import fetch_document_markup
from app.providers.us.sec_edgar import _UA

log = logging.getLogger(__name__)

HTML_VERSION = "1"

_SCRIPT_RE = re.compile(r"(?is)<script\b.*?</script>")
_BASE_RE = re.compile(r"(?i)<base\b[^>]*>")
_HEAD_RE = re.compile(r"(?i)<head[^>]*>")
_HTML_RE = re.compile(r"(?i)<html[^>]*>")
# default-src 'none' blocks every external fetch (scripts, fonts, frames, css, remote images) →
# no client egress; inline styles + data: images still render so the filing looks right.
_CSP = ('<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:\">")


def sanitize(markup: str) -> str:
    """Strip active/egress content but keep the document (and its inline-XBRL tags) intact."""
    h = _SCRIPT_RE.sub("", markup)
    h = _BASE_RE.sub("", h)
    if _HEAD_RE.search(h):
        h = _HEAD_RE.sub(lambda m: m.group(0) + _CSP, h, count=1)
    elif _HTML_RE.search(h):
        h = _HTML_RE.sub(lambda m: m.group(0) + "<head>" + _CSP + "</head>", h, count=1)
    else:
        h = "<head>" + _CSP + "</head>" + h
    return h


def _html_path(market: str, accession: str) -> pathlib.Path:
    safe = accession.replace("/", "_")
    return pathlib.Path(settings.evidence_docs_dir) / "html" / market.upper() / f"{safe}.html"


async def _source_html(market: str, accession: str, cik: str | None,
                       fetch_url: str | None = None) -> str | None:
    """Fetch the raw filing markup per market (KR: document.xml ZIP; US: iXBRL primary doc).
    `fetch_url` (US) skips the submissions lookup; else it's resolved from the CIK."""
    if market == "KR":
        return await fetch_document_markup(accession)
    url = fetch_url
    if not url and cik:
        from app.store.filing_refs import _primary_doc_map  # local import avoids an import cycle

        url = (await _primary_doc_map(cik.zfill(10))).get(accession)
    if not url:
        return None
    try:
        return await fetch_text("sec_edgar", url, headers=_UA)
    except Exception:  # noqa: BLE001 — upstream/network → graceful (None), UI falls back to the link
        return None


async def get_filing_html(market: str, accession: str, cik: str | None = None,
                          fetch_url: str | None = None) -> str | None:
    """Cache-first sanitized filing HTML for the in-app viewer (or None → UI uses the source link)."""
    market = market.upper()
    path = _html_path(market, accession)
    if path.exists():
        return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
    raw = await _source_html(market, accession, cik, fetch_url)
    if not raw or not raw.strip():
        log.info("filing html skipped (no source markup) %s %s", market, accession)
        return None
    clean = sanitize(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, clean, encoding="utf-8")
    log.info("filing html stored %s %s (%d KB) → %s", market, accession, len(clean) // 1024, path)
    return clean
