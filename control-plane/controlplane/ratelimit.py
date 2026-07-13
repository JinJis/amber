"""Per-key rate limiting.

In-memory fixed-window counter by default. SC-2.4/HI-2: when ``REDIS_URL`` is set the counter moves
to Redis (atomic INCR per windowed key) so a horizontally-scaled gateway enforces ONE limit across
all replicas instead of N× (each replica otherwise counts independently → the abuse backstop is
effectively multiplied by the replica count). Redis unset/unreachable → the in-process counter still
applies as a per-replica backstop.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("controlplane.ratelimit")


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._buckets: dict[tuple[str, int], int] = {}

    def allow(self, key: str, limit: int | None = None) -> bool:
        """PLAN-2: ``limit`` overrides the global default for this call — the gateway passes
        the caller project's plan-tier rate so free/guest/pro get different backstops.
        In-process fixed-window counter (per replica)."""
        window = int(time.time() // 60)
        # opportunistic cleanup of old windows
        if len(self._buckets) > 10000:
            self._buckets = {k: v for k, v in self._buckets.items() if k[1] >= window}
        bkey = (key, window)
        self._buckets[bkey] = self._buckets.get(bkey, 0) + 1
        return self._buckets[bkey] <= (limit if limit is not None else self.per_minute)

    async def allow_async(self, key: str, limit: int | None = None) -> bool:
        """Redis-backed fixed-window when ``REDIS_URL`` is set (shared across replicas), else the
        in-process counter. On any Redis error, fall back to the in-process backstop (never fail open
        completely)."""
        from controlplane import redisstate

        r = redisstate.client()
        if r is None:
            return self.allow(key, limit)
        cap = limit if limit is not None else self.per_minute
        window = int(time.time() // 60)
        rkey = f"rl:{key}:{window}"
        try:
            n = await r.incr(rkey)
            if n == 1:
                await r.expire(rkey, 120)  # 2 windows of slack, then auto-cleanup
            return int(n) <= cap
        except Exception as exc:  # noqa: BLE001 — Redis down → per-replica in-process backstop
            log.warning("rate-limit Redis error (%s) — falling back to in-process", exc)
            return self.allow(key, limit)
