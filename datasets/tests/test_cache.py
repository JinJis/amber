"""Unit tests for the async TTL cache (`app.cache`) — the per-key ``ttl_seconds`` override.

Slow-changing bulk loads (the DART corp registry) cache for ~a day while everything else keeps
the short default; the override must expire on ITS deadline without touching other keys.
"""

from __future__ import annotations

import pytest

from app import cache as C


class _Clock:
    """Deterministic stand-in for the `time` module (only `monotonic` is used)."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


@pytest.mark.asyncio
async def test_ttl_override_expires_on_its_own_deadline(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(C, "time", clock)
    cache = C.TTLCache(ttl_seconds=900)
    calls = {"short": 0, "default": 0, "long": 0}

    def factory(key):
        async def load():
            calls[key] += 1
            return calls[key]
        return load

    assert await cache.get_or_set("short", factory("short"), ttl_seconds=60) == 1
    assert await cache.get_or_set("default", factory("default")) == 1
    assert await cache.get_or_set("long", factory("long"), ttl_seconds=86_400) == 1

    clock.now += 30      # inside every TTL → all cached
    assert await cache.get_or_set("short", factory("short"), ttl_seconds=60) == 1
    assert await cache.get_or_set("default", factory("default")) == 1

    clock.now += 31      # t+61s: past the 60s override, inside the 900s default
    assert await cache.get_or_set("short", factory("short"), ttl_seconds=60) == 2   # re-loaded
    assert await cache.get_or_set("default", factory("default")) == 1               # untouched

    clock.now += 900     # t+961s: past the default, far inside the day-long override
    assert await cache.get_or_set("default", factory("default")) == 2
    assert await cache.get_or_set("long", factory("long"), ttl_seconds=86_400) == 1
