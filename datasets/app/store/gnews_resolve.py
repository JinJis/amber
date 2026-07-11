"""Resolve a Google News RSS article URL to the ORIGINAL publisher article URL.

Google News RSS ``<link>``s (``https://news.google.com/rss/articles/CBMi…``) are interstitials:
the server never HTTP-redirects to the publisher — a 302 only bounces back to news.google.com
itself, and the 200 body is a script-only shell whose redirect happens client-side. So a
server-side fetch can never reach the article by following redirects; the id must be *decoded*.

Two decoding paths, cheap first:

1. **Local decode** — older article ids base64-encode the publisher URL directly; decoding the
   token and scanning for an ``http(s)://`` string costs no network call.
2. **batchexecute decode** — newer ids are opaque; Google's own splash endpoint
   (``/_/DotsSplashUi/data/batchexecute``, the same call the shell page's script makes) returns
   the target URL given the article id + the ``data-n-a-sg``/``data-n-a-ts`` signature attributes
   embedded in the interstitial HTML.

Results are cached in-process (the id → URL mapping is immutable). On any failure the caller
gets ``None`` and degrades honestly (the external link still works in a real browser).
"""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import quote, urlparse

import httpx

log = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/118.0"}
_TIMEOUT = 12.0
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

_ARTICLE_PATH_RE = re.compile(r"^/(?:rss/)?articles/([^/?#]+)")
_URL_IN_BYTES_RE = re.compile(rb"https?://[^\x00-\x20\"<>\\]+")
_SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')

_cache: dict[str, str | None] = {}
_CACHE_MAX = 4096


def is_gnews_article_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return (p.hostname or "").endswith("news.google.com") and bool(_ARTICLE_PATH_RE.match(p.path or ""))


def _article_id(url: str) -> str | None:
    m = _ARTICLE_PATH_RE.match(urlparse(url).path or "")
    return m.group(1) if m else None


def decode_local(url: str) -> str | None:
    """Zero-network fast path: older article ids base64-embed the publisher URL."""
    aid = _article_id(url)
    if not aid:
        return None
    try:
        raw = base64.urlsafe_b64decode(aid + "=" * (-len(aid) % 4))
    except (ValueError, TypeError):
        return None
    m = _URL_IN_BYTES_RE.search(raw)
    if not m:
        return None
    try:
        out = m.group(0).decode("utf-8")
    except UnicodeDecodeError:
        return None
    # a decoded google/AMP-cache URL is not the publisher; treat it as a miss
    host = (urlparse(out).hostname or "").lower()
    if not host or host.endswith("google.com") or host.endswith("googleusercontent.com"):
        return None
    return out


async def _decode_batchexecute(url: str, client: httpx.AsyncClient) -> str | None:
    """Ask Google's own splash endpoint for the target URL (what the shell script does)."""
    aid = _article_id(url)
    if not aid:
        return None
    page = await client.get(url, headers=_UA, follow_redirects=True)
    if page.status_code != 200:
        return None
    sg, ts = _SG_RE.search(page.text), _TS_RE.search(page.text)
    if not (sg and ts):
        return None
    inner = json.dumps([
        "garturlreq",
        [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1,
          None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
        aid, int(ts.group(1)), sg.group(1),
    ], separators=(",", ":"))
    freq = json.dumps([[["Fbv4je", inner, None, "generic"]]], separators=(",", ":"))
    resp = await client.post(
        _BATCH_URL,
        headers={**_UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        content=f"f.req={quote(freq)}",
    )
    if resp.status_code != 200:
        return None
    return _parse_batch_response(resp.text)


def _parse_batch_response(text: str) -> str | None:
    """The batchexecute envelope: an anti-JSON `)]}'` prefix, then a JSON array whose
    `["wrb.fr","Fbv4je","<json-string>"]` row wraps `["garturlres","<publisher url>", …]`."""
    start = text.find("[")
    if start < 0:
        return None
    try:
        rows = json.loads(text[start:])
    except ValueError:
        return None
    for row in rows if isinstance(rows, list) else []:
        if not (isinstance(row, list) and len(row) >= 3 and row[0] == "wrb.fr" and isinstance(row[2], str)):
            continue
        try:
            inner = json.loads(row[2])
        except ValueError:
            continue
        if isinstance(inner, list) and len(inner) >= 2 and inner[0] == "garturlres" \
                and isinstance(inner[1], str) and inner[1].startswith("http"):
            return inner[1]
    return None


async def resolve_gnews_url(url: str) -> str | None:
    """news.google.com/(rss/)articles/… → the publisher article URL, or None (caller degrades)."""
    aid = _article_id(url)
    if not aid:
        return None
    if aid in _cache:
        return _cache[aid]
    out = decode_local(url)
    if out is None:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                out = await _decode_batchexecute(url, client)
        except httpx.HTTPError as exc:
            log.info("gnews resolve failed %s: %s", url[:100], exc)
            return None    # transient — don't cache a failure, the next view retries
    if out is None:
        return None
    log.info("gnews resolved → %s", out[:120])
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[aid] = out
    return out
