"""SC-2.4 / HI-2: the gateway rate limiter shares ONE fixed-window counter across replicas when
REDIS_URL is set (else per-replica in-process). fakeredis stands in for a real server; the two
RateLimiter instances model two gateway replicas pointed at the same Redis."""

from __future__ import annotations

import fakeredis.aioredis

from controlplane import ratelimit, redisstate


async def test_allow_async_shares_counter_across_replicas(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redisstate, "client", lambda: fake)
    a = ratelimit.RateLimiter(per_minute=100)   # replica A
    b = ratelimit.RateLimiter(per_minute=100)   # replica B — same Redis

    # cap of 3 across BOTH replicas: 3 allowed, the 4th denied regardless of which replica serves it
    verdicts = []
    for i in range(4):
        limiter = a if i % 2 == 0 else b
        verdicts.append(await limiter.allow_async("proj1", limit=3))
    assert verdicts == [True, True, True, False]

    # a different key has its own window
    assert await a.allow_async("proj2", limit=3) is True


async def test_allow_async_falls_back_to_in_process_without_redis(monkeypatch):
    monkeypatch.setattr(redisstate, "client", lambda: None)
    a = ratelimit.RateLimiter(per_minute=2)
    assert await a.allow_async("k") is True
    assert await a.allow_async("k") is True
    assert await a.allow_async("k") is False   # in-process counter still backstops


async def test_allow_async_degrades_when_redis_errors(monkeypatch):
    class _Broken:
        async def incr(self, *_a, **_k):
            raise RuntimeError("redis down")

    monkeypatch.setattr(redisstate, "client", lambda: _Broken())
    a = ratelimit.RateLimiter(per_minute=1)
    assert await a.allow_async("k") is True    # first call: in-process backstop allows
    assert await a.allow_async("k") is False   # second: in-process cap of 1 hit
