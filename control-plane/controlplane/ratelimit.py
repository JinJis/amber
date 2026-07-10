"""Per-key rate limiting.

In-memory fixed-window counter (sufficient for single-process dev/staging). The
interface is backend-agnostic so a Redis-backed limiter swaps in later.
"""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._buckets: dict[tuple[str, int], int] = {}

    def allow(self, key: str, limit: int | None = None) -> bool:
        """PLAN-2: ``limit`` overrides the global default for this call — the gateway passes
        the caller project's plan-tier rate so free/guest/pro get different backstops."""
        window = int(time.time() // 60)
        # opportunistic cleanup of old windows
        if len(self._buckets) > 10000:
            self._buckets = {k: v for k, v in self._buckets.items() if k[1] >= window}
        bkey = (key, window)
        self._buckets[bkey] = self._buckets.get(bkey, 0) + 1
        return self._buckets[bkey] <= (limit if limit is not None else self.per_minute)
