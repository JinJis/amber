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
    async def replace_scope(self, filters: dict, keep_ids: list[str],
                            chunks: list[Chunk], vectors: list[list[float]]) -> int:
        """ONE atomic operation: delete rows matching `filters` whose id is NOT in `keep_ids`,
        then upsert `chunks`+`vectors` — the prune-stale swap. Same exact filter semantics as
        `delete_where` (`meta->>k = v`, None → unscoped). Returns the pruned row count. Lets a
        re-chunked filing replace its old sections atomically: retrieval only ever sees the
        complete old set or the complete new set, never a half-swapped (truncated) filing."""
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

    @staticmethod
    def _matches(c: Chunk, filters: dict) -> bool:
        """True iff the chunk matches EVERY exact filter (None → the field must be None)."""
        return all(getattr(c, k, None) == v for k, v in filters.items())

    def _upsert_one(self, c: Chunk, v: list[float]) -> None:
        idx = self._pos.get(c.id)
        if idx is None:
            self._pos[c.id] = len(self._chunks)
            self._chunks.append(c)
            self._matrix.append(v)
        else:
            self._chunks[idx] = c
            self._matrix[idx] = v

    async def delete_where(self, filters):
        kept = [(c, v) for c, v in zip(self._chunks, self._matrix) if not self._matches(c, filters)]
        removed = len(self._chunks) - len(kept)
        self._chunks = [c for c, _ in kept]
        self._matrix = [v for _, v in kept]
        self._pos = {c.id: i for i, c in enumerate(self._chunks)}
        return removed

    async def replace_scope(self, filters, keep_ids, chunks, vectors):
        # Synchronous body → atomic under asyncio (no await between mutations), the MemoryStore
        # analogue of the PgVectorStore single-transaction swap.
        keep = set(keep_ids)
        kept = [(c, v) for c, v in zip(self._chunks, self._matrix)
                if not (self._matches(c, filters) and c.id not in keep)]
        pruned = len(self._chunks) - len(kept)
        self._chunks = [c for c, _ in kept]
        self._matrix = [v for _, v in kept]
        self._pos = {c.id: i for i, c in enumerate(self._chunks)}
        for c, v in zip(chunks, vectors):
            self._upsert_one(c, v)
        return pruned


class PgVectorStore:
    def __init__(self, dsn: str, dim: int) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        from rag.config import settings

        self._psycopg = psycopg
        self._register = register_vector
        self._dsn = dsn
        self._dim = dim
        # CR-8: HNSW/timeout tuning applied per search transaction (set_config is_local=true).
        self._ef_search = settings.hnsw_ef_search or max(settings.candidate_k * 2, 100)
        self._iterative_scan = settings.hnsw_iterative_scan
        self._iter_scan_ok = bool(self._iterative_scan)  # flipped off if pgvector rejects it (pre-0.8)
        self._stmt_timeout_ms = settings.search_statement_timeout_ms
        self._trgm_enabled = settings.lexical_trgm_enabled
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
            # RQ-5/CR-8: the pg_trgm short-query leg is OFF by default (22s cold / 0 rows measured) —
            # only build its (multi-GB) index when the leg is actually enabled.
            if self._trgm_enabled:
                try:
                    conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                    conn.execute(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunks_trgm ON rag_chunks "
                        "USING gin (text gin_trgm_ops)"
                    )
                except Exception as exc:  # noqa: BLE001 — 미지원/권한 부족 → FTS 단독으로 동작
                    import logging
                    logging.getLogger(__name__).warning("rag_chunks_trgm index deferred: %s", exc)
            # ING-1: an expression index on meta->>'accession' so replace-by-accession
            # (delete_where / replace_scope) is an index scan, not a full-corpus seq scan —
            # per-accession prunes on an 800k+-row corpus are otherwise O(table) each.
            try:
                conn.execute(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunks_accession ON rag_chunks "
                    "((meta->>'accession'))"
                )
            except Exception as exc:  # noqa: BLE001 — a deferred/again build never blocks boot
                import logging
                logging.getLogger(__name__).warning("rag_chunks_accession index deferred: %s", exc)
            finally:
                conn.autocommit = False

        # CR-7: ONE pooled, vector-registered connection set for every data-plane operation, instead
        # of a fresh connect + register_vector per query (a single search fans out to ~6 legs).
        from psycopg_pool import ConnectionPool
        self._pool = ConnectionPool(
            dsn, min_size=settings.pg_pool_min_size, max_size=settings.pg_pool_max_size,
            configure=self._register, open=True, name="rag_pg",
        )

    def _connect(self):
        # RAW connection — used only for the __init__ bootstrap (extension + CONCURRENTLY index
        # builds that must run before the pool / outside a pooled transaction). Data-plane ops use
        # the pool below.
        conn = self._psycopg.connect(self._dsn)
        self._register(conn)
        return conn

    def _tune(self, conn) -> None:
        """CR-8: apply the search-path GUCs to the CURRENT transaction only (is_local=true), so they
        never leak to this pooled connection's next borrower. ef_search lifts the ANN candidate pool
        above LIMIT (default 40 under-returns behind post-filters); statement_timeout caps a runaway
        scan at the DB; iterative_scan keeps scanning under filters until LIMIT is filled (pgvector
        ≥0.8 — degrades gracefully if unsupported)."""
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(self._stmt_timeout_ms),))
        conn.execute("SELECT set_config('hnsw.ef_search', %s, true)", (str(self._ef_search),))
        if self._iter_scan_ok:
            try:
                conn.execute("SELECT set_config('hnsw.iterative_scan', %s, true)", (self._iterative_scan,))
            except Exception as exc:  # noqa: BLE001 — pre-0.8 pgvector has no such GUC → stop trying
                self._iter_scan_ok = False
                import logging
                logging.getLogger(__name__).warning("hnsw.iterative_scan unsupported, disabled: %s", exc)

    async def upsert(self, chunks, vectors):
        # tenant lives in meta (reserved key) for filtering, but is excluded from
        # provenance() so it never surfaces in user-facing hits (_rows_for handles it).
        rows = self._rows_for(chunks, vectors)

        def _run() -> None:
            with self._pool.connection() as conn:
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
            with self._pool.connection() as conn:
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
            with self._pool.connection() as conn:
                self._tune(conn)   # CR-8: ef_search + iterative_scan + statement_timeout (txn-local)
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
                with self._pool.connection() as conn:
                    self._tune(conn)   # CR-8: statement_timeout caps the OR-pass at the DB
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

        # RQ-5/CR-8: 이름형 짧은 쿼리(≤3토큰) 트라이그램 유사도 레그 — 기본 OFF(짧은 쿼리 22s/0행 측정).
        # RAG_LEXICAL_TRGM_ENABLED=true로 켜면 복원. dense 레그가 이름형 회수를 담당한다.
        if self._trgm_enabled and len(toks) <= 3 and len(query.strip()) >= 2:
            tsql = (
                "SELECT id, text, meta, similarity(text, %s) AS score FROM rag_chunks "
                f"WHERE text %% %s {('AND ' + where[len('WHERE '):]) if where else ''} "
                "ORDER BY score DESC LIMIT %s"
            )
            targs = [query, query, *filter_params, top_k]

            def _trun():
                with self._pool.connection() as conn:
                    self._tune(conn)
                    return conn.execute(tsql, targs).fetchall()

            try:
                trows = await asyncio.to_thread(_trun)
                seen = {c.id for c, _ in hits}
                hits += [(c, sc) for c, sc in self._rows_to_hits(trows) if c.id not in seen]
            except Exception:  # noqa: BLE001 — 확장 미설치 등 → FTS 결과만
                pass
        return hits[:top_k * 2]

    @staticmethod
    def _exact_conds(filters: dict) -> tuple[list[str], list]:
        """Exact-match SQL conditions for `delete_where`/`replace_scope` (NOT the search
        tenant-OR-NULL semantics): `meta->>k = v`, with None → `meta->>k IS NULL`."""
        conds, params = [], []
        for k, v in filters.items():
            if v is None:
                conds.append("meta->>%s IS NULL")
                params.append(k)
            else:
                conds.append("meta->>%s = %s")
                params.extend([k, str(v)])
        return conds, params

    async def delete_where(self, filters):
        conds, params = self._exact_conds(filters)
        if not conds:
            return 0
        sql = "DELETE FROM rag_chunks WHERE " + " AND ".join(conds)

        def _run():
            with self._pool.connection() as conn:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.rowcount or 0

        return await asyncio.to_thread(_run)

    def _rows_for(self, chunks, vectors):
        """(id, text, meta_json, vector) tuples for an executemany upsert — shared by
        `upsert` and `replace_scope`."""
        def _meta(c):
            m = c.provenance()
            if c.tenant:
                m["tenant"] = c.tenant
            return json.dumps(m)
        return [(c.id, c.text, _meta(c), np.asarray(v, dtype=np.float32))
                for c, v in zip(chunks, vectors)]

    async def replace_scope(self, filters, keep_ids, chunks, vectors):
        conds, params = self._exact_conds(filters)
        if not conds:
            # no scope to prune → never delete the whole table; just upsert the new chunks
            if chunks:
                await self.upsert(chunks, vectors)
            return 0
        # a stable per-scope advisory lock (signed 64-bit) serializes concurrent swaps of the
        # SAME scope (weekly sweep vs. on-demand ingest) so they can't interleave delete+insert.
        import hashlib
        scope_key = "&".join(f"{k}={filters[k]}" for k in sorted(filters))
        lock_id = int.from_bytes(hashlib.blake2b(scope_key.encode(), digest_size=8).digest(),
                                 "big", signed=True)
        del_sql = ("DELETE FROM rag_chunks WHERE " + " AND ".join(conds)
                   + " AND NOT (id = ANY(%s))")
        rows = self._rows_for(chunks, vectors)

        def _run():
            with self._pool.connection() as conn:
                # all in ONE transaction (lock → prune stale → upsert new → commit)
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
                pruned = conn.execute(del_sql, [*params, list(keep_ids)]).rowcount or 0
                if rows:
                    conn.cursor().executemany(
                        "INSERT INTO rag_chunks (id, text, meta, embedding) VALUES (%s,%s,%s,%s) "
                        "ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, meta=EXCLUDED.meta, "
                        "embedding=EXCLUDED.embedding",
                        rows,
                    )
                conn.commit()
                return pruned

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
