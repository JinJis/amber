"""Ingest documents -> chunks (with provenance) -> embeddings -> vector store."""

from __future__ import annotations

from rag.chunk import chunk_text
from rag.embeddings import get_embedder
from rag.models import Chunk, IngestDoc, doc_to_chunks
from rag.store import get_store


async def ingest_docs(docs: list[IngestDoc], replace: dict | None = None) -> int:
    """Chunk → embed → upsert. Returns the number of chunks (re)embedded. Incremental: chunks
    already stored with identical text are skipped, so re-running a pipeline over unchanged
    filings/news (e.g. the weekly filing_text sweep) costs no embeddings.

    ``replace`` (e.g. {"accession": "..."}) deletes the matching chunks first — used when
    re-chunking changes section boundaries so stale per-section ids don't linger."""
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(doc_to_chunks(doc, chunk_text(doc.text)))
    store = get_store()
    # Only replace when the new chunk set is non-empty for that key — never blow away a good
    # prior ingest because this run happened to extract nothing (honesty over an empty overwrite).
    if replace and chunks:
        await store.delete_where(replace)
    if not chunks:
        return 0
    existing = await store.existing_texts([c.id for c in chunks])
    todo = [c for c in chunks if existing.get(c.id) != c.text]  # new id OR changed text
    if not todo:
        return 0
    vectors = await get_embedder().embed([c.text for c in todo])
    await store.upsert(todo, vectors)
    return len(todo)
