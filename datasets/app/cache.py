"""A tiny async TTL cache.

In-memory by default (sufficient for a single-process dev/staging deployment). SC-2.4: an instance
can opt into a **cross-replica read-through** by passing ``redis_ns`` — when ``REDIS_URL`` is set the
value is also mirrored into Redis (JSON by default, or a custom ``dumps``/``loads`` pair for non-JSON
values), so replicas share it. The KIS OAuth token uses this (KIS caps issuance ~1/min); Redis
unset/unreachable → pure in-process, unchanged.
"""

from __future__ import annotations

import asyncio
import json
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
    growth), and the per-key single-flight locks are removed after use so they can't accumulate.

    SC-2.4: pass ``redis_ns`` to make the instance a cross-replica read-through — a cold miss consults
    Redis before running the factory, and a fresh value is written back with the same TTL, so an
    upstream token/registry is fetched once per cluster window rather than once per replica."""

    def __init__(self, ttl_seconds: int, max_entries: int | None = None, *,
                 redis_ns: str | None = None,
                 dumps: Callable[[object], str] | None = None,
                 loads: Callable[[str], object] | None = None) -> None:
        self._ttl = ttl_seconds
        self._max = max(1, max_entries if max_entries is not None
                        else getattr(settings, "cache_max_entries", 2000))
        self._store: "OrderedDict[str, tuple[float, object]]" = OrderedDict()  # LRU order
        self._lock = asyncio.Lock()                       # guards _store + _inflight
        self._inflight: dict[str, asyncio.Lock] = {}      # per-key single-flight locks
        self._redis_ns = redis_ns
        self._dumps = dumps or (lambda v: json.dumps(v))
        self._loads = loads or json.loads

    def _redis(self):
        """The Redis client iff this instance opted in (``redis_ns``) AND ``REDIS_URL`` is set."""
        if self._redis_ns is None:
            return None
        from app import redisstate
        return redisstate.client()

    async def _put_local(self, key: str, value: object, ttl: float) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + ttl, value)
            self._store.move_to_end(key)
            self._evict_locked()

    async def get_or_set(self, key: str, factory: Callable[[], Awaitable[T]],
                         ttl_seconds: int | None = None) -> T:
        """``ttl_seconds`` overrides the default TTL for this key — for slow-changing bulk
        loads (the DART corp registry changes ~daily; re-downloading the multi-MB zip every
        15 minutes wastes the OpenDART quota the evidence viewer also depends on)."""
        ttl = float(ttl_seconds or self._ttl)
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
                # SC-2.4: cross-replica read-through — a peer may already hold a fresh value
                r = self._redis()
                if r is not None:
                    try:
                        raw = await r.get(f"cache:{self._redis_ns}:{key}")
                        if raw is not None:
                            value = self._loads(raw)
                            await self._put_local(key, value, ttl)
                            return value  # type: ignore[return-value]
                    except Exception:  # noqa: BLE001 — Redis down → fall through to the factory
                        r = None
                value = await factory()  # only one caller per key reaches here; failures aren't cached
                await self._put_local(key, value, ttl)
                if r is not None:
                    try:
                        await r.set(f"cache:{self._redis_ns}:{key}", self._dumps(value), ex=int(ttl))
                    except Exception:  # noqa: BLE001 — mirror is best-effort; local copy still serves
                        pass
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

    async def invalidate(self, key: str) -> None:
        """Drop a key from the local store AND (if shared) Redis. SC-2.4: a token that died early
        (KIS 401 mid-TTL) must be re-issued cluster-wide, not just on the replica that noticed."""
        async with self._lock:
            self._store.pop(key, None)
        r = self._redis()
        if r is not None:
            try:
                await r.delete(f"cache:{self._redis_ns}:{key}")
            except Exception:  # noqa: BLE001 — best-effort; the local drop already forces a re-issue here
                pass

    def clear(self) -> None:
        self._store.clear()
        self._inflight.clear()


cache = TTLCache(settings.cache_ttl_seconds)
