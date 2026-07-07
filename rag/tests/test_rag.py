"""RAG pipeline tests (chunk → embed → store → search → provenance) + tenant isolation.

Embeddings are Gemini-only in production, so these unit tests inject a deterministic LEXICAL fake
embedder (no key, no network) to exercise the full pipeline end-to-end; a separate live-key test
(test_rag_semantic.py) proves real SEMANTIC retrieval with the actual Gemini model.
"""

from __future__ import annotations

import hashlib
import math
import re

import pytest
from fastapi.testclient import TestClient

import rag.embeddings
import rag.ingest
import rag.search
from rag import store
from rag.chunk import chunk_text
from rag.ingest import ingest_docs
from rag.main import app
from rag.models import IngestDoc
from rag.search import search

client = TestClient(app)

_TOK = re.compile(r"[A-Za-z0-9]+|[가-힣]+")


class _FakeEmbedder:
    """Deterministic lexical embedder for key-free unit tests (stands in for the production Gemini
    embedder). Bag-of-hashed-tokens, L2-normalized — stable vector space to test the pipeline."""

    dim = 64

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOK.findall((text or "").lower()):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n else v

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch):
    fake = _FakeEmbedder()
    for mod in (rag.embeddings, rag.ingest, rag.search):
        monkeypatch.setattr(mod, "get_embedder", lambda: fake)
    store.get_store.cache_clear()
    yield


def _reset():
    store.get_store.cache_clear()  # fresh in-memory store


def test_chunking():
    text = "Para one is here.\n\nPara two follows.\n\nThird paragraph ends it."
    chunks = chunk_text(text, size=25, overlap=5)
    assert len(chunks) >= 2 and all(chunks)


async def test_ingest_search_with_provenance():
    _reset()
    docs = [
        IngestDoc(text="Apple discloses a limited number of suppliers including TSMC that manufacture its chips.",
                  source="SEC EDGAR", doc_type="10-K", ticker="AAPL", market="US",
                  url="https://sec.gov/aapl-10k", as_of="2025-11-01", section="Item 1A"),
        IngestDoc(text="The Bank of Korea raised its base interest rate to 3.5 percent.", source="ECOS", market="KR"),
        IngestDoc(text="Tesla expanded electric vehicle battery production at its gigafactory.", source="SEC EDGAR", ticker="TSLA", market="US"),
    ]
    n = await ingest_docs(docs)
    assert n >= 3
    hits = await search("Apple chip suppliers TSMC", top_k=3)
    assert hits and "TSMC" in hits[0].text
    prov = hits[0].provenance
    assert prov["ticker"] == "AAPL" and prov["source"] == "SEC EDGAR"
    assert prov["url"] == "https://sec.gov/aapl-10k" and prov["as_of"] == "2025-11-01"


async def test_reingest_same_doc_upserts_no_duplicates():
    # a re-run pipeline ingesting the SAME doc (stable doc_id) must REPLACE, not duplicate —
    # otherwise the memory corpus grows every sweep and retrieval returns repeated passages.
    _reset()
    doc = IngestDoc(text="Apple supply chain risk and TSMC concentration.", doc_id="aapl-10k:p.42",
                    source="SEC EDGAR", doc_type="filing", ticker="AAPL", market="US", accession="acc1")
    await ingest_docs([doc])
    st = store.get_store()
    n1 = len(st._chunks)
    await ingest_docs([doc])  # same doc_id again (idempotent re-run)
    await ingest_docs([doc])
    assert len(st._chunks) == n1  # upserted in place — corpus did NOT grow
    hits = await search("Apple supply chain TSMC", top_k=5)
    assert sum(1 for h in hits if "TSMC" in h.text) == 1  # the passage appears exactly once


async def test_reingest_unchanged_skips_embedding():
    # incremental: re-ingesting identical docs embeds nothing (the weekly filing_text sweep must
    # not re-embed unchanged filings); a CHANGED text under the same doc_id does re-embed.
    _reset()
    doc = IngestDoc(text="Apple relies on TSMC to fabricate its custom silicon chips.",
                    doc_id="aapl:s.1", source="SEC EDGAR", doc_type="filing", ticker="AAPL")
    assert await ingest_docs([doc]) >= 1          # first pass embeds
    assert await ingest_docs([doc]) == 0          # identical → nothing re-embedded
    changed = IngestDoc(text="Apple now sources chips from multiple foundries.",
                        doc_id="aapl:s.1", source="SEC EDGAR", doc_type="filing", ticker="AAPL")
    assert await ingest_docs([changed]) >= 1      # changed text → re-embedded


async def test_search_filter_by_market():
    _reset()
    await ingest_docs([
        IngestDoc(text="Samsung Electronics semiconductor memory chips business.", ticker="005930", market="KR", source="OpenDART"),
        IngestDoc(text="Apple semiconductor chips and suppliers overview.", ticker="AAPL", market="US", source="SEC EDGAR"),
    ])
    hits = await search("semiconductor chips", top_k=5, filters={"market": "KR"})
    assert hits and all(h.provenance.get("market") == "KR" for h in hits)


def test_endpoints():
    _reset()
    assert "gemini-embedding" in client.get("/rag/info").json()["embedding_model"]
    ing = client.post("/rag/ingest", json={"documents": [
        {"text": "Apple sources chips from TSMC, a key supplier.", "source": "SEC EDGAR", "ticker": "AAPL", "url": "https://sec.gov/x"}
    ]})
    assert ing.json()["chunks"] >= 1
    res = client.post("/rag/search", json={"query": "Apple TSMC supplier chips", "top_k": 3}).json()
    assert res["hits"] and res["hits"][0]["provenance"]["ticker"] == "AAPL"


async def test_reranker_none_passthrough():
    from rag.rerank import NoneReranker

    out = await NoneReranker().rerank("q", ["a", "b", "c"], 2)
    assert out == [(0, 0.0), (1, 0.0)]


# --- reranker wiring into search (the gcp Vertex ranker is exercised live in
#     test_rag_semantic.py; here we prove the search→rerank plumbing + fail-safe with fakes) ----
class _ReverseReranker:
    """Stand-in ranker that reverses the embedding order — lets us assert search applies its order."""

    async def rerank(self, query, docs, top_n):
        return [(i, 1.0) for i in reversed(range(len(docs)))][:top_n]


class _BrokenReranker:
    """Simulates a reranker outage (e.g. the GCP 403 before the SA is granted)."""

    async def rerank(self, query, docs, top_n):
        raise RuntimeError("403 PermissionDenied: discoveryengine.rankingConfigs.rank denied (simulated)")


async def test_search_applies_reranker_order(monkeypatch):
    # when a reranker is active, search must return hits in the RERANKER's order, not the embedder's.
    _reset()
    await ingest_docs([
        IngestDoc(text="Apple relies on TSMC to fabricate its custom silicon chips.", source="SEC EDGAR", ticker="AAPL"),
        IngestDoc(text="Apple discloses supplier concentration risk across its component vendors.", source="SEC EDGAR", ticker="AAPL2"),
        IngestDoc(text="Apple sources display panels and chips from several key suppliers.", source="SEC EDGAR", ticker="AAPL3"),
    ])
    q = "Apple chip suppliers TSMC concentration"
    monkeypatch.setattr(rag.search.settings, "reranker_backend", "none")
    base = [h.provenance["ticker"] for h in await search(q, top_k=3)]
    monkeypatch.setattr(rag.search.settings, "reranker_backend", "gcp")
    monkeypatch.setattr(rag.search, "get_reranker", lambda: _ReverseReranker())
    reranked = [h.provenance["ticker"] for h in await search(q, top_k=3)]
    assert reranked == base[::-1]  # reranker order won, end to end


async def test_search_survives_reranker_failure(monkeypatch):
    # a reranker outage must NEVER break search — it falls back to the embedding order (fail-safe).
    _reset()
    await ingest_docs([
        IngestDoc(text="Apple relies on TSMC to fabricate its custom silicon chips.", source="SEC EDGAR", ticker="AAPL"),
        IngestDoc(text="The cafeteria served pasta on Tuesday.", source="Misc", ticker="ZZZ"),
    ])
    monkeypatch.setattr(rag.search.settings, "reranker_backend", "gcp")
    monkeypatch.setattr(rag.search, "get_reranker", lambda: _BrokenReranker())
    hits = await search("TSMC silicon chips supplier", top_k=2)  # must not raise
    assert hits and hits[0].provenance["ticker"] == "AAPL"  # embedding order preserved


def test_get_reranker_selects_backend(monkeypatch):
    from rag import rerank

    for backend in ("none", "bogus", ""):
        rerank.get_reranker.cache_clear()
        monkeypatch.setattr(rerank.settings, "reranker_backend", backend)
        assert type(rerank.get_reranker()).__name__ == "NoneReranker"
    rerank.get_reranker.cache_clear()


def test_config_gcp_location_is_global():
    # the Vertex semantic-ranker ranking_config is global-only; a regional default would 404 it.
    from rag.config import settings as cfg

    assert cfg.gcp_location == "global"


def test_info_endpoint_reflects_backends():
    j = client.get("/rag/info").json()
    assert "gemini-embedding" in j["embedding_model"] and j["vector_store"] == "memory" and j["reranker_backend"] == "none"


async def test_search_on_empty_store_returns_nothing():
    _reset()
    assert await search("anything at all", top_k=5) == []


# --- PH-2a: per-tenant document isolation -------------------------------------
_HDR_A = {"X-Tenant-Id": "ten_a"}
_HDR_B = {"X-Tenant-Id": "ten_b"}


def _ingest(text: str, headers: dict | None = None):
    return client.post("/rag/ingest", json={"documents": [{"text": text, "source": "SEC EDGAR"}]},
                       headers=headers or {})


def test_tenant_cannot_see_another_tenants_docs():
    _reset()
    _ingest("Acme builds widgets exclusively for tenant A.", _HDR_A)
    # tenant B searches the same query → must not see tenant A's private doc
    resb = client.post("/rag/search", json={"query": "Acme widgets", "top_k": 5}, headers=_HDR_B).json()
    assert resb["hits"] == []
    # tenant A sees its own doc
    resa = client.post("/rag/search", json={"query": "Acme widgets", "top_k": 5}, headers=_HDR_A).json()
    assert resa["hits"] and "Acme" in resa["hits"][0]["text"]


def test_global_docs_visible_to_every_tenant():
    _reset()
    _ingest("Apple sources chips from TSMC, a key supplier.")  # no header → global
    # a scoped tenant still sees the shared/global corpus
    res = client.post("/rag/search", json={"query": "Apple TSMC supplier chips", "top_k": 5}, headers=_HDR_A).json()
    assert res["hits"] and "TSMC" in res["hits"][0]["text"]


def test_tenant_not_leaked_into_provenance():
    _reset()
    _ingest("Tenant-scoped note about chips.", _HDR_A)
    res = client.post("/rag/search", json={"query": "chips note", "top_k": 5}, headers=_HDR_A).json()
    assert res["hits"] and "tenant" not in res["hits"][0]["provenance"]


async def test_search_respects_top_k():
    _reset()
    await ingest_docs([
        IngestDoc(text=f"Document number {i} about semiconductor chips and suppliers.", source="SEC EDGAR", ticker=f"T{i}")
        for i in range(6)
    ])
    hits = await search("semiconductor chips suppliers", top_k=2)
    assert len(hits) == 2


async def test_search_ranks_relevant_above_unrelated():
    _reset()
    await ingest_docs([
        IngestDoc(text="Apple relies on TSMC to fabricate its custom silicon chips.", source="SEC EDGAR", ticker="AAPL"),
        IngestDoc(text="The cafeteria menu featured pasta and salad on Tuesday.", source="Misc", ticker="ZZZ"),
    ])
    hits = await search("TSMC silicon chips supplier", top_k=2)
    assert hits[0].provenance["ticker"] == "AAPL"  # relevant doc ranks first


async def test_search_filter_by_ticker():
    _reset()
    await ingest_docs([
        IngestDoc(text="Apple chips and suppliers.", ticker="AAPL", market="US", source="SEC EDGAR"),
        IngestDoc(text="Apple-like fruit and orchard suppliers.", ticker="FARM", market="US", source="Misc"),
    ])
    hits = await search("apple suppliers", top_k=5, filters={"ticker": "AAPL"})
    assert hits and all(h.provenance.get("ticker") == "AAPL" for h in hits)


def test_long_text_chunks_into_multiple_pieces():
    text = ". ".join(f"Sentence {i} about supply chains and disclosures" for i in range(40))
    chunks = chunk_text(text, size=80, overlap=10)
    assert len(chunks) > 1 and all(chunks)


# ── RQ-2: structure-aware chunking ──────────────────────────────────────────
def test_chunk_keeps_table_rows_atomic_and_prefixes_heading():
    text = ("Item 1A. Risk Factors\n\n"
            "Supply chain risk is material to operations.\n\n"
            "Metric | 2025 | 2024\nRevenue | 391.0 | 383.3\nNet income | 93.7 | 97.0")
    chunks = chunk_text(text, size=400, overlap=40)
    joined = "\n---\n".join(chunks)
    # heading context rides on the chunk(s)
    assert any("Item 1A. Risk Factors" in c for c in chunks)
    # a table row is never split across the ' | ' — the full row survives in one chunk
    assert any("Revenue | 391.0 | 383.3" in c for c in chunks), joined
    assert any("Net income | 93.7 | 97.0" in c for c in chunks), joined


def test_chunk_splits_oversized_paragraph_on_sentences():
    para = " ".join(f"Sentence number {i} states a distinct fact." for i in range(40))
    chunks = chunk_text(para, size=200, overlap=30)
    assert len(chunks) >= 3
    # no chunk ends mid-sentence (each ends on a terminator, modulo trailing space)
    for c in chunks:
        assert c.rstrip().endswith(".") or c.rstrip().endswith("fact")


# ── RQ-1: hybrid lexical leg + RRF fusion ──────────────────────────────────

async def test_lexical_leg_finds_exact_identifier_the_dense_miss():
    # The fake embedder is bag-of-tokens, so an accession number embeds to near-nothing; the
    # lexical leg must still surface the chunk that literally contains it (the hybrid win).
    _reset()
    await ingest_docs([
        IngestDoc(text="The company faces competition and margin pressure in cloud.", doc_id="a"),
        IngestDoc(text="Filing accession 0000320193-24-000123 discusses buyback capacity.", doc_id="b"),
    ])
    hits = await search("0000320193-24-000123", top_k=2)
    assert hits and "0000320193-24-000123" in hits[0].text


async def test_lexical_matches_korean_prefix_token():
    _reset()
    await ingest_docs([
        IngestDoc(text="반도체 업황이 개선되며 실적이 회복되었다.", doc_id="k1"),
        IngestDoc(text="삼성전자의 반도체 부문 매출이 크게 늘었다.", doc_id="k2"),
    ])
    # query carries a particle (삼성전자가) — prefix token 삼성전자:* still matches 삼성전자의
    hits = await search("삼성전자가 어떻게 됐나", top_k=2)
    assert hits and any("삼성전자" in h.text for h in hits)


# ── RQ-2: replace-by-accession ─────────────────────────────────────────────

async def test_replace_by_accession_drops_stale_sections():
    _reset()
    # first ingest: 3 sections for one accession
    await rag.ingest.ingest_docs([
        IngestDoc(text=f"Old section {i} text about risk.", doc_id=f"AC:s.{i}", accession="AC")
        for i in range(1, 4)])
    # re-chunk: now only 1 section for the SAME accession → replace must delete the other two
    await rag.ingest.ingest_docs(
        [IngestDoc(text="Fresh single section text about risk.", doc_id="AC:s.1", accession="AC")],
        replace={"accession": "AC"})
    hits = await search("risk", top_k=10, filters={"accession": "AC"})
    texts = [h.text for h in hits]
    assert any("Fresh single section" in t for t in texts)
    assert not any("Old section" in t for t in texts), texts


# ── recall harness: a tiny graded set, recall@k over hybrid ────────────────

async def test_recall_at_k_hybrid_over_mixed_queries():
    _reset()
    corpus = {
        "d_rev": "Total revenue increased 18% to $35.1 billion in fiscal 2025.",
        "d_risk": "Item 1A. Risk Factors: supply chain disruption could impair production.",
        "d_buyback": "The board authorized a $10 billion share repurchase program.",
        "d_kr": "삼성전자 3분기 영업이익이 시장 기대치를 상회했다.",
        "d_noise": "The cafeteria menu changed to include vegetarian options.",
    }
    await ingest_docs([IngestDoc(text=t, doc_id=i) for i, t in corpus.items()])
    graded = [
        ("how much did revenue grow", "d_rev"),
        ("share buyback authorization", "d_buyback"),
        ("supply chain risk factors", "d_risk"),
        ("삼성전자 영업이익", "d_kr"),
    ]
    hit_at_3 = 0
    for q, want in graded:
        hits = await search(q, top_k=3)
        if want in {h.provenance.get("doc_type") or "" for h in hits} or \
           any(corpus[want][:20] in h.text for h in hits):
            hit_at_3 += 1
    # hybrid (dense+lexical) over this tiny lexical-embedder set should nail ≥3/4
    assert hit_at_3 >= 3, f"recall@3 too low: {hit_at_3}/4"
