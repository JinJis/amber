"""RAG-ingest client behavior (ING-1): the /rag/ingest POST helpers in app.store.news_ingest.

Covers the three ING-1 changes to how docs are POSTed to the RAG service:
  * a ``replace``-scoped ingest (a filing/transcript/deck — one accession's docs) goes in ONE POST
    so the server can compute the full new chunk-id set and do the atomic prune-swap;
  * feeds without a scope (news/era_news) still batch 40 per POST, none carrying ``replace``;
  * the client read budget scales with doc count (``_ingest_timeout``);
  * ``_post_ingest`` retries ONCE on a transient failure (TransportError / 5xx) but never on 4xx.

httpx is mocked with respx (same as the transcript/deck suites); ``asyncio.sleep`` is stubbed so the
15s retry backoff never actually blocks the test. No network, no keys.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import settings
from app.store import news_ingest as N


async def _no_sleep(_seconds):
    """Drop-in for asyncio.sleep so the retry backoff doesn't cost real wall-time."""
    return None


def _body(call) -> dict:
    """The JSON body of a recorded respx call."""
    return json.loads(call.request.content)


# --- replace-scoped ingest → ONE POST carrying every doc + the replace key -------------------
@respx.mock
async def test_replace_scoped_ingest_is_a_single_post():
    route = respx.post("http://rag.test/rag/ingest").mock(
        return_value=httpx.Response(200, json={"chunks": 250}))
    docs = [{"text": f"doc {i}", "doc_id": str(i), "accession": "X"} for i in range(100)]

    total = await N._ingest_to_rag("http://rag.test", docs, replace={"accession": "X"})

    assert route.call_count == 1                     # NOT split into 40-doc batches
    body = _body(route.calls[0])
    assert len(body["documents"]) == 100             # the whole accession in one request…
    assert body["replace"] == {"accession": "X"}     # …so the server can do the atomic prune-swap
    assert total == 250


# --- non-replace feed → still batches 40, none carrying a replace scope ----------------------
@respx.mock
async def test_non_replace_feed_still_batches_40():
    route = respx.post("http://rag.test/rag/ingest").mock(
        return_value=httpx.Response(200, json={"chunks": 1}))
    docs = [{"text": f"doc {i}", "doc_id": str(i)} for i in range(100)]

    total = await N._ingest_to_rag("http://rag.test", docs)   # no replace scope

    assert route.call_count == 3                              # 100 / 40 → 40 + 40 + 20
    sizes = [len(_body(c)["documents"]) for c in route.calls]
    assert sizes == [40, 40, 20]
    assert all("replace" not in _body(c) for c in route.calls)   # nothing to swap → no scope
    assert total == 3                                        # 1 chunk per POST × 3, summed


# --- scaled client timeout: read = min(base + per_doc*docs, max) -----------------------------
def test_ingest_timeout_scales_with_doc_count_and_caps():
    base = settings.rag_ingest_timeout_base_seconds      # 120
    per = settings.rag_ingest_timeout_per_doc_seconds    # 6
    cap = settings.rag_ingest_timeout_max_seconds        # 1200

    t10 = N._ingest_timeout(10)
    assert t10.read == base + per * 10 == 180.0          # scales linearly with the doc count

    t1000 = N._ingest_timeout(1000)
    assert t1000.read == cap == 1200.0                   # capped — base + 6*1000 = 6120 → 1200

    # connect/write/pool stay small + fixed; only the read (embed wall-time) grows
    assert (t10.connect, t10.write, t10.pool) == (10.0, 60.0, 10.0)
    assert (t1000.connect, t1000.write, t1000.pool) == (10.0, 60.0, 10.0)


# --- _post_ingest: exactly one retry on a transient failure ---------------------------------
@respx.mock
async def test_post_ingest_retries_once_on_5xx(monkeypatch):
    monkeypatch.setattr(N.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json={"chunks": 7})

    respx.post("http://rag.test/rag/ingest").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        n = await N._post_ingest(client, "http://rag.test/rag/ingest", {"documents": []})

    assert n == 7 and calls["n"] == 2    # 503 once → one retry → 200; the chunk count is NOT doubled


@respx.mock
async def test_post_ingest_retries_once_on_transport_error(monkeypatch):
    monkeypatch.setattr(N.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")   # a dropped connection, then success
        return httpx.Response(200, json={"chunks": 3})

    respx.post("http://rag.test/rag/ingest").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        n = await N._post_ingest(client, "http://rag.test/rag/ingest", {"documents": []})

    assert n == 3 and calls["n"] == 2


@respx.mock
async def test_post_ingest_gives_up_after_two_failures(monkeypatch):
    monkeypatch.setattr(N.asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("still down")

    respx.post("http://rag.test/rag/ingest").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await N._post_ingest(client, "http://rag.test/rag/ingest", {"documents": []})

    assert calls["n"] == 2    # tried twice (one retry), then propagated — not an infinite loop


# --- _post_ingest: a 4xx is a real client error → fail fast, never retry ---------------------
@respx.mock
async def test_post_ingest_does_not_retry_on_4xx(monkeypatch):
    monkeypatch.setattr(N.asyncio, "sleep", _no_sleep)   # a stray sleep would betray a wrong retry
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, json={"detail": "bad docs"})

    respx.post("http://rag.test/rag/ingest").mock(side_effect=handler)
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPStatusError):
            await N._post_ingest(client, "http://rag.test/rag/ingest", {"documents": []})

    assert calls["n"] == 1    # raise_for_status fires immediately; no second attempt
