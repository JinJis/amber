"""Platform client — fetch the tool catalog and call tools through the gateway.

Tool calls carry the tenant API key, so the control plane enforces entitlement +
metering + audit (the agent can only use what the tenant activated).
"""

from __future__ import annotations

import httpx

from agentengine.config import settings


class PlatformClient:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    async def fetch_tools(self) -> dict[str, dict]:
        import asyncio

        last_exc: Exception | None = None
        connectors = None
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
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
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            if tool["method"] == "GET":
                resp = await client.get(url, params=args, headers=headers)
            else:
                resp = await client.request(tool["method"], url, json=args, headers=headers)
        try:
            data = resp.json()
        except ValueError:
            data = resp.text[:1000]
        return {"status": resp.status_code, "connector": resp.headers.get("x-connector"), "data": data}
