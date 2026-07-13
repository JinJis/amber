"""Shared async HTTP client + small fetch helpers used by providers.

IMP-2: transient upstream pushback (429/5xx — Yahoo rate limits foremost) is retried with
exponential backoff instead of failing the whole sweep on the first 503; a small per-provider
circuit breaker stops hammering an upstream that keeps refusing (fails fast until a cooldown
elapses). 4xx other than 429 (404 = ticker doesn't exist) never retries — it's an answer.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.config import settings
from app.errors import upstream_error

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_BACKOFFS = (0.5, 1.5, 3.5)          # ~3 retries, jittered by attempt order upstreamside
_BREAK_AFTER = 5                      # consecutive transient failures per provider →
_BREAK_SECONDS = 60.0                 # …fail fast for this long (no hammering)
_breaker: dict[str, list] = {}        # provider → [consecutive_failures, opened_at]


class _RateLimiter:
    """Token-spacing limiter: at most `rate_per_sec` calls/sec, awaited before each call. Async-safe
    (one lock), process-local. CR-9/ME-11: uniform per-provider client-side throttle — SEC EDGAR's
    ~10 req/s guideline foremost (a cold fan-out across CIKs otherwise gets the platform IP banned)."""

    def __init__(self, rate_per_sec: float) -> None:
        self.min_interval = 1.0 / max(0.001, rate_per_sec)
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            t = time.monotonic()
            wait = self._last + self.min_interval - t
            if wait > 0:
                await asyncio.sleep(wait)
                t += wait
            self._last = t


def _build_limiters() -> dict[str, _RateLimiter]:
    lim: dict[str, _RateLimiter] = {}
    if settings.sec_edgar_rate_per_sec > 0:
        lim["sec_edgar"] = _RateLimiter(settings.sec_edgar_rate_per_sec)
    if getattr(settings, "yahoo_rate_per_sec", 0) > 0:
        lim["yahoo"] = _RateLimiter(settings.yahoo_rate_per_sec)
    return lim


_limiters = _build_limiters()  # provider → limiter (absent = unthrottled)


async def _breaker_open(provider: str) -> bool:
    # SC-2.4/HI-2: share the breaker across replicas when REDIS_URL is set — otherwise each replica
    # independently burns _BREAK_AFTER failures against a down upstream before tripping (N× hammering).
    from app import redisstate
    r = redisstate.client()
    if r is not None:
        try:
            return bool(await r.exists(f"breaker:{provider}:open"))
        except Exception:  # noqa: BLE001 — redis down → per-replica in-process breaker
            pass
    st = _breaker.get(provider)
    if not st or st[0] < _BREAK_AFTER:
        return False
    if time.monotonic() - st[1] > _BREAK_SECONDS:
        _breaker.pop(provider, None)  # cooldown over — try again
        return False
    return True


async def _breaker_note(provider: str, ok: bool) -> None:
    from app import redisstate
    r = redisstate.client()
    if r is not None:
        try:
            if ok:
                await r.delete(f"breaker:{provider}:open", f"breaker:{provider}:fails")
            else:
                fails = await r.incr(f"breaker:{provider}:fails")
                await r.expire(f"breaker:{provider}:fails", int(_BREAK_SECONDS))  # stale failures decay
                if int(fails) >= _BREAK_AFTER:
                    await r.set(f"breaker:{provider}:open", "1", ex=int(_BREAK_SECONDS))
            return
        except Exception:  # noqa: BLE001 — redis down → in-process
            pass
    if ok:
        _breaker.pop(provider, None)
        return
    st = _breaker.setdefault(provider, [0, 0.0])
    st[0] += 1
    if st[0] >= _BREAK_AFTER:
        st[1] = time.monotonic()


async def _get_with_retry(provider: str, url: str, *, params: dict | None, headers: dict | None):
    """GET with bounded backoff on transient statuses + the provider circuit breaker."""
    if await _breaker_open(provider):
        raise upstream_error(provider, f"upstream cooling down after repeated errors (≤{int(_BREAK_SECONDS)}s) for {url}")
    limiter = _limiters.get(provider)   # CR-9/ME-11: per-provider client-side rate cap
    if limiter is not None:
        await limiter.acquire()
    last: httpx.Response | None = None
    for i, delay in enumerate((0.0,) + _BACKOFFS):
        if delay:
            await asyncio.sleep(delay)
        resp = await get_client().get(url, params=params, headers=headers)
        if resp.status_code not in _RETRY_STATUSES:
            await _breaker_note(provider, ok=resp.is_success)
            return resp
        last = resp
    await _breaker_note(provider, ok=False)
    return last

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
    return _client


async def fetch_json(
    provider: str, url: str, *, params: dict | None = None, headers: dict | None = None
) -> dict | list:
    try:
        resp = await _get_with_retry(provider, url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        raise upstream_error(provider, f"HTTP {exc.response.status_code} for {url}")
    except (httpx.HTTPError, ValueError) as exc:
        raise upstream_error(provider, str(exc))


async def fetch_text(
    provider: str, url: str, *, params: dict | None = None, headers: dict | None = None
) -> str:
    try:
        resp = await _get_with_retry(provider, url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as exc:
        raise upstream_error(provider, f"HTTP {exc.response.status_code} for {url}")
    except httpx.HTTPError as exc:
        raise upstream_error(provider, str(exc))


async def fetch_bytes(
    provider: str, url: str, *, params: dict | None = None, headers: dict | None = None
) -> bytes:
    # ME-11: route byte downloads (DART zips/docs) through the same retry + circuit breaker +
    # per-provider token bucket as fetch_json/fetch_text (previously a bare client.get → no retry).
    try:
        resp = await _get_with_retry(provider, url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as exc:
        raise upstream_error(provider, f"HTTP {exc.response.status_code} for {url}")
    except httpx.HTTPError as exc:
        raise upstream_error(provider, str(exc))
