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

_BATCH = 64  # texts per embed_content request


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
        from google.genai import types

        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i : i + _BATCH]

            def _run(b: list[str] = batch) -> list[list[float]]:
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
                resp = self._client.models.embed_content(model=self.model, contents=contents, config=cfg)
                return [list(e.values) for e in resp.embeddings]

            vecs = await asyncio.to_thread(_run)
            out.extend(_normalize(v) for v in vecs)
        if out:
            self.dim = len(out[0])
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
