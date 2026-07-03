"""HTTP helpers for the admin's read-only service probes (RF-14, split from main.py).

The admin fetches live status from datasets / rag / gateway / agent over httpx; ``_safe_get`` wraps
that with JSON/text fallback + error capture so a down service degrades to a flagged dict (``_error``
/ ``_status``) instead of throwing, and ``_ok`` tests for that flag.
"""

from __future__ import annotations

import httpx


async def _safe_get(client: httpx.AsyncClient, url: str) -> dict:
    try:
        r = await client.get(url, timeout=8)
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_status": r.status_code}
    except Exception as e:
        return {"_error": str(e)[:120]}


def _ok(d: dict) -> bool:
    return isinstance(d, dict) and "_error" not in d and "_status" not in d
