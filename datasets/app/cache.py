"""A tiny async TTL cache.

In-memory by default (sufficient for a single-process dev/staging deployment).
``redis_url`` is reserved for a future shared-cache backend; the interface below
is intentionally backend-agnostic so swapping it in later touches only this file.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Awaitable, Callable, TypeVar

from app.config import settings

T = TypeVar("T")


class TTLCache:
    """Async TTL cache with **single-flight per key**: under concurrent cold-cache access, the
    factory runs exactly once for a key (others await its result) — different keys still load in
    parallel. This makes it the one upstream-loader cache: cheap fetches (SEC ticker index, DART
    corp map) avoid a thundering herd, and rate-limited issuers (the KIS OAuth token, ~1/min) can
    rely on it instead of hand-rolling a lock (RF-06).

    HI-4: bounded — expired entries are swept and the store is LRU-capped at ``max_entries`` (it
    previously only replaced expired entries on refresh and never dropped idle ones → unbounded
    growth), and the per-key single-flight locks are removed after use so they can't accumulate."""

    def __init__(self, ttl_seconds: int, max_entries: int | None = None) -> None:
        self._ttl = ttl_seconds
        self._max = max(1, max_entries if max_entries is not None
                        else getattr(settings, "cache_max_entries", 2000))
        self._store: "OrderedDict[str, tuple[float, object]]" = OrderedDict()  # LRU order
        self._lock = asyncio.Lock()                       # guards _store + _inflight
        self._inflight: dict[str, asyncio.Lock] = {}      # per-key single-flight locks

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]],
                         ttl_seconds: int | None = None) -> T:
        """``ttl_seconds`` overrides the default TTL for this key — for slow-changing bulk
        loads (the DART corp registry changes ~daily; re-downloading the multi-MB zip every
        15 minutes wastes the OpenDART quota the evidence viewer also depends on)."""
        async with self._lock:
            hit = self._store.get(key)
            if hit is not None and hit[0] > time.monotonic():
                self._store.move_to_end(key)   # LRU: mark most-recently-used
                return hit[1]  # type: ignore[return-value]
            keylock = self._inflight.setdefault(key, asyncio.Lock())
        async with keylock:
            try:
                # double-check: another caller may have populated the key while we held keylock
                async with self._lock:
                    hit = self._store.get(key)
                    if hit is not None and hit[0] > time.monotonic():
                        self._store.move_to_end(key)
                        return hit[1]  # type: ignore[return-value]
                value = await factory()  # only one caller per key reaches here; failures aren't cached
                async with self._lock:
                    self._store[key] = (time.monotonic() + (ttl_seconds or self._ttl), value)
                    self._store.move_to_end(key)
                    self._evict_locked()
                return value
            finally:
                # HI-4: drop the single-flight lock (success OR failure) so _inflight can't grow
                # unbounded. Safe: concurrent awaiters already hold their own reference to it.
                async with self._lock:
                    self._inflight.pop(key, None)

    def _evict_locked(self) -> None:
        """Under self._lock: only when over the ceiling, sweep expired entries then LRU-evict down
        to ``self._max`` — so the store never grows without bound (HI-4)."""
        if len(self._store) <= self._max:
            return
        now = time.monotonic()
        for k in [k for k, (exp, _) in self._store.items() if exp <= now]:
            self._store.pop(k, None)
        while len(self._store) > self._max:
            self._store.popitem(last=False)   # evict least-recently-used

    def clear(self) -> None:
        self._store.clear()
        self._inflight.clear()


cache = TTLCache(settings.cache_ttl_seconds)
