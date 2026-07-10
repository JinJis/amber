"""COST-1 — report every Gemini call's token usage to the control-plane metering store.

`report(kind, model, resp)` extracts ``resp.usage_metadata`` (prompt/candidates token counts) and
fire-and-forgets a POST to the control-plane admin API. Strictly best-effort: cost telemetry must
never slow down or fail a chat turn — errors are swallowed, the POST is detached, and when no
usage metadata is present nothing is sent (we never fabricate a figure — CLAUDE §2.6).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from agentengine.config import settings

log = logging.getLogger(__name__)


def _tokens(resp) -> tuple[int, int] | None:
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return None
    made_in = getattr(um, "prompt_token_count", None) or 0
    made_out = (getattr(um, "candidates_token_count", None) or 0) + (getattr(um, "thoughts_token_count", None) or 0)
    if not made_in and not made_out:
        return None
    return int(made_in), int(made_out)


async def _post(payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            await c.post(f"{settings.gateway_url}/admin/llm-usage", json=payload,
                         headers={"X-Admin-Token": settings.admin_token})
    except Exception:  # noqa: BLE001 — telemetry is never worth failing a turn
        pass


def report(kind: str, model: str, resp) -> None:
    """Record one call's usage (detached). Call right after a generate_content(+stream) returns —
    for streams, pass the LAST chunk (usage_metadata rides the final one)."""
    got = _tokens(resp)
    if not got:
        return
    payload = {"service": "agent-engine", "kind": kind, "model": model or "unknown",
               "input_tokens": got[0], "output_tokens": got[1]}
    try:
        asyncio.get_running_loop().create_task(_post(payload))
    except RuntimeError:  # no running loop (sync context) — skip rather than block
        pass
