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


def _tokens(resp) -> dict | None:
    """The billable totals (unchanged) + informational breakdowns from usage_metadata.

    input/output stay authoritative: input = prompt_token_count (the full request input; cached and
    tool-use prompt tokens are already inside it — we do NOT re-add them, to avoid double counting),
    output = candidates + thoughts (thinking is billed at the output rate). The breakdowns are stored
    separately so the dashboard can apply the cache discount and show thinking/tool overhead.
    """
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return None
    thoughts = int(getattr(um, "thoughts_token_count", None) or 0)
    made_in = int(getattr(um, "prompt_token_count", None) or 0)
    made_out = int(getattr(um, "candidates_token_count", None) or 0) + thoughts
    if not made_in and not made_out:
        return None
    return {
        "input_tokens": made_in,
        "output_tokens": made_out,
        "cached_input_tokens": int(getattr(um, "cached_content_token_count", None) or 0),
        "tool_input_tokens": int(getattr(um, "tool_use_prompt_token_count", None) or 0),
        "thinking_tokens": thoughts,
    }


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
    from agentengine.usage_context import current_project
    # COST-2: prefer the RESOLVED model (what `gemini-flash-latest` actually served, e.g.
    # gemini-2.5-flash) so the cost dashboard prices the real model, not the ambiguous alias.
    resolved = getattr(resp, "model_version", None) or model or "unknown"
    payload = {"service": "agent-engine", "kind": kind, "model": resolved,
               **got,   # input/output + cached/tool/thinking breakdowns
               "project_id": current_project()}   # METER-1: 유저별 원가 귀속 (없으면 None=공용)
    try:
        asyncio.get_running_loop().create_task(_post(payload))
    except RuntimeError:  # no running loop (sync context) — skip rather than block
        pass
