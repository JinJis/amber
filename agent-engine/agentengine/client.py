"""Platform client — fetch the tool catalog and call tools through the gateway.

Tool calls carry the tenant API key, so the control plane enforces entitlement +
metering + audit (the agent can only use what the tenant activated).
"""

from __future__ import annotations

import asyncio
import time

import httpx

from agentengine.config import settings

# HI-5: ONE shared client + connection pool for all gateway traffic (catalog + ~8 tool calls/turn)
# instead of a fresh AsyncClient (new pool + TLS handshake) per call.
_shared_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            limits=httpx.Limits(max_connections=settings.httpx_max_connections,
                                max_keepalive_connections=settings.httpx_max_keepalive))
    return _shared_client


# HI-5: /catalog is global (no tenant key) and near-static, yet re-fetched EVERY turn (with 3
# retries). Cache it process-wide for a short TTL — entitlement is still enforced per tool CALL.
_catalog_cache: tuple[float, dict[str, dict]] | None = None
_catalog_lock = asyncio.Lock()


def _reset_catalog_cache() -> None:
    """Test seam: drop the cached catalog (tests mock different catalogs per test)."""
    global _catalog_cache
    _catalog_cache = None


class PlatformClient:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    async def fetch_tools(self) -> dict[str, dict]:
        global _catalog_cache
        if _catalog_cache is not None and _catalog_cache[0] > time.monotonic():
            return _catalog_cache[1]
        async with _catalog_lock:
            if _catalog_cache is not None and _catalog_cache[0] > time.monotonic():
                return _catalog_cache[1]   # another caller populated it while we waited
            tools = await self._fetch_tools_uncached()
            _catalog_cache = (time.monotonic() + settings.catalog_cache_ttl_seconds, tools)
            return tools

    async def _fetch_tools_uncached(self) -> dict[str, dict]:
        last_exc: Exception | None = None
        connectors = None
        client = _client()
        for attempt in range(3):  # tolerate a transient gateway blip under load
            try:
                resp = await client.get(f"{settings.gateway_url}/catalog")
                resp.raise_for_status()
                connectors = resp.json().get("connectors", [])
                break
            except Exception as e:  # noqa: BLE001
                last_exc = e
                await asyncio.sleep(0.5 * (attempt + 1))
        if connectors is None:
            raise last_exc if last_exc else RuntimeError("catalog unavailable")
        tools: dict[str, dict] = {}
        for con in connectors:
            con_name = con.get("name") or con["id"]
            for res in con.get("resources", []):
                name = f"{con['id']}__{res['name']}"
                desc = (res.get("description") or "").rstrip(".")
                tools[name] = {
                    "name": name,
                    "connector": con["id"],
                    "connector_name": con_name,  # human-readable, e.g. "OpenDART (KR)"
                    # friendly label for the UI/answer instead of the raw `{con}__{res}` id
                    "friendly": f"{con_name} · {desc}" if desc else con_name,
                    "method": res.get("method", "GET").upper(),
                    "path": res["path"],
                    "params": res.get("params", []),
                    "markets": res.get("markets") or con.get("markets"),
                    "category": res.get("category"),  # user-facing group (market/macro/…)
                    "cadence": res.get("cadence"),  # periodicity class — gates the pin→alert flow
                    "description": res.get("description", ""),
                    "source": (res.get("provenance") or {}).get("source"),
                }
        return tools

    async def call_tool(self, tool: dict, args: dict) -> dict:
        args = dict(args or {})
        # The gateway routes by the `market` query param, so a single-market tool
        # (e.g. ECOS=KR, FRED=US) must carry its market or it can misroute to the
        # other market's connector. Force it when the connector serves one market.
        markets = tool.get("markets")
        if markets and len(markets) == 1 and any(p.get("name") == "market" for p in tool.get("params", [])):
            args["market"] = markets[0]
        headers = {"X-API-KEY": self.api_key} if self.api_key else {}
        url = f"{settings.gateway_url}{tool['path']}"
        # One retry on a TRANSIENT failure (502/503/504 or a transport error): our tools are
        # read-only data pulls, so a retry is safe — and a single gateway/upstream blip must
        # not cost the turn its evidence. Anything else returns as-is (honest status).
        resp = None
        client = _client()
        for attempt in range(2):
            try:
                if tool["method"] == "GET":
                    resp = await client.get(url, params=args, headers=headers)
                else:
                    resp = await client.request(tool["method"], url, json=args, headers=headers)
            except httpx.HTTPError:
                if attempt == 1:
                    raise
                await asyncio.sleep(1.0)
                continue
            if resp.status_code in (502, 503, 504) and attempt == 0:
                await asyncio.sleep(1.0)
                continue
            break
        assert resp is not None  # loop always sets or raises
        try:
            data = resp.json()
        except ValueError:
            data = resp.text[:1000]
        return {"status": resp.status_code, "connector": resp.headers.get("x-connector"), "data": data}
