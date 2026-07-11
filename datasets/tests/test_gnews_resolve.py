"""Unit tests for the Google News article-URL resolver (`app.store.gnews_resolve`).

Google News RSS links are client-side interstitials — the publisher URL must be *decoded* from
the article id (older ids base64-embed it) or asked from Google's own batchexecute splash
endpoint (newer, opaque ids). Both paths are pinned here without any live network: the
batchexecute flow runs against respx-mocked responses in the real wire format.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from app.store import gnews_resolve as G


@pytest.fixture(autouse=True)
def _fresh_cache():
    # the id → URL cache is module-level; keep each test hermetic
    G._cache.clear()
    yield
    G._cache.clear()


def _article_url(payload: bytes, prefix: str = "rss/", query: str = "?oc=5") -> str:
    aid = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"https://news.google.com/{prefix}articles/{aid}{query}"


# --- is_gnews_article_url ----------------------------------------------------
def test_is_gnews_article_url_accepts_both_article_paths():
    assert G.is_gnews_article_url("https://news.google.com/rss/articles/CBMiabc?oc=5") is True
    assert G.is_gnews_article_url("https://news.google.com/articles/CBMiabc") is True


def test_is_gnews_article_url_rejects_publisher_and_non_article_urls():
    assert G.is_gnews_article_url("https://publisher.example/articles/x") is False
    assert G.is_gnews_article_url("https://news.google.com/topstories") is False
    assert G.is_gnews_article_url("https://news.google.com/") is False
    assert G.is_gnews_article_url("http://[unparsable") is False   # never a raise


# --- decode_local (zero-network fast path) -------------------------------------
def test_decode_local_extracts_publisher_url():
    # older ids base64-encode a protobuf-ish blob with the publisher URL inline; the byte after
    # the URL is a control byte (< 0x21) so the scan stops exactly at the URL boundary.
    raw = b"\x08\x13\x22\x1f" + b"https://publisher.example/story" + b"\x10\x01"
    assert G.decode_local(_article_url(raw)) == "https://publisher.example/story"
    # the /articles/ (no rss/) shape decodes the same way
    assert G.decode_local(_article_url(raw, prefix="")) == "https://publisher.example/story"


def test_decode_local_opaque_id_returns_none():
    raw = b"\x01\x02 opaque token without any link \x03"
    assert G.decode_local(_article_url(raw)) is None


def test_decode_local_google_target_is_a_miss():
    # a decoded google/AMP-cache URL is NOT the publisher — must fall through to batchexecute
    for target in ("https://news.google.com/foo", "https://lh3.googleusercontent.com/img"):
        raw = b"\x22" + target.encode() + b"\x10"
        assert G.decode_local(_article_url(raw)) is None


def test_decode_local_non_article_or_bad_base64_returns_none():
    assert G.decode_local("https://news.google.com/topstories") is None
    assert G.decode_local("https://publisher.example/story") is None
    assert G.decode_local("https://news.google.com/rss/articles/!!not-base64!!") is None


# --- batchexecute envelope parsing ---------------------------------------------
def _wire_body(inner_payload) -> str:
    # the REAL wire format: anti-JSON prefix + outer array whose row[2] is a JSON *string*
    # (so the garturlres array arrives with escaped quotes inside it)
    inner = json.dumps(inner_payload, separators=(",", ":"))
    return ")]}'\n\n" + json.dumps(
        [["wrb.fr", "Fbv4je", inner, None, None, None, "generic"], ["di", 27], ["af.httprm", 27]])


def test_parse_batch_response_unwraps_double_encoded_payload():
    body = _wire_body(["garturlres", "https://publisher.example/deep/story?a=1", 600])
    assert '[\\"garturlres\\"' in body   # proves the payload is escaped on the wire
    assert G._parse_batch_response(body) == "https://publisher.example/deep/story?a=1"


def test_parse_batch_response_rejects_garbage_and_non_url_payloads():
    assert G._parse_batch_response("") is None
    assert G._parse_batch_response(")]}'\n\nnot json at all") is None
    assert G._parse_batch_response(")]}'\n\n[[1,2,3]]") is None
    # a garturlres row whose value is not an http(s) URL must not be returned
    assert G._parse_batch_response(_wire_body(["garturlres", "javascript:alert(1)"])) is None
    # a different response kind is not a resolution
    assert G._parse_batch_response(_wire_body(["someotherres", "https://x.example/y"])) is None


# --- resolve_gnews_url --------------------------------------------------------
@pytest.mark.asyncio
async def test_resolve_uses_local_decode_and_caches_by_article_id(monkeypatch):
    url = _article_url(b"\x22" + b"https://publisher.example/story" + b"\x10")
    assert await G.resolve_gnews_url(url) == "https://publisher.example/story"

    # cached: the second resolve must not decode (or fetch) again
    def boom(u):
        raise AssertionError("decode must not run again for a cached id")
    monkeypatch.setattr(G, "decode_local", boom)
    assert await G.resolve_gnews_url(url) == "https://publisher.example/story"


@pytest.mark.asyncio
async def test_resolve_non_article_url_is_none():
    assert await G.resolve_gnews_url("https://publisher.example/story") is None


@pytest.mark.asyncio
@respx.mock
async def test_resolve_opaque_id_via_batchexecute_flow():
    # an opaque id (no URL in the base64) → interstitial GET for the signature attributes →
    # batchexecute POST → publisher URL out of the double-encoded payload. All mocked.
    url = _article_url(b"\x01\x02 opaque \x03", query="")
    page = ('<html><body><c-wiz data-n-a-id="x" data-n-a-sg="AbC_sig-token" '
            'data-n-a-ts="1751852800"></c-wiz></body></html>')
    respx.get(url).mock(return_value=httpx.Response(200, html=page))
    batch = respx.post(G._BATCH_URL).mock(return_value=httpx.Response(
        200, text=_wire_body(["garturlres", "https://publisher.example/deep/story", 600])))

    assert await G.resolve_gnews_url(url) == "https://publisher.example/deep/story"
    assert batch.called
    # the signature attributes from the page rode along in the f.req form payload
    sent = batch.calls.last.request.content.decode()
    assert "AbC_sig-token" in sent and "1751852800" in sent and sent.startswith("f.req=")


@pytest.mark.asyncio
async def test_resolve_failure_is_not_cached(monkeypatch):
    # a transient miss must NOT poison the cache — the next view retries and can succeed
    url = _article_url(b"\x01\x02 opaque \x03")

    monkeypatch.setattr(G, "decode_local", lambda u: None)

    async def no_result(u, client):
        return None
    monkeypatch.setattr(G, "_decode_batchexecute", no_result)
    assert await G.resolve_gnews_url(url) is None

    monkeypatch.setattr(G, "decode_local", lambda u: "https://publisher.example/story")
    assert await G.resolve_gnews_url(url) == "https://publisher.example/story"
