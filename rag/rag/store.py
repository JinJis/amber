"""Pluggable vector store, selected by RAG_VECTOR_STORE.

* memory   — numpy cosine over an in-process list (dev/CI; vectors are normalized)
* pgvector — Postgres + pgvector (prod; managed via Cloud SQL / AlloyDB)
"""

from __future__ import annotations

import asyncio
import json
from functools import cache
from typing import Protocol

import numpy as np

from rag.config import settings
from rag.models import PROVENANCE_FIELDS, Chunk


class VectorStore(Protocol):
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    async def search(self, vector: list[float], top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        """Top-k by cosine similarity, honoring `filters`. Filter semantics every backend must match
        (MemoryStore in Python, PgVectorStore in SQL): a `meta->>key == value` equality per filter
        key, EXCEPT `tenant`, which is isolation — a row matches iff its tenant equals the caller's
        OR is unscoped/global (None). (Keep `_match` and the pgvector WHERE in sync with this — RF-17.)"""
        ...
    async def existing_texts(self, ids: list[str]) -> dict[str, str]:
        """{id: stored_text} for ids already present — lets ingest skip re-embedding unchanged chunks."""
        ...


class MemoryStore:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._matrix: list[list[float]] = []
        self._pos: dict[str, int] = {}  # chunk.id → row index, for dedup (like pgvector's PK)

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        # UPSERT by chunk id — re-ingesting the same doc REPLACES its rows instead of piling up
        # duplicates (a re-run pipeline would otherwise flood the corpus, degrading retrieval).
        for c, v in zip(chunks, vectors):
            idx = self._pos.get(c.id)
            if idx is None:
                self._pos[c.id] = len(self._chunks)
                self._chunks.append(c)
                self._matrix.append(v)
            else:
                self._chunks[idx] = c
                self._matrix[idx] = v

    async def existing_texts(self, ids: list[str]) -> dict[str, str]:
        return {cid: self._chunks[self._pos[cid]].text for cid in ids if cid in self._pos}

    async def search(self, vector, top_k, filters=None):
        if not self._matrix:
            return []
        mat = np.asarray(self._matrix, dtype=np.float32)
        q = np.asarray(vector, dtype=np.float32)
        sims = mat @ q  # vectors are L2-normalized -> dot == cosine
        order = np.argsort(-sims)
        out: list[tuple[Chunk, float]] = []
        for i in order:
            chunk = self._chunks[int(i)]
            if filters and not _match(chunk, filters):
                continue
            out.append((chunk, float(sims[int(i)])))
            if len(out) >= top_k:
                break
        return out


class PgVectorStore:
    def __init__(self, dsn: str, dim: int) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        self._psycopg = psycopg
        self._register = register_vector
        self._dsn = dsn
        self._dim = dim
        # bootstrap on a RAW connection — register_vector() (in _connect) needs the `vector` type to
        # already exist, so the extension must be created first, before we ever register the adapter.
        with psycopg.connect(dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.commit()
        with self._connect() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS rag_chunks (id TEXT PRIMARY KEY, text TEXT, "
                f"meta JSONB, embedding vector({dim}))"
            )
            # HNSW ANN index for cosine — single-digit-ms search up to millions of vectors. Built
            # incrementally on insert. (pgvector caps HNSW at 2000 dims; our 1536 is well under.)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_hnsw ON rag_chunks "
                "USING hnsw (embedding vector_cosine_ops)"
            )
            conn.commit()

    def _connect(self):
        conn = self._psycopg.connect(self._dsn)
        self._register(conn)
        return conn

    async def upsert(self, chunks, vectors):
        # tenant lives in meta (reserved key) for filtering, but is excluded from
        # provenance() so it never surfaces in user-facing hits.
        def _meta(c):
            m = c.provenance()
            if c.tenant:
                m["tenant"] = c.tenant
            return json.dumps(m)

        rows = [(c.id, c.text, _meta(c), np.asarray(v, dtype=np.float32)) for c, v in zip(chunks, vectors)]

        def _run() -> None:
            with self._connect() as conn:
                conn.cursor().executemany(
                    "INSERT INTO rag_chunks (id, text, meta, embedding) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, meta=EXCLUDED.meta, embedding=EXCLUDED.embedding",
                    rows,
                )
                conn.commit()

        await asyncio.to_thread(_run)  # blocking psycopg off the event loop

    async def existing_texts(self, ids: list[str]) -> dict[str, str]:
        if not ids:
            return {}

        def _run():
            with self._connect() as conn:
                return conn.execute("SELECT id, text FROM rag_chunks WHERE id = ANY(%s)", (list(ids),)).fetchall()

        rows = await asyncio.to_thread(_run)
        return {cid: text for cid, text in rows}

    async def search(self, vector, top_k, filters=None):
        where, filter_params = "", []
        if filters:
            conds = []
            for k, val in filters.items():
                if k == "tenant":
                    # tenant isolation: own chunks OR global (unscoped) ones.
                    conds.append("(meta->>'tenant' = %s OR meta->>'tenant' IS NULL)")
                    filter_params.append(str(val))
                else:
                    conds.append("meta->>%s = %s")
                    filter_params.extend([k, str(val)])
            where = "WHERE " + " AND ".join(conds)
        sql = (
            "SELECT id, text, meta, 1 - (embedding <=> %s) AS score FROM rag_chunks "
            f"{where} ORDER BY embedding <=> %s LIMIT %s"
        )
        # pass the query vector as a numpy array — register_vector adapts ndarray → pgvector
        # `vector` (a plain list serializes as double precision[], which the <=> operator rejects).
        qv = np.asarray(vector, dtype=np.float32)
        args = [qv, *filter_params, qv, top_k]

        def _run():
            with self._connect() as conn:
                return conn.execute(sql, args).fetchall()

        rows = await asyncio.to_thread(_run)  # blocking psycopg off the event loop
        out = []
        for cid, text, meta, score in rows:
            meta = meta or {}
            out.append((Chunk(id=cid, text=text, **{k: meta.get(k) for k in PROVENANCE_FIELDS}), float(score)))
        return out


def _match(chunk: Chunk, filters: dict) -> bool:
    for k, v in filters.items():
        if k == "tenant":
            # tenant isolation: a tenant sees its own chunks AND global (unscoped) ones.
            if chunk.tenant is not None and chunk.tenant != v:
                return False
        elif getattr(chunk, k, None) != v:
            return False
    return True


@cache
def get_store() -> VectorStore:
    if settings.vector_store == "memory":
        return MemoryStore()
    if settings.vector_store == "pgvector":
        from rag.embeddings import get_embedder

        dim = get_embedder().dim or settings.embedding_dim
        return PgVectorStore(settings.database_url, dim)
    raise ValueError(f"Unknown RAG_VECTOR_STORE '{settings.vector_store}'.")
