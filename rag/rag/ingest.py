"""Ingest documents -> chunks (with provenance) -> embeddings -> vector store."""

from __future__ import annotations

from rag.chunk import chunk_text
from rag.embeddings import get_embedder
from rag.models import Chunk, IngestDoc, doc_to_chunks
from rag.store import get_store


async def ingest_docs(docs: list[IngestDoc]) -> int:
    """Chunk → embed → upsert. Returns the number of chunks (re)embedded. Incremental: chunks
    already stored with identical text are skipped, so re-running a pipeline over unchanged
    filings/news (e.g. the weekly filing_text sweep) costs no embeddings."""
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(doc_to_chunks(doc, chunk_text(doc.text)))
    if not chunks:
        return 0
    store = get_store()
    existing = await store.existing_texts([c.id for c in chunks])
    todo = [c for c in chunks if existing.get(c.id) != c.text]  # new id OR changed text
    if not todo:
        return 0
    vectors = await get_embedder().embed([c.text for c in todo])
    await store.upsert(todo, vectors)
    return len(todo)
