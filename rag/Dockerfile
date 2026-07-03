# RAG service image. Embeddings are Gemini-only (google-genai, a core dep) — the embedder calls
# gemini-embedding-2 via the Gemini API with GOOGLE_API_KEY (injected from the shared .env). No
# local model download, no torch.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev --extra pg --extra gcp

COPY rag ./rag
RUN uv sync --frozen --no-dev --extra pg --extra gcp

EXPOSE 8002

CMD ["uvicorn", "rag.main:app", "--host", "0.0.0.0", "--port", "8002"]
