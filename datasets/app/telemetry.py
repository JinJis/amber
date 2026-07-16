"""COST-2 — datasets → control-plane cost-usage telemetry.

Datasets is a data plane behind the gateway, but paid MANAGED calls here (GCP Document AI, billed
per page) must surface on the admin cost dashboard. Mirror rag's best-effort, detached POST to the
control plane's ``/admin/llm-usage`` — one shared client, fire-and-forget, failures swallowed. This
telemetry never touches the ingest/serve path.
"""

from __future__ import annotations

import asyncio

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=3.0)
    return _client


def report_usage(payload: dict) -> None:
    """Fire-and-forget POST of one usage row; swallows everything (incl. no running loop)."""

    async def _post() -> None:
        try:
            await _get_client().post(
                f"{settings.control_plane_url}/admin/llm-usage",
                json=payload, headers={"X-Admin-Token": settings.admin_token})
        except Exception:  # noqa: BLE001 — telemetry never fails an ingest
            pass

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:  # no running loop (sync context) — skip rather than block
        pass


def report_provider_usage(counts: dict) -> None:
    """COST-3: batched background-sweep upstream call counts {provider: calls} → control-plane."""
    if not counts:
        return

    async def _post() -> None:
        try:
            await _get_client().post(
                f"{settings.control_plane_url}/admin/provider-usage",
                json={"providers": counts}, headers={"X-Admin-Token": settings.admin_token})
        except Exception:  # noqa: BLE001 — telemetry never fails a fetch
            pass

    try:
        asyncio.get_running_loop().create_task(_post())
    except RuntimeError:
        pass
