"""SC-2.4 / HI-3: a TTLCache with ``redis_ns`` becomes a cross-replica read-through — the KIS OAuth
token (issuance ~1/min) is fetched once per cluster window instead of once per replica. fakeredis
stands in for a real server; two TTLCache instances model two datasets replicas."""

from __future__ import annotations

import fakeredis.aioredis

from app import redisstate
from app.cache import TTLCache


def _use_fake(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redisstate, "client", lambda: fake)
    return fake


async def test_read_through_shares_value_across_replicas(monkeypatch):
    _use_fake(monkeypatch)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return "TOKEN-abc"

    a = TTLCache(100, redis_ns="kis")
    b = TTLCache(100, redis_ns="kis")   # second replica, empty local store
    assert await a.get_or_set("kis:token", factory) == "TOKEN-abc"
    assert await b.get_or_set("kis:token", factory) == "TOKEN-abc"   # served from Redis
    assert calls["n"] == 1   # the factory (token issuance) ran once across the cluster


async def test_invalidate_forces_cluster_wide_reissue(monkeypatch):
    _use_fake(monkeypatch)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return f"TOKEN-{calls['n']}"

    a = TTLCache(100, redis_ns="kis")
    assert await a.get_or_set("kis:token", factory) == "TOKEN-1"
    await a.invalidate("kis:token")   # KIS 401 mid-TTL → drop it everywhere
    b = TTLCache(100, redis_ns="kis")   # a fresh replica must re-issue (Redis was cleared too)
    assert await b.get_or_set("kis:token", factory) == "TOKEN-2"
    assert calls["n"] == 2


async def test_without_redis_ns_is_pure_in_process(monkeypatch):
    _use_fake(monkeypatch)   # Redis available, but the instance did NOT opt in
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    a = TTLCache(100)   # no redis_ns → never touches Redis
    b = TTLCache(100)
    assert await a.get_or_set("k", factory) == 1
    assert await b.get_or_set("k", factory) == 2   # independent per-replica stores
    assert calls["n"] == 2


async def test_redis_down_falls_back_to_factory(monkeypatch):
    class _Broken:
        async def get(self, *_a, **_k):
            raise RuntimeError("redis down")

        async def set(self, *_a, **_k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(redisstate, "client", lambda: _Broken())
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return "V"

    a = TTLCache(100, redis_ns="kis")
    assert await a.get_or_set("k", factory) == "V"   # Redis GET raised → ran the factory
    assert calls["n"] == 1
