"""Optional Redis backend for cross-replica shared state (SC-2.4 / HI-2, HI-3).

Off by default. With ``REDIS_URL`` unset every accessor returns ``None`` and callers keep their
in-process behavior (single-node correct). Set ``REDIS_URL`` to share upstream-facing state across
replicas: the KIS OAuth token (KIS caps issuance ~1/min, so N replicas each issuing = hard failures),
the OpenDART daily-quota key blocks, the per-provider circuit breaker, the news dedup window.

``redis`` is an optional dependency (``pip install .[redis]``) imported lazily so an image built
before this landed still runs when ``REDIS_URL`` is unset; a misconfigured/unreachable Redis degrades
to in-process rather than failing requests.
"""

from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger("app.redisstate")

_client = None
_resolved = False
_sync_client = None
_sync_resolved = False


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


def sync_client():
    """A cached synchronous ``redis.Redis`` client when ``REDIS_URL`` is set, else ``None``. For the
    few sync call sites (OpenDART daily-quota key blocks) that have many sync callers + tests and
    would otherwise force an async ripple. The ops are tiny (MGET/SET) and infrequent (per DART call /
    only on a 020), so blocking the loop briefly is acceptable."""
    global _sync_client, _sync_resolved
    if _sync_resolved:
        return _sync_client
    _sync_resolved = True
    url = (getattr(settings, "redis_url", "") or "").strip()
    if not url:
        _sync_client = None
        return None
    try:
        import redis

        _sync_client = redis.from_url(url, decode_responses=True)
    except Exception as exc:  # noqa: BLE001 — dep missing / bad URL → degrade to in-process
        log.warning("REDIS_URL set but Redis unavailable (%s) — using in-process state", exc)
        _sync_client = None
    return _sync_client


def reset() -> None:
    """Test hook: forget the cached clients so a monkeypatched REDIS_URL is re-read."""
    global _client, _resolved, _sync_client, _sync_resolved
    _client = None
    _resolved = False
    _sync_client = None
    _sync_resolved = False
