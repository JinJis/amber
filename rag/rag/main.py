"""RAG service: provenance-first retrieval over the platform's documents.

Backends (embedding / reranker / vector store) are chosen by RAG_* env vars, so
the same service runs CPU-OSS, GCP (Vertex), or GPU without code changes.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from rag.config import settings
from rag.ingest import ingest_docs
from rag.logging_config import install_request_logging, setup_logging
from rag.models import IngestRequest, SearchRequest
from rag.search import search as run_search

setup_logging()


@asynccontextmanager
async def _lifespan(app: FastAPI):
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
    # The gateway stamps the tenant from the caller's key; it's authoritative and
    # overrides anything a client put in the body (clients can't ingest for others).
    tenant = request.headers.get(_TENANT_HEADER)
    if tenant:
        for doc in body.documents:
            doc.tenant = tenant
    # scope the replace to the tenant so one tenant's re-ingest can't delete another's chunks.
    replace = {**body.replace, "tenant": tenant} if body.replace and tenant else body.replace
    n = await ingest_docs(body.documents, replace=replace)
    return {"chunks": n}


@app.post("/rag/search", tags=["RAG"], summary="Retrieve passages with provenance")
async def search(body: SearchRequest, request: Request) -> dict:
    filters = {k: v for k, v in (("ticker", body.ticker), ("market", body.market),
                                 ("doc_type", body.doc_type)) if v}
    tenant = request.headers.get(_TENANT_HEADER)
    if tenant:
        filters["tenant"] = tenant
    hits = await run_search(body.query, body.top_k, filters)
    return {"hits": [h.model_dump() for h in hits]}
