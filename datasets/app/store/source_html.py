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
    # "v3:" — a server-side visible-text floor was added; the v2 cache holds useless JS shells
    # (e.g. Google News interstitials: 336 KB, 11 visible chars) stored before the floor existed.
    # New key → those entries are naturally abandoned and real pages regenerate.
    key = hashlib.sha256(("v3:" + url).encode("utf-8")).hexdigest()[:32]
    return pathlib.Path(settings.evidence_docs_dir) / "source" / f"{key}.html"


_TAG_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>")
_MIN_VISIBLE_CHARS = 120   # below this the sanitized page is a script-shell/consent wall — a gap


def _visible_chars(markup: str) -> int:
    """Visible text length after tags go — mirrors (and pre-empts) the FE's empty-shell check."""
    import html as _html

    return len(re.sub(r"\s+", " ", _html.unescape(_TAG_RE.sub(" ", markup))).strip())


# DART's public viewer (dsaf001/main.do?rcpNo=…) is a script-driven shell: after sanitizing,
# only the viewer chrome survives — no document. Canonicalize it to the real filing markup we
# already serve for /evidence/html (same sanitize + persistent cache path).
_DART_VIEWER_RE = re.compile(r"^https?://dart\.fss\.or\.kr/dsaf001/main\.do", re.IGNORECASE)
_DART_RCPNO_RE = re.compile(r"[?&]rcpNo=(\d+)")


async def get_source_html(url: str) -> str | None:
    """Cache-first sanitized HTML for an arbitrary public source page (or None → UI uses the link)."""
    url = _strip_fragment(url)

    # a DART viewer link IS a filing — serve the real document through the filing path
    if _DART_VIEWER_RE.match(url):
        m = _DART_RCPNO_RE.search(url)
        if m:
            from app.store.filing_html import get_filing_html

            return await get_filing_html("KR", m.group(1))
        return None

    # a Google News interstitial can never render (client-side JS redirect) — resolve the
    # article id to the publisher URL first, then fetch/sanitize THAT page.
    from app.store.gnews_resolve import is_gnews_article_url, resolve_gnews_url

    fetch_url = url
    if is_gnews_article_url(url):
        resolved = await resolve_gnews_url(url)
        if not resolved:
            return None    # honest gap — the external link still redirects in a real browser
        fetch_url = _strip_fragment(resolved)

    if not _safe(fetch_url):
        return None
    path = _cache_path(url)   # keyed by the CITATION's url so repeat views hit the cache
    if path.exists():
        return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
    raw = await _fetch(fetch_url)
    if not raw or not raw.strip():
        return None
    from urllib.parse import urlsplit

    from app.store.filing_html import CSP_PASSIVE
    parts = urlsplit(fetch_url)
    page_base = f"{parts.scheme}://{parts.netloc}{parts.path.rsplit('/', 1)[0]}/"
    # external pages: PASSIVE CSP (styles/images/fonts over https render; scripts/XHR still dead)
    # + <base> so the page's relative asset URLs resolve inside srcdoc — fixes the gray shell.
    clean = sanitize(_META_POLICY_RE.sub("", raw), csp=CSP_PASSIVE, base=page_base)
    if _visible_chars(clean) < _MIN_VISIBLE_CHARS:
        # a script-rendered shell (or bot/consent wall) — caching it would poison every future
        # view with an empty iframe; return the honest gap instead.
        log.info("source html below visible-text floor, skipped %s", fetch_url[:100])
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, clean, encoding="utf-8")
    log.info("source html stored (%d KB) %s → %s", len(clean) // 1024, fetch_url[:80], path)
    return clean
