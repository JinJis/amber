"""Build MCP tools from the data-plane catalog, and execute them via the gateway.

Each connector resource becomes one MCP tool named ``{connector}__{resource}``
with an input schema derived from the manifest params. A tool call is translated
into a gateway request carrying the tenant API key, so entitlement (activation),
metering, and audit all happen in the control plane. The tool description carries
the provenance source and license so the agent knows what it's getting.
"""

from __future__ import annotations

import httpx

from mcpserver.config import settings

_TYPE = {"integer": "integer", "number": "number", "boolean": "boolean", "date": "string", "string": "string"}


async def fetch_catalog() -> list[dict]:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        resp = await client.get(f"{settings.gateway_url}/catalog")
        resp.raise_for_status()
        return resp.json().get("connectors", [])


def _input_schema(resource: dict) -> dict:
    props: dict = {}
    required: list[str] = []
    for p in resource.get("params", []):
        prop = {"type": _TYPE.get(p.get("type", "string"), "string")}
        if p.get("description"):
            prop["description"] = p["description"]
        if p.get("enum"):
            prop["enum"] = p["enum"]
        props[p["name"]] = prop
        if p.get("required"):
            required.append(p["name"])
    return {"type": "object", "properties": props, "required": required}


def build_tools(connectors: list[dict]) -> list[dict]:
    tools: list[dict] = []
    for connector in connectors:
        lic = connector.get("license", {})
        for resource in connector.get("resources", []):
            prov = resource.get("provenance", {})
            redistribute = "" if lic.get("redistribution", True) else " · NO-REDISTRIBUTE"
            cadence = resource.get("cadence")
            desc = (
                f"{resource.get('description', '')} "
                f"[source: {prov.get('source')}; markets: {','.join(resource.get('markets', []))}; "
                f"cadence: {cadence}; license: {lic.get('id')}{redistribute}]"
            )
            tools.append(
                {
                    "name": f"{connector['id']}__{resource['name']}",
                    "description": desc.strip()[:480],
                    "inputSchema": _input_schema(resource),
                    "_method": resource.get("method", "GET").upper(),
                    "_path": resource["path"],
                }
            )
    return tools


def tool_index(tools: list[dict]) -> dict[str, dict]:
    return {t["name"]: t for t in tools}


def _safe_body(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]


async def call_tool(tool: dict, arguments: dict) -> dict:
    headers = {"X-API-KEY": settings.api_key} if settings.api_key else {}
    url = f"{settings.gateway_url}{tool['_path']}"
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        if tool["_method"] == "GET":
            resp = await client.get(url, params=arguments or {}, headers=headers)
        else:
            resp = await client.request(tool["_method"], url, json=arguments or {}, headers=headers)
    return {"status": resp.status_code, "connector": resp.headers.get("x-connector"), "data": _safe_body(resp)}
