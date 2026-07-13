"""HI-8 / SC-1.12: search() caches so repeat work is free — a whole-funnel result cache (repeat
(query,filters,top_k) skips embed→retrieve→rerank) and a hash→vector embed cache (repeated query
text never re-hits the embedding API). Proven with a query-embed COUNTER."""

from __future__ import annotations

import hashlib
import math
import re

import rag.embeddings
import rag.ingest
import rag.search
from rag import store
from rag.ingest import ingest_docs
from rag.models import IngestDoc
from rag.search import search

_TOK = re.compile(r"[a-z0-9]+")


class _CountingEmbedder:
    dim = 64

    def __init__(self) -> None:
        self.q_calls = 0
        self.embed_calls: list[list[str]] = []

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOK.findall((text or "").lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else v

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.q_calls += 1
        return self._vec(text)


def _install(monkeypatch, emb) -> None:
    store.get_store.cache_clear()
    for mod in (rag.embeddings, rag.ingest, rag.search):
        monkeypatch.setattr(mod, "get_embedder", lambda: emb)
    monkeypatch.setattr(rag.search.settings, "multi_query", False)  # 1 query → 1 dense embed


async def test_result_cache_skips_funnel_on_repeat(monkeypatch):
    emb = _CountingEmbedder()
    _install(monkeypatch, emb)
    await ingest_docs([IngestDoc(text="Apple relies on TSMC for its custom chips.",
                                 source="SEC EDGAR", ticker="AAPL", market="US")])
    emb.q_calls = 0
    r1 = await search("Apple TSMC chips", top_k=3)
    assert r1 and emb.q_calls == 1                 # cold search embeds once
    r2 = await search("Apple TSMC chips", top_k=3)
    assert emb.q_calls == 1                         # repeat served from the result cache (no funnel)
    assert [h.text for h in r1] == [h.text for h in r2]


async def test_embed_cache_reuses_vector_with_result_cache_off(monkeypatch):
    emb = _CountingEmbedder()
    _install(monkeypatch, emb)
    monkeypatch.setattr(rag.search.settings, "search_cache_ttl_seconds", 0)  # result cache OFF
    await ingest_docs([IngestDoc(text="Apple relies on TSMC.", source="SEC EDGAR",
                                 ticker="AAPL", market="US")])
    emb.q_calls = 0
    await search("Apple TSMC", top_k=3)
    await search("Apple TSMC", top_k=3)             # same query text, funnel re-runs
    assert emb.q_calls == 1                          # embed cache reused the vector
