"""MCP server tests: tool generation from the catalog + tool execution via gateway."""

from __future__ import annotations

import httpx
import respx

from mcpserver import tools as T
from mcpserver.tools import build_tools, call_tool, tool_index

CATALOG = [
    {
        "id": "yahoo",
        "license": {"id": "restricted-byo", "redistribution": False},
        "resources": [
            {
                "name": "prices", "description": "EOD prices", "method": "GET", "path": "/prices",
                "markets": ["US", "KR"],
                "params": [
                    {"name": "ticker", "required": True, "description": "symbol"},
                    {"name": "interval", "required": True, "enum": ["day", "week"]},
                    {"name": "market", "enum": ["US", "KR"]},
                ],
                "provenance": {"source": "Yahoo Finance"},
            }
        ],
    },
    {
        "id": "sec_edgar",
        "license": {"id": "us-public-domain", "redistribution": True},
        "resources": [
            {"name": "company_facts", "description": "facts", "method": "GET", "path": "/company/facts",
             "markets": ["US"], "params": [{"name": "ticker"}], "provenance": {"source": "SEC EDGAR"}}
        ],
    },
]
_GW = r"http://gw\.test/prices"


def test_build_tools_covers_every_catalog_resource():
    # single source of truth (invariant #8): exactly one tool per catalog resource, named
    # {connector}__{resource} — nothing hand-added to or dropped from the catalog derivation.
    tools = build_tools(CATALOG)
    expected = {f"{c['id']}__{r['name']}" for c in CATALOG for r in c["resources"]}
    assert {t["name"] for t in tools} == expected
    assert len(tools) == sum(len(c["resources"]) for c in CATALOG)


def test_build_tools_schema_and_license():
    idx = tool_index(build_tools(CATALOG))
    assert "yahoo__prices" in idx and "sec_edgar__company_facts" in idx
    schema = idx["yahoo__prices"]["inputSchema"]
    assert schema["properties"]["interval"]["enum"] == ["day", "week"]
    assert set(schema["required"]) == {"ticker", "interval"}
    assert "NO-REDISTRIBUTE" in idx["yahoo__prices"]["description"]
    assert "NO-REDISTRIBUTE" not in idx["sec_edgar__company_facts"]["description"]
    assert "Yahoo Finance" in idx["yahoo__prices"]["description"]


@respx.mock
async def test_call_tool_success(monkeypatch):
    monkeypatch.setattr(T.settings, "gateway_url", "http://gw.test")
    monkeypatch.setattr(T.settings, "api_key", "vgk_demo")
    respx.route(method="GET", url__regex=_GW).mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": []}, headers={"x-connector": "yahoo"})
    )
    tool = tool_index(build_tools(CATALOG))["yahoo__prices"]
    res = await call_tool(tool, {"ticker": "AAPL", "interval": "day", "market": "US"})
    assert res["status"] == 200 and res["data"]["ticker"] == "AAPL" and res["connector"] == "yahoo"


@respx.mock
async def test_call_tool_unentitled(monkeypatch):
    monkeypatch.setattr(T.settings, "gateway_url", "http://gw.test")
    respx.route(method="GET", url__regex=_GW).mock(
        return_value=httpx.Response(403, json={"error": "Error", "message": "Not entitled"})
    )
    tool = tool_index(build_tools(CATALOG))["yahoo__prices"]
    res = await call_tool(tool, {"ticker": "AAPL"})
    assert res["status"] == 403


@respx.mock
async def test_get_call_sends_args_as_query_params(monkeypatch):
    monkeypatch.setattr(T.settings, "gateway_url", "http://gw.test")
    monkeypatch.setattr(T.settings, "api_key", "vgk_q")
    route = respx.route(method="GET", url__regex=_GW).mock(return_value=httpx.Response(200, json={}))
    tool = tool_index(build_tools(CATALOG))["yahoo__prices"]
    await call_tool(tool, {"ticker": "AAPL", "interval": "day", "market": "US"})
    url = str(route.calls.last.request.url)
    assert "ticker=AAPL" in url and "interval=day" in url
    assert route.calls.last.request.headers.get("x-api-key") == "vgk_q"


@respx.mock
async def test_call_without_api_key_omits_header(monkeypatch):
    monkeypatch.setattr(T.settings, "gateway_url", "http://gw.test")
    monkeypatch.setattr(T.settings, "api_key", "")
    route = respx.route(method="GET", url__regex=_GW).mock(return_value=httpx.Response(200, json={}))
    tool = tool_index(build_tools(CATALOG))["yahoo__prices"]
    await call_tool(tool, {"ticker": "AAPL"})
    assert "x-api-key" not in route.calls.last.request.headers


def test_tools_carry_provenance_in_description():
    idx = tool_index(build_tools(CATALOG))
    # every generated tool surfaces its source so an MCP client sees provenance up front
    assert "SEC EDGAR" in idx["sec_edgar__company_facts"]["description"]
    assert "Yahoo Finance" in idx["yahoo__prices"]["description"]


def test_server_module_imports():
    import mcpserver.server as srv

    assert srv.server is not None


def test_build_tools_includes_rag_search():
    cat = CATALOG + [{
        "id": "rag", "license": {"id": "rag-derived", "redistribution": False},
        "resources": [{
            "name": "search", "description": "semantic search", "method": "POST", "path": "/rag/search",
            "markets": ["US", "KR"],
            "params": [{"name": "query", "required": True}, {"name": "top_k", "type": "integer"}],
            "provenance": {"source": "Platform RAG"},
        }],
    }]
    idx = tool_index(build_tools(cat))
    assert "rag__search" in idx
    t = idx["rag__search"]
    assert t["_method"] == "POST" and t["_path"] == "/rag/search"
    assert "query" in t["inputSchema"]["required"]


@respx.mock
async def test_call_tool_post_sends_json_body(monkeypatch):
    monkeypatch.setattr(T.settings, "gateway_url", "http://gw.test")
    monkeypatch.setattr(T.settings, "api_key", "vgk_k")
    route = respx.route(method="POST", url__regex=r"http://gw\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": []})
    )
    res = await T.call_tool({"_method": "POST", "_path": "/rag/search", "name": "rag__search"}, {"query": "apple"})
    assert res["status"] == 200 and route.called
    assert b"apple" in route.calls.last.request.read()
