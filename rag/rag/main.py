"""RAG service: provenance-first retrieval over the platform's documents.

Backends (embedding / reranker / vector store) are chosen by RAG_* env vars, so
the same service runs CPU-OSS, GCP (Vertex), or GPU without code changes.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from rag.config import assert_production_secrets, settings
from rag.ingest import ingest_docs
from rag.logging_config import install_request_logging, setup_logging
from rag.models import IngestRequest, PruneRequest, SearchRequest
from rag.search import search as run_search

setup_logging()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    assert_production_secrets()  # SC-0: production은 dev 기본 토큰/pg 비번으로 기동 불가
    # Warm the query embedder in the background: the OSS backends load their model lazily on
    # the FIRST search, which can exceed the gateway's HTTP timeout — that turn's rag__search
    # then 502'd and the answer silently lost its RAG evidence. Best-effort: a warmup failure
    # just means the first search pays the model load like before.
    async def _warm() -> None:
        try:
            from rag.embeddings import get_embedder

            await get_embedder().embed_query("warmup")
            logging.getLogger("rag").info("embedder warmed")
        except Exception as exc:  # noqa: BLE001 — warmup must never block startup
            logging.getLogger("rag").warning("embedder warmup skipped: %s", exc)

    task = asyncio.create_task(_warm())
    yield
    task.cancel()


app = FastAPI(
    title="Platform RAG", version="0.1.0",
    description="Provenance-first RAG with pluggable embedding/reranker/store backends.",
    lifespan=_lifespan,
)
install_request_logging(app)

# Header the gateway injects from the caller's authenticated key (control-plane
# project_id). Direct callers (admin ops, dev) omit it → docs stay unscoped/global.
_TENANT_HEADER = "x-tenant-id"


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/rag/info", tags=["RAG"], summary="Active backends")
async def info() -> dict:
    return {
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "reranker_backend": settings.reranker_backend,
        "vector_store": settings.vector_store,
    }


@app.post("/rag/ingest", tags=["RAG"], summary="Ingest documents (chunk + embed + store)")
async def ingest(body: IngestRequest, request: Request) -> dict:
    import logging
    import time

    # The gateway stamps the tenant from the caller's key; it's authoritative and
    # overrides anything a client put in the body (clients can't ingest for others).
    tenant = request.headers.get(_TENANT_HEADER)
    if tenant:
        for doc in body.documents:
            doc.tenant = tenant
    # ING-1: ALWAYS pin the tenant into the replace scope (including None → unscoped/global), so
    # a global re-ingest can never prune a tenant's same-accession rows and vice versa. The
    # prune keep-set is tenant-namespaced ids, so an unpinned scope would delete across tenants.
    replace = {**body.replace, "tenant": tenant} if body.replace else None
    t0 = time.perf_counter()
    res = await ingest_docs(body.documents, replace=replace)
    logging.getLogger(__name__).info(
        "ingest docs=%d embedded=%d skipped=%d pruned=%d replace=%s %.0fms",
        len(body.documents), res["chunks"], res["skipped"], res["pruned"],
        (replace or {}).get("accession") or bool(replace), (time.perf_counter() - t0) * 1000)
    return {"chunks": res["chunks"], "pruned": res["pruned"], "skipped": res["skipped"]}


@app.post("/rag/prune", tags=["RAG"], summary="Age-out delete: drop scoped chunks older than a cutoff")
async def prune(body: PruneRequest) -> dict:
    """ME-16: the news corpus has no retention (doc_id=url upserts never remove old rows), so old
    chunks live in the hybrid indexes forever. The datasets worker calls this daily with
    {filters: {"doc_type": "news"}, before_as_of: <cutoff>}. An empty filter is refused (never an
    unscoped purge)."""
    import logging

    from rag.store import get_store
    if not body.filters:
        return {"deleted": 0}
    n = await get_store().delete_older_than(body.filters, body.before_as_of)
    logging.getLogger(__name__).info("prune scope=%s before=%s → %d chunks", body.filters, body.before_as_of, n)
    return {"deleted": n}


@app.post("/rag/search", tags=["RAG"], summary="Retrieve passages with provenance")
async def search(body: SearchRequest, request: Request) -> dict:
    filters = {k: v for k, v in (("ticker", body.ticker), ("market", body.market),
                                 ("doc_type", body.doc_type)) if v}
    tenant = request.headers.get(_TENANT_HEADER)
    if tenant:
        filters["tenant"] = tenant
    hits = await run_search(body.query, body.top_k, filters)
    return {"hits": [h.model_dump() for h in hits]}
