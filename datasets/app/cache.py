"""A tiny async TTL cache.

In-memory by default (sufficient for a single-process dev/staging deployment).
``redis_url`` is reserved for a future shared-cache backend; the interface below
is intentionally backend-agnostic so swapping it in later touches only this file.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

from app.config import settings

T = TypeVar("T")


class TTLCache:
    """Async TTL cache with **single-flight per key**: under concurrent cold-cache access, the
    factory runs exactly once for a key (others await its result) — different keys still load in
    parallel. This makes it the one upstream-loader cache: cheap fetches (SEC ticker index, DART
    corp map) avoid a thundering herd, and rate-limited issuers (the KIS OAuth token, ~1/min) can
    rely on it instead of hand-rolling a lock (RF-06)."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}
        self._lock = asyncio.Lock()                       # guards _store + _inflight
        self._inflight: dict[str, asyncio.Lock] = {}      # per-key single-flight locks

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            hit = self._store.get(key)
            if hit is not None and hit[0] > time.monotonic():
                return hit[1]  # type: ignore[return-value]
            keylock = self._inflight.setdefault(key, asyncio.Lock())
        async with keylock:
            # double-check: another caller may have populated the key while we waited for keylock
            async with self._lock:
                hit = self._store.get(key)
                if hit is not None and hit[0] > time.monotonic():
                    return hit[1]  # type: ignore[return-value]
            value = await factory()  # only one caller per key reaches here; failures aren't cached
            async with self._lock:
                self._store[key] = (time.monotonic() + self._ttl, value)
            return value

    def clear(self) -> None:
        self._store.clear()
        self._inflight.clear()


cache = TTLCache(settings.cache_ttl_seconds)
