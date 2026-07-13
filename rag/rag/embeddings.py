"""Gemini embeddings (Google) — the ONLY embedding backend.

Uses the Gemini API with ``GOOGLE_API_KEY`` (the same key the agent uses — no service account /
project / Vertex needed). Documents and queries are embedded asymmetrically (a real retrieval-
quality win) and L2-normalized for cosine search.

Model-aware (per https://ai.google.dev/gemini-api/docs/embeddings):
  * ``gemini-embedding-2`` (default, latest) — task goes in the PROMPT, and a plain list of strings
    aggregates to ONE vector, so each text is wrapped in a ``Content`` for separate embeddings.
  * ``gemini-embedding-001`` (stable, text-only) — uses the ``task_type`` config field; a plain list
    already yields one vector per text.
The legacy hash / fastembed / sentence-transformers / TEI backends were removed.
"""

from __future__ import annotations

import asyncio
import math
from functools import cache
from typing import Protocol

from rag.config import settings

# Transient Gemini API failures self-heal instead of failing the whole ingest request —
# a single 503 UNAVAILABLE was surfacing as a 500 to callers (filing_text pipeline dropped
# a ticker over it in the 2026-07 full-pipeline audit). Retry 429 + 5xx with backoff.
_RETRYABLE = {429, 500, 502, 503, 504}
_RETRIES = 3
_BACKOFF_SECONDS = 2.0  # 2 → 4 → 8


def _with_retry(call):
    """Run a sync Gemini call, retrying transient APIErrors (runs inside asyncio.to_thread,
    so time.sleep backoff is fine)."""
    import logging
    import time

    from google.genai import errors

    for attempt in range(_RETRIES + 1):
        try:
            return call()
        except errors.APIError as exc:
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if attempt >= _RETRIES or code not in _RETRYABLE:
                raise
            wait = _BACKOFF_SECONDS * (2 ** attempt)
            logging.getLogger(__name__).warning(
                "gemini embed transient %s — retry %d/%d in %.0fs", code, attempt + 1, _RETRIES, wait)
            time.sleep(wait)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class Embedder(Protocol):
    dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...   # documents (the corpus)
    async def embed_query(self, text: str) -> list[float]: ...          # a single search query


class GeminiEmbedder:
    """Google Gemini embeddings via the Gemini API (``GOOGLE_API_KEY``). ``output_dimensionality``
    is MRL-truncated to ``embedding_dim`` and re-normalized."""

    def __init__(self) -> None:
        from google import genai

        self._client = genai.Client()  # Gemini API — reads GOOGLE_API_KEY from the environment
        self.model = settings.embedding_model or "gemini-embedding-2"
        self.dim = settings.embedding_dim
        # gemini-embedding-2 puts the task in the prompt + needs Content-wrapping for batches;
        # the older -001 takes a `task_type` field and batches a plain string list directly.
        self._prompt_task = self.model.startswith("gemini-embedding-2")

    async def _embed(self, texts: list[str], *, query: bool) -> list[list[float]]:
        import logging
        import time

        from google.genai import types

        if not texts:
            return []
        batch_size = max(1, settings.embed_batch)
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        # ING-1: embed sub-batches CONCURRENTLY (bounded) instead of sequentially — one large
        # filing's ~N 64-text calls used to run back-to-back (measured ~109s/call under load →
        # a single ingest exceeding any sane client budget). `=1` restores the sequential path.
        sem = asyncio.Semaphore(max(1, settings.embed_concurrency))

        def _run(b: list[str], idx: int) -> list[list[float]]:
            if self._prompt_task:
                # task in the prompt; wrap each text so they embed SEPARATELY (not aggregated)
                instr = "task: search result | query: " if query else "task: search result | document: "
                contents = [types.Content(parts=[types.Part(text=instr + t)]) for t in b]
                cfg = types.EmbedContentConfig(output_dimensionality=self.dim or None)
            else:
                contents = b
                cfg = types.EmbedContentConfig(
                    task_type="RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT",
                    output_dimensionality=self.dim or None)
            t0 = time.perf_counter()
            resp = _with_retry(lambda: self._client.models.embed_content(
                model=self.model, contents=contents, config=cfg))
            logging.getLogger(__name__).info(
                "embed batch %d/%d size=%d %.0fms", idx + 1, len(batches), len(b),
                (time.perf_counter() - t0) * 1000)
            return [list(e.values) for e in resp.embeddings]

        async def _one(idx: int, b: list[str]) -> tuple[int, list[list[float]]]:
            async with sem:
                return idx, await asyncio.to_thread(_run, b, idx)

        results = await asyncio.gather(*(_one(i, b) for i, b in enumerate(batches)))
        out: list[list[float]] = []
        for _, vecs in sorted(results, key=lambda r: r[0]):  # positional order preserved
            out.extend(_normalize(v) for v in vecs)
        if out:
            self.dim = len(out[0])
        _report_usage(self.model, texts, query=query)
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed corpus chunks for storage (document side)."""
        return await self._embed(texts, query=False) if texts else []

    async def embed_query(self, text: str) -> list[float]:
        """Embed one search query (asymmetric to the documents for better recall)."""
        return (await self._embed([text], query=True))[0]


@cache
def get_embedder() -> Embedder:
    return GeminiEmbedder()


# --- COST-1: embedding usage telemetry (best-effort, detached) --------------------------------
# The embed API returns no usage metadata, so tokens are ESTIMATED (~4 chars/token) and flagged
# `estimated` — the cost dashboard labels them as such (never presented as an exact figure).
def _report_usage(model: str, texts: list[str], *, query: bool) -> None:
    try:
        from rag.telemetry import report_usage   # HI-5: shared telemetry client

        chars = sum(len(t or "") for t in texts)
        if not chars:
            return
        report_usage({"service": "rag", "kind": "embed_query" if query else "embed_docs",
                      "model": model, "input_tokens": max(1, chars // 4), "output_tokens": 0,
                      "calls": 1, "estimated": True})
    except Exception:  # noqa: BLE001 — telemetry never fails a search/ingest
        pass
