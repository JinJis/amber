"""Serve ANY public data-source page as sanitized, same-origin HTML for the in-app viewer.

The filing viewer (``filing_html``) proved the pattern: fetch the real source markup, sanitize it
(strip scripts + strict CSP → no egress), and serve it so the browser renders the *real* document and
the viewer highlights the cited figure/passage in the DOM. This generalizes it to the OTHER sources
whose evidence is a static HTML page with the value inline — e.g. the BLS timeseries page behind a
macro series (``data.bls.gov/timeseries/…``), a DBnomics series page, a news article.

SSRF-safe: only ``http(s)`` URLs whose host resolves to a PUBLIC IP are fetched (private / loopback /
link-local / reserved / cloud-metadata addresses are refused), redirects are followed manually and
re-validated each hop, and the response must be HTML under a size cap. The URL comes from our own
tool results, but we never trust the network target without these checks.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import pathlib
import re
import socket
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.config import settings
from app.store.filing_html import sanitize

log = logging.getLogger(__name__)

_UA = {"User-Agent": "ValueGraph/1.0 (research desk; +https://valuegraph.example) contact@example.com"}
_MAX_BYTES = 12_000_000          # ~12 MB cap (filings run up to ~9 MB; refuse anything larger)
_MAX_REDIRECTS = 4
_TIMEOUT = 15.0
# also strip any CSP / X-Frame meta the source page carried, so only our injected CSP applies.
_META_POLICY_RE = re.compile(
    r'(?is)<meta[^>]+http-equiv\s*=\s*["\']?(?:content-security-policy|x-frame-options)["\'][^>]*>')


def _is_public_host(host: str) -> bool:
    """True only if every address the host resolves to is a routable, public IP (SSRF guard)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _strip_fragment(url: str) -> str:
    """Drop a `#…` fragment (e.g. a news citation's `#:~:text=` highlight anchor) — it's client-side
    only, irrelevant to the server fetch, and keeps the cache key stable across anchors."""
    p = urlparse(url)
    return urlunparse(p._replace(fragment="")) if p.fragment else url


def _safe(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname) and _is_public_host(p.hostname)


async def _fetch(url: str) -> str | None:
    """SSRF-safe GET that follows redirects manually (re-validating each hop) and returns the HTML
    body, or None if anything is unsafe / not HTML / too large / errored."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            if not _safe(url):
                log.info("source html refused (non-public/non-http) %s", url[:120])
                return None
            try:
                r = await client.get(url, headers=_UA)
            except httpx.HTTPError as exc:
                log.info("source html fetch failed %s: %s", url[:120], exc)
                return None
            if r.is_redirect:
                loc = r.headers.get("location")
                if not loc:
                    return None
                url = urljoin(url, loc)
                continue
            if r.status_code != 200:
                return None
            if "html" not in (r.headers.get("content-type") or "").lower():
                return None
            if len(r.content) > _MAX_BYTES:
                log.info("source html too large (%d bytes) %s", len(r.content), url[:120])
                return None
            return r.text
    return None


def _cache_path(url: str) -> pathlib.Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return pathlib.Path(settings.evidence_docs_dir) / "source" / f"{key}.html"


async def get_source_html(url: str) -> str | None:
    """Cache-first sanitized HTML for an arbitrary public source page (or None → UI uses the link)."""
    url = _strip_fragment(url)
    if not _safe(url):
        return None
    path = _cache_path(url)
    if path.exists():
        return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
    raw = await _fetch(url)
    if not raw or not raw.strip():
        return None
    clean = sanitize(_META_POLICY_RE.sub("", raw))   # drop the source's CSP/X-Frame meta, then sanitize
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, clean, encoding="utf-8")
    log.info("source html stored (%d KB) %s → %s", len(clean) // 1024, url[:80], path)
    return clean
