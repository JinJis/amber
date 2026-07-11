"""Ingest documents -> chunks (with provenance) -> embeddings -> vector store."""

from __future__ import annotations

from rag.chunk import chunk_text
from rag.embeddings import get_embedder
from rag.models import Chunk, IngestDoc, doc_to_chunks
from rag.store import get_store


async def ingest_docs(docs: list[IngestDoc], replace: dict | None = None) -> dict:
    """Chunk → embed → atomic swap. Returns ``{"chunks", "pruned", "skipped"}`` (chunks =
    (re)embedded count). Incremental: chunks already stored with identical text are skipped,
    so re-running a pipeline over unchanged filings/news (the weekly sweep) costs no embeddings.

    ING-1 order — the swap is atomic and the delete never precedes the embed:
      chunk → existing_texts (BEFORE any delete, so the skip fires even with `replace`)
            → embed only the new/changed chunks (no store mutation yet)
            → ONE store op: replace_scope prunes stale ids for the scope + upserts the new set.

    ``replace`` (e.g. {"accession": "..."}) scopes the prune. A re-chunked filing's old sections
    are removed and the fresh set inserted in a single transaction, so retrieval only ever sees
    the complete old set or the complete new set — never a half-swapped (truncated) filing."""
    chunks: list[Chunk] = []
    for doc in docs:
        chunks.extend(doc_to_chunks(doc, chunk_text(doc.text)))
    store = get_store()
    # Never delete on an empty extraction — honesty over blowing away a good prior ingest.
    if not chunks:
        return {"chunks": 0, "pruned": 0, "skipped": 0}
    existing = await store.existing_texts([c.id for c in chunks])
    todo = [c for c in chunks if existing.get(c.id) != c.text]  # new id OR changed text
    skipped = len(chunks) - len(todo)
    # Embed BEFORE mutating the store — a failure here leaves the corpus untouched (atomicity).
    vectors = await get_embedder().embed([c.text for c in todo]) if todo else []
    if replace:
        # keep-set = ALL new chunk ids (not just `todo`) so unchanged chunks survive the prune;
        # runs even when `todo` is empty, to drop stale ids an older chunking left behind.
        pruned = await store.replace_scope(replace, [c.id for c in chunks], todo, vectors)
    else:
        if todo:
            await store.upsert(todo, vectors)
        pruned = 0
    return {"chunks": len(todo), "pruned": pruned, "skipped": skipped}
