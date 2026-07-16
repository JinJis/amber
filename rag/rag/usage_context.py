"""METER-3 — per-request cost-attribution context for rag.

The gateway injects the caller's authenticated control-plane **project_id** as the ``X-Tenant-Id``
header on every RAG request (control-plane ``gateway._resolve_entitlement``). The ``/rag/search``
endpoint stamps it into this contextvar so ``telemetry.report_usage`` attributes the turn's
embed / rerank / multi-query token usage to the user who triggered it — instead of the anonymous
NULL 공용 bucket. Background ingest (the worker, no gateway) leaves it None = shared cost. Being a
contextvar, it never mixes across concurrent search requests.
"""

from __future__ import annotations

from contextvars import ContextVar

_project_id: ContextVar[str | None] = ContextVar("rag_project_id", default=None)


def set_project(project_id: str | None) -> None:
    _project_id.set(project_id or None)


def current_project() -> str | None:
    return _project_id.get()
