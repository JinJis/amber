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
    embedder). Bag-of-hashed-tokens, L2-normalized — stable vector space to test the pipeline.
    Records each ``embed`` call's texts in ``embed_calls`` so tests can prove an unchanged re-ingest
    embeds ZERO chunks (ING-1 incremental-skip)."""

    dim = 64

    def __init__(self) -> None:
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
    n = (await ingest_docs(docs))["chunks"]
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
    assert (await ingest_docs([doc]))["chunks"] >= 1          # first pass embeds
    assert (await ingest_docs([doc]))["chunks"] == 0          # identical → nothing re-embedded
    changed = IngestDoc(text="Apple now sources chips from multiple foundries.",
                        doc_id="aapl:s.1", source="SEC EDGAR", doc_type="filing", ticker="AAPL")
    assert (await ingest_docs([changed]))["chunks"] >= 1      # changed text → re-embedded


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
    rag.search._result_cache.clear()  # HI-8: reranker config changed mid-test → re-run the funnel
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


# ── ING-1: atomic prune-swap (replace_scope) + incremental skip under replace ──
# The swap is chunk → existing_texts (before any delete) → embed only new/changed →
# ONE store op (prune stale ids for the scope + upsert). These prove: stale-id pruning,
# atomicity on embed failure (the delete never precedes the embed), the incremental skip
# still fires under `replace`, keep-set = ALL new ids, and the empty-extraction guard.


def _acc_ids(accession: str, tenant: object = "__any__") -> set[str]:
    """Ids of stored chunks for an accession (optionally pinned to a tenant), read straight
    off the MemoryStore — a direct assertion on what the atomic swap left behind."""
    st = store.get_store()
    return {c.id for c in st._chunks
            if c.accession == accession and (tenant == "__any__" or c.tenant == tenant)}


async def test_replace_prunes_stale_ids_and_spares_other_accessions():
    # re-chunk shrink: an accession that had 3 sections is re-ingested as 1 → the 2 stale ids are
    # pruned, the surviving id is intact, and a DIFFERENT accession is never touched.
    _reset()
    await ingest_docs([IngestDoc(text=f"AC section {i} discusses supply chain risk.",
                                 doc_id=f"AC:s.{i}", accession="AC") for i in range(1, 4)])
    await ingest_docs([IngestDoc(text="Unrelated filing about a share buyback.",
                                 doc_id="OT:s.1", accession="OT")])
    assert _acc_ids("AC") == {"AC:s.1::0", "AC:s.2::0", "AC:s.3::0"}
    res = await ingest_docs([IngestDoc(text="AC now a single fresh section about risk.",
                                       doc_id="AC:s.1", accession="AC")],
                            replace={"accession": "AC"})
    assert res["chunks"] == 1 and res["pruned"] == 2      # s.2 + s.3 dropped in the swap
    assert _acc_ids("AC") == {"AC:s.1::0"}                # surviving id intact, stale ids gone
    assert _acc_ids("OT") == {"OT:s.1::0"}                # the other accession untouched
    hits = await search("supply chain risk", top_k=10, filters={"accession": "AC"})
    assert hits and not any("section 2" in h.text.lower() for h in hits)


async def test_ingest_atomic_on_embed_failure_leaves_store_unchanged(monkeypatch):
    # THE key ING-1 regression: if embedding fails, the prune-swap must NOT have run — the delete
    # is ordered AFTER the embed, so a mid-ingest embed outage leaves the corpus byte-identical.
    _reset()
    await ingest_docs([IngestDoc(text=f"Section {i} discusses supply chain risk factors.",
                                 doc_id=f"AC:s.{i}", accession="AC") for i in range(1, 4)])
    st = store.get_store()
    before_ids = {c.id for c in st._chunks}
    before_texts = {c.id: c.text for c in st._chunks}

    class _RaisingEmbedder:
        dim = 64

        async def embed(self, texts):
            raise RuntimeError("gemini embed 503 mid-ingest (simulated)")

        async def embed_query(self, text):
            return [0.0] * self.dim

    monkeypatch.setattr(rag.ingest, "get_embedder", lambda: _RaisingEmbedder())
    # a re-ingest with CHANGED text → non-empty `todo` → the embed call fires → raises
    with pytest.raises(RuntimeError):
        await ingest_docs([IngestDoc(text=f"CHANGED section {i} text.",
                                     doc_id=f"AC:s.{i}", accession="AC") for i in range(1, 4)],
                          replace={"accession": "AC"})
    # store UNCHANGED — no delete happened before the failed embed
    assert {c.id for c in st._chunks} == before_ids
    assert {c.id: c.text for c in st._chunks} == before_texts   # old text still present (not swapped)
    # search still uses the fake query embedder (only rag.ingest was repatched) → old content stands
    hits = await search("supply chain risk", top_k=10, filters={"accession": "AC"})
    assert hits and all("CHANGED" not in h.text for h in hits)


async def test_replace_unchanged_reingest_embeds_zero_and_keeps_corpus_identical():
    # incremental skip fires EVEN UNDER `replace`: existing_texts is read BEFORE any delete, so a
    # re-ingest of identical docs re-embeds nothing (0 embed calls) and the corpus is byte-identical.
    _reset()
    docs = [IngestDoc(text=f"Section {i} discusses supply chain and risk factors.",
                      doc_id=f"AC:s.{i}", accession="AC") for i in range(1, 4)]
    await ingest_docs(docs)
    st = store.get_store()
    before = {c.id: c.text for c in st._chunks}
    fake = rag.ingest.get_embedder()
    calls_before = len(fake.embed_calls)
    res = await ingest_docs(docs, replace={"accession": "AC"})
    assert res == {"chunks": 0, "pruned": 0, "skipped": 3}
    assert len(fake.embed_calls) == calls_before          # NO re-embed on the second pass
    assert {c.id: c.text for c in st._chunks} == before    # corpus byte-identical (ids + texts)


async def test_replace_changed_text_reembeds_same_id_and_survives_prune():
    # a single chunk's text changes → it is re-embedded (chunks>=1) and, sharing its id, survives
    # the prune (same id → in keep-set) rather than being dropped.
    _reset()
    await ingest_docs([IngestDoc(text="Apple relies on TSMC to fabricate its chips.",
                                 doc_id="AC:s.1", accession="AC")])
    res = await ingest_docs([IngestDoc(text="Apple now sources chips from several foundries.",
                                       doc_id="AC:s.1", accession="AC")],
                            replace={"accession": "AC"})
    assert res["chunks"] >= 1 and res["pruned"] == 0
    assert _acc_ids("AC") == {"AC:s.1::0"}                 # same id, still present
    hits = await search("Apple chips foundries", top_k=5, filters={"accession": "AC"})
    assert hits and any("foundries" in h.text for h in hits)


async def test_replace_keep_set_is_all_new_ids_not_just_todo():
    # 3 sections, only 1 changes → the 2 UNCHANGED sections survive the prune because keep_ids =
    # ALL new ids (not just `todo`); only the changed section is re-embedded.
    _reset()
    await ingest_docs([IngestDoc(text=f"Section {i} states fact number {i} plainly.",
                                 doc_id=f"AC:s.{i}", accession="AC") for i in range(1, 4)])
    fake = rag.ingest.get_embedder()
    calls_before = len(fake.embed_calls)
    res = await ingest_docs([
        IngestDoc(text="Section 1 states fact number 1 plainly.", doc_id="AC:s.1", accession="AC"),
        IngestDoc(text="Section 2 now states a brand new fact.", doc_id="AC:s.2", accession="AC"),
        IngestDoc(text="Section 3 states fact number 3 plainly.", doc_id="AC:s.3", accession="AC"),
    ], replace={"accession": "AC"})
    assert res["chunks"] == 1 and res["skipped"] == 2 and res["pruned"] == 0
    assert _acc_ids("AC") == {"AC:s.1::0", "AC:s.2::0", "AC:s.3::0"}   # unchanged sections survived
    assert len(fake.embed_calls) == calls_before + 1                  # one re-embed pass...
    assert fake.embed_calls[-1] == ["Section 2 now states a brand new fact."]  # ...of just the change


async def test_empty_extraction_never_prunes_a_good_prior_ingest():
    # honesty over blowing away good data: an empty doc list (or docs that chunk to nothing) under a
    # `replace` scope must NOT delete the prior good ingest — and returns chunks 0.
    _reset()
    await ingest_docs([IngestDoc(text="A good prior filing about supply chain risk.",
                                 doc_id="AC:s.1", accession="AC")])
    before = _acc_ids("AC")
    assert await ingest_docs([], replace={"accession": "AC"}) == {"chunks": 0, "pruned": 0, "skipped": 0}
    res = await ingest_docs([IngestDoc(text="   ", doc_id="AC:s.1", accession="AC")],
                            replace={"accession": "AC"})
    assert res["chunks"] == 0 and res["pruned"] == 0
    assert _acc_ids("AC") == before                        # prior good ingest survives
    assert await search("supply chain risk", top_k=5, filters={"accession": "AC"})


# ── ING-1 Phase 3: the replace scope is ALWAYS tenant-pinned (incl. None → global) ──
# main.py stamps the tenant from x-tenant-id into the replace scope, so a global re-ingest can
# never prune a tenant's same-accession rows, and vice versa.

def test_global_replace_prunes_only_global_rows_sparing_tenant_rows():
    _reset()
    # a tenant's private copy of accession AC
    assert client.post("/rag/ingest", json={"documents": [
        {"text": "Tenant p1 private note about accession AC risk.", "source": "SEC EDGAR",
         "accession": "AC", "doc_id": "AC:s.1"}]}, headers={"X-Tenant-Id": "p1"}).status_code == 200
    # global (no header) accession AC with TWO sections
    client.post("/rag/ingest", json={"documents": [
        {"text": "Global section 1 about accession AC.", "source": "SEC EDGAR", "accession": "AC", "doc_id": "AC:s.1"},
        {"text": "Global section 2 about accession AC.", "source": "SEC EDGAR", "accession": "AC", "doc_id": "AC:s.2"}]})
    assert _acc_ids("AC", tenant=None) == {"AC:s.1::0", "AC:s.2::0"}
    assert _acc_ids("AC", tenant="p1") == {"p1::AC:s.1::0"}
    # re-ingest GLOBAL AC shorter (1 section) with replace → prunes only tenant=None rows
    res = client.post("/rag/ingest", json={"documents": [
        {"text": "Global fresh single section about accession AC.", "source": "SEC EDGAR",
         "accession": "AC", "doc_id": "AC:s.1"}], "replace": {"accession": "AC"}}).json()
    assert {"chunks", "pruned", "skipped"} <= set(res)
    assert res["pruned"] == 1                              # only global s.2 dropped
    assert _acc_ids("AC", tenant=None) == {"AC:s.1::0"}    # global pruned to the fresh set
    assert _acc_ids("AC", tenant="p1") == {"p1::AC:s.1::0"}  # the tenant's row is UNTOUCHED


def test_tenant_replace_does_not_touch_global_rows():
    _reset()
    # a global copy of accession BB
    client.post("/rag/ingest", json={"documents": [
        {"text": "Global note about accession BB.", "source": "SEC EDGAR", "accession": "BB", "doc_id": "BB:s.1"}]})
    # tenant p1 accession BB with TWO sections
    client.post("/rag/ingest", json={"documents": [
        {"text": "Tenant p1 section 1 about BB.", "source": "SEC EDGAR", "accession": "BB", "doc_id": "BB:s.1"},
        {"text": "Tenant p1 section 2 about BB.", "source": "SEC EDGAR", "accession": "BB", "doc_id": "BB:s.2"}]},
        headers={"X-Tenant-Id": "p1"})
    assert _acc_ids("BB", tenant="p1") == {"p1::BB:s.1::0", "p1::BB:s.2::0"}
    # p1 re-ingests BB shorter with replace → prunes only p1's rows
    res = client.post("/rag/ingest", json={"documents": [
        {"text": "Tenant p1 fresh single section about BB.", "source": "SEC EDGAR",
         "accession": "BB", "doc_id": "BB:s.1"}], "replace": {"accession": "BB"}},
        headers={"X-Tenant-Id": "p1"}).json()
    assert {"chunks", "pruned", "skipped"} <= set(res)
    assert res["pruned"] == 1                              # only p1's s.2 dropped
    assert _acc_ids("BB", tenant="p1") == {"p1::BB:s.1::0"}
    assert _acc_ids("BB", tenant=None) == {"BB:s.1::0"}   # the global row is UNTOUCHED


# ── ING-1 Phase 4: concurrent sub-batch embedding preserves positional order ──

async def test_concurrent_embedding_preserves_positional_order(monkeypatch):
    # GeminiEmbedder._embed fans sub-batches out via asyncio.gather; results are re-sorted by batch
    # index so the returned vectors keep the SAME positional order as the input texts — regardless
    # of concurrency, and even when later batches complete first. No real Gemini API is hit.
    from rag import embeddings as E

    monkeypatch.setattr(E, "_report_usage", lambda *a, **k: None)  # no telemetry task/network

    def _fake_vec(text: str) -> list[float]:
        n = int(text.rsplit("-", 1)[1])           # encode the text's identity positionally
        v = [0.0] * 8
        v[n % 8] = float(n + 1)
        return v

    class _Emb:
        def __init__(self, values): self.values = values

    class _Resp:
        def __init__(self, embs): self.embeddings = embs

    class _Models:
        def embed_content(self, *, model, contents, config):
            import time
            texts = [c if isinstance(c, str) else c.parts[0].text for c in contents]
            # scramble completion order: earlier batches (lower index) sleep LONGER, so under
            # concurrency they finish LAST — exercising the positional re-sort.
            time.sleep(0.01 * (10 - int(texts[0].rsplit("-", 1)[1])))
            return _Resp([_Emb(_fake_vec(t)) for t in texts])

    class _Client:
        models = _Models()

    def _make():
        emb = E.GeminiEmbedder.__new__(E.GeminiEmbedder)  # bypass __init__ (no genai.Client / key)
        emb.model = "gemini-embedding-001"  # task_type path → contents stay plain strings
        emb.dim = 8
        emb._prompt_task = False
        emb._client = _Client()
        return emb

    texts = [f"text-{i}" for i in range(5)]
    monkeypatch.setattr(E.settings, "embed_batch", 2)     # 5 texts → 3 sub-batches
    expected = [E._normalize(_fake_vec(t)) for t in texts]
    for conc in (4, 1):                                    # concurrent AND the =1 rollback path
        monkeypatch.setattr(E.settings, "embed_concurrency", conc)
        out = await _make().embed(texts)
        assert out == expected, f"positional order broke at concurrency={conc}"
