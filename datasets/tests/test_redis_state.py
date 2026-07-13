"""SC-2.4 (2/2): the OpenDART daily-quota key blocks, the per-provider circuit breaker, and the news
dedup window share state across replicas when REDIS_URL is set. fakeredis stands in — sync for the
block pool (sync call sites), async for the breaker + dedup (async call sites)."""

from __future__ import annotations

import fakeredis
import fakeredis.aioredis

from app import http, redisstate
from app.models.generated import News
from app.providers import news as newsmod
from app.providers.kr import opendart as od
from app.symbols import Market


def test_opendart_blocks_shared_via_redis(monkeypatch):
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(redisstate, "sync_client", lambda: fake)
    monkeypatch.setattr(od.settings, "opendart_api_keys", "k1,k2,k3", raising=False)
    monkeypatch.setattr(od.settings, "opendart_api_key", "")
    od.reset_quota_blocks()

    assert od.available_keys() == ["k1", "k2", "k3"]
    od.mark_quota_blocked("k1")                    # spent on some replica
    assert od.available_keys() == ["k2", "k3"]     # every replica now rotates off k1
    assert od.quota_blocked() is False
    od.mark_quota_blocked("k2")
    od.mark_quota_blocked("k3")
    assert od.quota_blocked() is True              # whole pool spent → fail fast everywhere
    od.reset_quota_blocks()
    assert od.available_keys() == ["k1", "k2", "k3"]


async def test_breaker_shared_via_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redisstate, "client", lambda: fake)

    assert await http._breaker_open("prov") is False
    for _ in range(http._BREAK_AFTER):             # consecutive failures across the fleet
        await http._breaker_note("prov", ok=False)
    assert await http._breaker_open("prov") is True    # tripped once, visible to all replicas
    await http._breaker_note("prov", ok=True)          # a success clears it everywhere
    assert await http._breaker_open("prov") is False


async def test_breaker_falls_back_to_in_process_without_redis(monkeypatch):
    monkeypatch.setattr(redisstate, "client", lambda: None)
    prov = "prov_local_x"
    http._breaker.pop(prov, None)
    assert await http._breaker_open(prov) is False
    for _ in range(http._BREAK_AFTER):
        await http._breaker_note(prov, ok=False)
    assert await http._breaker_open(prov) is True
    http._breaker.pop(prov, None)


async def test_news_dedup_shared_via_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redisstate, "client", lambda: fake)
    calls = {"n": 0}

    async def fake_fetch(self, market, ticker, limit):
        calls["n"] += 1
        return [News(ticker=ticker, title="t", source="s", url="http://x.test/a")]

    monkeypatch.setattr(newsmod.AutoNewsProvider, "_fetch", fake_fetch)
    newsmod._news_cache.clear()

    p = newsmod.AutoNewsProvider()
    r1 = await p.news(Market.US, "AAPL", 5)
    newsmod._news_cache.clear()          # simulate a second replica's empty local store (Redis persists)
    r2 = await p.news(Market.US, "AAPL", 5)

    assert calls["n"] == 1               # cross-replica: the upstream was hit once
    assert len(r1) == len(r2) == 1
    assert r2[0].title == "t" and str(r2[0].url).startswith("http://x.test/a")   # serde round-trip
