# RAG Service

Provenance-first retrieval over the platform's documents (filings, news, transcripts). The pipeline —
**structure-aware chunk → embed → hybrid retrieve (dense ∪ lexical, RRF) → rerank** — keeps a
**provenance envelope on every chunk** (source, doc_type, ticker, as_of, url, section…), so retrieved
passages are citeable and consistent with the structured connector data.

**Embeddings are Gemini-only** — `gemini-embedding-2` via the Gemini API (`GOOGLE_API_KEY`), MRL-truncated
to `RAG_EMBEDDING_DIM` (1536) and L2-normalized; documents and queries embed asymmetrically. The legacy
hash / fastembed / sentence-transformers / TEI backends were removed.

**Hybrid retrieval (RQ-1).** Each query runs a dense leg (cosine over embeddings) AND a lexical leg
(Postgres FTS — a functional GIN index on `to_tsvector('simple', text)`, prefix tokens `tok:*` so Korean
particles match), fused with Reciprocal Rank Fusion over a wide `RAG_CANDIDATE_K` (40) candidate pool,
then reranked down to `RAG_TOP_K` (8). Exact identifiers (tickers, accession numbers, figures, Korean
names) stay retrievable even when the embedding misses them. The lexical leg and the reranker each fail
safe — an outage in either degrades to the other, never to a failed query.

**Structure-aware chunking (RQ-2).** Chunks split on sentence boundaries (never mid-sentence), carry
their enclosing heading as an `[Item 1A. Risk Factors]` prefix, keep table rows atomic (`cell | cell`),
and preserve speaker turns whole in transcripts. Filing/DART sections break on real headings (Item N /
제N장), so a hit points at a *named* region. Re-chunking replaces a document's chunks by `accession`
(delete-then-insert) so shifting section boundaries never orphan stale chunks.

Reranker (`RAG_RERANKER_BACKEND`): `none` · `gcp` (Vertex AI Ranking API). Vector store
(`RAG_VECTOR_STORE`): `memory` (dev/CI — numpy cosine + token-overlap lexical) · `pgvector` (prod —
HNSW ANN + GIN FTS).

## Run

```bash
cd rag
uv sync --extra dev                 # base (hash + memory) — runs anywhere
# pick a real backend:
#   uv sync --extra oss   && export RAG_EMBEDDING_BACKEND=oss-cpu
#   uv sync --extra gcp   && export RAG_EMBEDDING_BACKEND=gcp RAG_GCP_PROJECT=...
#   uv sync --extra st    && export RAG_EMBEDDING_BACKEND=oss-gpu
uv run uvicorn rag.main:app --reload --port 8002
uv run pytest -q
```

```bash
curl -X POST localhost:8002/rag/ingest -H 'Content-Type: application/json' -d '{
  "documents":[{"text":"Apple relies on a limited number of suppliers including TSMC...","source":"SEC EDGAR","doc_type":"10-K","ticker":"AAPL","url":"https://sec.gov/..."}]}'
curl -X POST localhost:8002/rag/search -H 'Content-Type: application/json' -d '{"query":"Apple chip suppliers","top_k":3}'
curl localhost:8002/rag/info     # which backends are active
```

Every hit returns `{text, score, provenance}` — provenance carries the source + as_of + url so agents cite it.
