"""Optional Redis backend for cross-replica shared state (SC-2.4 / HI-2).

Off by default. With ``REDIS_URL`` unset every accessor returns ``None`` and callers keep their
in-process behavior (single-node correct). Set ``REDIS_URL`` to share state across replicas — the
rate limiter's fixed-window counter foremost, so N replicas enforce ONE limit instead of N×.

``redis`` is imported lazily so an image built before this landed still runs when ``REDIS_URL`` is
unset; a misconfigured/unreachable Redis degrades to in-process rather than failing requests.
"""

from __future__ import annotations

import logging

from controlplane.config import settings

log = logging.getLogger("controlplane.redisstate")

_client = None
_resolved = False


def client():
    """A cached ``redis.asyncio`` client when ``REDIS_URL`` is set, else ``None`` (lazy, no connection
    until first command — so boot never blocks on Redis)."""
    global _client, _resolved
    if _resolved:
        return _client
    _resolved = True
    url = (getattr(settings, "redis_url", "") or "").strip()
    if not url:
        _client = None
        return None
    try:
        import redis.asyncio as redis

        _client = redis.from_url(url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 — dep missing / bad URL → degrade to in-process
        log.warning("REDIS_URL set but Redis unavailable (%s) — using in-process state", exc)
        _client = None
    return _client


def reset() -> None:
    """Test hook: forget the cached client so a monkeypatched REDIS_URL is re-read."""
    global _client, _resolved
    _client = None
    _resolved = False
