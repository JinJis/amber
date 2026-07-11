"""Pluggable vector store, selected by RAG_VECTOR_STORE.

* memory   — numpy cosine over an in-process list (dev/CI; vectors are normalized)
* pgvector — Postgres + pgvector (prod; managed via Cloud SQL / AlloyDB)

Hybrid retrieval (RQ-1): every backend exposes BOTH a dense ``search`` (cosine over embeddings)
and a ``lexical`` leg (keyword match — Postgres FTS with prefix tokens / token overlap in
memory). ``rag.search`` fuses the two with RRF, so exact identifiers (tickers, accession
numbers, Korean company names, figures) are retrievable even when the embedding misses them.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from functools import cache
from typing import Protocol

import numpy as np

from rag.config import settings
from rag.models import PROVENANCE_FIELDS, Chunk

# Shared query tokenizer for the lexical leg — alnum + 한글 runs, ≥2 chars, first 12 tokens.
# Both backends use the same tokens so memory (CI) and pgvector (prod) rank comparably.
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")


def lexical_tokens(query: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for t in _TOKEN.findall((query or "").lower()):
        if t not in seen:
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


class VectorStore(Protocol):
    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...
    async def search(self, vector: list[float], top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        """Top-k by cosine similarity, honoring `filters`. Filter semantics every backend must match
        (MemoryStore in Python, PgVectorStore in SQL): a `meta->>key == value` equality per filter
        key, EXCEPT `tenant`, which is isolation — a row matches iff its tenant equals the caller's
        OR is unscoped/global (None). (Keep `_match` and the pgvector WHERE in sync with this — RF-17.)"""
        ...
    async def lexical(self, query: str, top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        """Top-k by KEYWORD relevance (FTS/token overlap), same filter semantics as `search`."""
        ...
    async def existing_texts(self, ids: list[str]) -> dict[str, str]:
        """{id: stored_text} for ids already present — lets ingest skip re-embedding unchanged chunks."""
        ...
    async def delete_where(self, filters: dict) -> int:
        """Delete chunks whose meta equals every filter key (tenant semantics: exact value,
        including None → unscoped). Used by ingest's replace-by-accession so a re-chunked
        filing never piles up stale duplicates."""
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

    async def lexical(self, query, top_k, filters=None):
        # Token-overlap scoring with prefix matching (mirrors pgvector's `token:*` tsquery):
        # score = matched query tokens / sqrt(doc token count) — a cheap BM25-ish proxy.
        qtoks = lexical_tokens(query)
        if not qtoks:
            return []
        scored: list[tuple[Chunk, float]] = []
        for chunk in self._chunks:
            if filters and not _match(chunk, filters):
                continue
            dtoks = _TOKEN.findall(chunk.text.lower())
            if not dtoks:
                continue
            hits = sum(1 for qt in qtoks if any(dt.startswith(qt) for dt in dtoks))
            if hits:
                scored.append((chunk, hits / math.sqrt(len(dtoks))))
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    async def delete_where(self, filters):
        def _keep(c: Chunk) -> bool:
            for k, v in filters.items():
                if getattr(c, k, None) != v:
                    return True
            return False

        kept = [(c, v) for c, v in zip(self._chunks, self._matrix) if _keep(c)]
        removed = len(self._chunks) - len(kept)
        self._chunks = [c for c, _ in kept]
        self._matrix = [v for _, v in kept]
        self._pos = {c.id: i for i, c in enumerate(self._chunks)}
        return removed


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
            # RQ-1 hybrid: a FUNCTIONAL GIN index on to_tsvector(text) for the lexical leg.
            # A generated STORED column would rewrite the whole table under an ACCESS EXCLUSIVE
            # lock (unacceptable on a live multi-hundred-k-row corpus); a functional index adds
            # no column and, built CONCURRENTLY, never blocks reads/writes. 'simple' config —
            # the corpus is mixed KR/EN, so no language stemming; queries use prefix tokens
            # (`tok:*`) which handle Korean particles (삼성전자의 ← 삼성전자:*).
            conn.commit()  # CONCURRENTLY can't run inside a txn block
            conn.autocommit = True
            try:
                conn.execute(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunks_tsv ON rag_chunks "
                    "USING gin (to_tsvector('simple', coalesce(text, '')))"
                )
            except Exception as exc:  # noqa: BLE001 — a failed/again build shouldn't block boot;
                # search still works (seq FTS), and the next boot retries the index.
                import logging
                logging.getLogger(__name__).warning("rag_chunks_tsv index build deferred: %s", exc)
            # RQ-5: 한국어/이름형 짧은 쿼리용 트라이그램 레그 — 부분어·오탈자에 강함.
            # (to_tsvector 'simple'은 한글 형태소를 못 쪼개 회사명 부분 매칭이 약하다.)
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                conn.execute(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunks_trgm ON rag_chunks "
                    "USING gin (text gin_trgm_ops)"
                )
            except Exception as exc:  # noqa: BLE001 — 미지원/권한 부족 → FTS 단독으로 동작
                import logging
                logging.getLogger(__name__).warning("rag_chunks_trgm index deferred: %s", exc)
            finally:
                conn.autocommit = False

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
        where, filter_params = self._where(filters)
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
        return self._rows_to_hits(rows)

    async def lexical(self, query, top_k, filters=None):
        toks = lexical_tokens(query)
        if not toks:
            return []
        # Prefix tokens + ts_rank: graded keyword overlap (BM25-lite). Prefix (`:*`) makes bare
        # stems match Korean particle-suffixed tokens; the WHERE expression matches the
        # functional GIN index (to_tsvector('simple', text)) so the planner uses it.
        #
        # RQ-10 (latency): ranking is the cost — ts_rank re-parses each matching row's text, and
        # an OR of common tokens matches ~10% of a 1M-chunk corpus (measured 7.6s/leg). So:
        #   1) AND of all tokens first — precise, few rows, ~15ms;
        #   2) if that under-fills top_k, the OR pass ranks a BOUNDED candidate set
        #      (index-scan LIMIT) — recall backstop at a fixed cost (~0.8s), while the dense
        #      leg carries the semantic recall anyway.
        where, filter_params = self._where(filters)
        and_clause = ('AND ' + where[len('WHERE '):]) if where else ''

        def _ranked_sql() -> str:
            return (
                "SELECT id, text, meta, ts_rank(to_tsvector('simple', coalesce(text,'')), q) AS score "
                "FROM (SELECT id, text, meta FROM rag_chunks "
                "      WHERE to_tsvector('simple', coalesce(text,'')) @@ to_tsquery('simple', %s) "
                f"      {and_clause} LIMIT %s) c, "
                "     to_tsquery('simple', %s) q "
                "ORDER BY score DESC LIMIT %s"
            )

        def _run(tsquery: str, bound: int):
            def _q():
                with self._connect() as conn:
                    return conn.execute(_ranked_sql(),
                                        [tsquery, *filter_params, bound, tsquery, top_k]).fetchall()
            return _q

        from rag.config import settings as _settings
        bound = getattr(_settings, "lexical_candidate_limit", 4000)
        and_query = " & ".join(f"{t}:*" for t in toks)
        rows = await asyncio.to_thread(_run(and_query, bound))
        if len(rows) < top_k and len(toks) > 1:   # AND under-filled → bounded-OR recall pass
            or_query = " | ".join(f"{t}:*" for t in toks)
            rows = await asyncio.to_thread(_run(or_query, bound))
        hits = self._rows_to_hits(rows)

        # RQ-5: 이름형 짧은 쿼리(≤3토큰)는 트라이그램 유사도 레그 병행 — '삼전'·'하이닉스'류
        # 부분어가 FTS prefix를 비껴가는 경우를 회수. 인덱스(%% 연산자) 기반이라 저비용.
        if len(toks) <= 3 and len(query.strip()) >= 2:
            tsql = (
                "SELECT id, text, meta, similarity(text, %s) AS score FROM rag_chunks "
                f"WHERE text %% %s {('AND ' + where[len('WHERE '):]) if where else ''} "
                "ORDER BY score DESC LIMIT %s"
            )
            targs = [query, query, *filter_params, top_k]

            def _trun():
                with self._connect() as conn:
                    return conn.execute(tsql, targs).fetchall()

            try:
                trows = await asyncio.to_thread(_trun)
                seen = {c.id for c, _ in hits}
                hits += [(c, sc) for c, sc in self._rows_to_hits(trows) if c.id not in seen]
            except Exception:  # noqa: BLE001 — 확장 미설치 등 → FTS 결과만
                pass
        return hits[:top_k * 2]

    async def delete_where(self, filters):
        conds, params = [], []
        for k, v in filters.items():
            if v is None:
                conds.append("meta->>%s IS NULL")
                params.append(k)
            else:
                conds.append("meta->>%s = %s")
                params.extend([k, str(v)])
        if not conds:
            return 0
        sql = "DELETE FROM rag_chunks WHERE " + " AND ".join(conds)

        def _run():
            with self._connect() as conn:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount or 0

        return await asyncio.to_thread(_run)

    @staticmethod
    def _where(filters: dict | None) -> tuple[str, list]:
        if not filters:
            return "", []
        conds, params = [], []
        for k, val in filters.items():
            if k == "tenant":
                # tenant isolation: own chunks OR global (unscoped) ones.
                conds.append("(meta->>'tenant' = %s OR meta->>'tenant' IS NULL)")
                params.append(str(val))
            else:
                conds.append("meta->>%s = %s")
                params.extend([k, str(val)])
        return "WHERE " + " AND ".join(conds), params

    @staticmethod
    def _rows_to_hits(rows) -> list[tuple[Chunk, float]]:
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
