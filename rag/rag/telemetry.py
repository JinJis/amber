"""HI-5: shared telemetry client + poster for rag cost-usage pings (embeddings + rerank).

Both fire a best-effort, detached POST to control-plane ``/admin/llm-usage`` per call — a fresh
AsyncClient (new pool + TLS handshake) each time was ~one per embed/rerank on the hot search path.
One shared client amortizes it. Failures never touch the search/ingest path.
"""

from __future__ import annotations

import asyncio

import httpx

from rag.config import settings

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=3.0)
    return _client


def report_usage(payload: dict) -> None:
    """Fire-and-forget POST of one usage row; swallows everything (incl. no running loop).

    METER-3: attributes the row to the current search request's project_id (embed/rerank/multi-query
    on the user-facing path) when the caller didn't set one; None (background ingest) = shared cost.
    """
    from rag.usage_context import current_project
    payload.setdefault("project_id", current_project())

    async def _post() -> None:
        try:
            await _get_client().post(
                f"{settings.control_plane_url}/admin/llm-usage",
                json=payload, headers={"X-Admin-Token": settings.admin_token})
        except Exception:  # noqa: BLE001 — telemetry never fails a search/ingest
            pass

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:  # no running loop (sync tests)
        pass
