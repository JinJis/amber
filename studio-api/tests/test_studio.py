"""Studio API tests — control-plane admin + agent-engine are respx-mocked."""

from __future__ import annotations

import asyncio
import json

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi import provision
from studioapi.agents import agent_to_spec, seed_templates
from studioapi.chat import _title, sse_tail, start_turn
from studioapi.config import settings
from studioapi.db import init_db
from studioapi.main import app
from studioapi.models import Agent, User
from studioapi.prompts import seed_community_prompts

client = TestClient(app)
SVC = "dev-service-token"


def setup_module(_module):
    init_db()
    seed_templates()
    seed_community_prompts()


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    monkeypatch.setattr(settings, "agent_engine_url", "http://ae.test")


def _mock_control_plane():
    respx.post("http://cp.test/admin/tenants").mock(return_value=httpx.Response(200, json={"id": "ten1"}))
    respx.post("http://cp.test/admin/tenants/ten1/projects").mock(return_value=httpx.Response(200, json={"id": "prj1"}))
    respx.post("http://cp.test/admin/projects/prj1/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_demo"}))
    respx.post("http://cp.test/admin/projects/prj1/activations").mock(return_value=httpx.Response(200, json={}))


def _hdr(email: str) -> dict:
    return {"X-Service-Token": SVC, "X-User-Email": email}


def test_service_token_required():
    assert client.post("/users/ensure", headers={"X-User-Email": "x@y.com"}).status_code == 401
    assert client.post("/users/ensure", headers={"X-Service-Token": SVC}).status_code == 401  # no user


@respx.mock
async def test_ensure_user_provisions_once(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    u = await provision.ensure_user("new@u.com")
    assert u.tenant_id == "ten1" and u.project_id == "prj1" and u.api_key == "vgk_demo"
    # second call returns cached (no new tenant call needed)
    u2 = await provision.ensure_user("new@u.com")
    assert u2.api_key == "vgk_demo"


@respx.mock
def test_users_ensure_endpoint(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    r = client.post("/users/ensure", headers=_hdr("e1@u.com"))
    assert r.status_code == 200 and r.json()["tenant_id"] == "ten1"
    assert "api_key" not in r.json()  # key never leaves the server


@respx.mock
def test_chat_stream_proxies_and_persists(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    sse = (
        'data: {"type":"tool","name":"yahoo__prices","args":{}}\n\n'
        'data: {"type":"token","text":"AAPL closed at 185."}\n\n'
        'data: {"type":"citation","tool":"yahoo__prices","source":"Yahoo Finance"}\n\n'
        'data: {"type":"done","citations":[{"tool":"yahoo__prices","source":"Yahoo Finance"}],"refused":false}\n\n'
    ).encode()
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))

    email = "chat@u.com"
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "AAPL price?"}]}, headers=_hdr(email))
    assert r.status_code == 200
    assert "yahoo__prices" in r.text and "conversation" in r.text  # tool event + final conversation event

    convs = client.get("/conversations", headers=_hdr(email)).json()["conversations"]
    assert convs
    cid = convs[0]["id"]
    msgs = client.get(f"/conversations/{cid}/messages", headers=_hdr(email)).json()["messages"]
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "assistant" in roles
    asst = next(m for m in msgs if m["role"] == "assistant")
    assert "AAPL" in asst["content"] and asst["citations"]


# --- title derivation -----------------------------------------------------
def test_title_uses_first_user_message_and_truncates():
    assert _title([{"role": "user", "content": "삼성전자 최근 실적"}]) == "삼성전자 최근 실적"
    # a leading assistant/system turn is skipped in favour of the first user turn
    assert _title([{"role": "assistant", "content": "hi"}, {"role": "user", "content": "AAPL?"}]) == "AAPL?"
    assert _title([{"role": "user", "content": "x" * 200}]) == "x" * 80
    assert _title([]) == "New chat"


# --- auth / deps ----------------------------------------------------------
def test_missing_user_email_is_401(monkeypatch):
    # service token present but no X-User-Email -> unauthenticated
    assert client.get("/conversations", headers={"X-Service-Token": SVC}).status_code == 401


def test_wrong_service_token_is_401():
    assert client.get("/conversations", headers={"X-Service-Token": "nope", "X-User-Email": "a@b.com"}).status_code == 401


# --- provisioning resilience ---------------------------------------------
@respx.mock
async def test_ensure_user_survives_activation_failures(monkeypatch):
    _cfg(monkeypatch)
    respx.post("http://cp.test/admin/tenants").mock(return_value=httpx.Response(200, json={"id": "tenF"}))
    respx.post("http://cp.test/admin/tenants/tenF/projects").mock(return_value=httpx.Response(200, json={"id": "prjF"}))
    respx.post("http://cp.test/admin/projects/prjF/keys").mock(return_value=httpx.Response(200, json={"api_key": "vgk_f"}))
    # every activation fails (e.g. connector missing) — the user is still provisioned
    respx.post("http://cp.test/admin/projects/prjF/activations").mock(return_value=httpx.Response(500, json={"error": "boom"}))
    u = await provision.ensure_user("partial@u.com")
    assert u.api_key == "vgk_f" and u.tenant_id == "tenF"


# --- conversations --------------------------------------------------------
@respx.mock
def test_messages_of_unknown_conversation_is_empty(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    r = client.get("/conversations/cnv_does_not_exist/messages", headers=_hdr("ghost@u.com"))
    assert r.status_code == 200 and r.json()["messages"] == []


async def test_run_manager_survives_leave_and_resumes():
    # generation lives server-side: a run keeps going after a tail (the client) stops, and a
    # fresh tail (re-entering the conversation) replays the buffer then continues live.
    from studioapi.runs import RunManager

    mgr = RunManager()
    gate = asyncio.Event()

    async def driver(run):
        await mgr.append(run, {"type": "token", "text": "a"})
        await gate.wait()  # simulate work in progress while the client leaves
        await mgr.append(run, {"type": "token", "text": "b"})

    run = mgr.start("conv1", driver)
    await asyncio.sleep(0.02)  # let the driver emit the first token
    assert mgr.active_run_id("conv1") == run.id  # still generating → resumable

    # a fresh tail (re-entry) replays from index 0: the seeded `run` event + token 'a' so far
    early = []
    async for ev in mgr.tail(run, 0):
        early.append(ev)
        if len(early) >= 2:
            break  # the "client" leaves again mid-run — driver keeps going
    assert early[0]["type"] == "run" and early[1] == {"type": "token", "text": "a"}

    gate.set()  # driver finishes in the background regardless of any client
    await run.task
    assert mgr.active_run_id("conv1") is None  # finished
    # a final tail sees the COMPLETE buffer (both tokens) — nothing was lost
    tokens = [ev["text"] async for ev in mgr.tail(run, 0) if ev.get("type") == "token"]
    assert tokens == ["a", "b"]


@respx.mock
async def test_chat_reuses_existing_conversation(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    sse = (
        'data: {"type":"token","text":"hello there"}\n\n'
        'data: {"type":"done","citations":[],"refused":false}\n\n'
    ).encode()
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse))

    import json as _json
    user = User(email="multi@u.com", tenant_id="t", project_id="p", api_key="vgk_m")

    async def _drive(conv_id, content):
        # start the background run + tail it to completion; return the conversation id seen
        run = start_turn(user, conv_id, [{"role": "user", "content": content}], None)
        seen = conv_id
        async for chunk in sse_tail(run, 0):
            line = next((l for l in chunk.split("\n") if l.startswith("data:")), None)
            if not line:
                continue
            ev = _json.loads(line[5:].strip())
            seen = ev.get("conversation_id") or (ev.get("id") if ev.get("type") == "conversation" else None) or seen
        return seen

    # first turn -> new conversation; second on the same id -> messages accumulate, no new row
    cid = await _drive(None, "first")
    assert cid
    assert await _drive(cid, "second") == cid

    convs = client.get("/conversations", headers=_hdr("multi@u.com")).json()["conversations"]
    assert len([c for c in convs if c["id"] == cid]) == 1
    msgs = client.get(f"/conversations/{cid}/messages", headers=_hdr("multi@u.com")).json()["messages"]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert {m["content"] for m in user_msgs} == {"first", "second"}


@respx.mock
def test_conversations_scoped_to_user(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    sse = b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n'
    respx.post("http://ae.test/agent/chat").mock(return_value=httpx.Response(200, content=sse))
    for q in ("alpha", "beta"):
        client.post("/chat/stream", json={"messages": [{"role": "user", "content": q}]}, headers=_hdr("owner@u.com"))
    # owner sees both of their conversations...
    owner_titles = {c["title"] for c in client.get("/conversations", headers=_hdr("owner@u.com")).json()["conversations"]}
    assert {"alpha", "beta"} <= owner_titles
    # ...a different user sees none of them
    other_titles = {c["title"] for c in client.get("/conversations", headers=_hdr("other@u.com")).json()["conversations"]}
    assert not ({"alpha", "beta"} & other_titles)


# --- F1: agents -----------------------------------------------------------
def test_agent_to_spec_maps_fields():
    a = Agent(name="X", model="gemini", system_prompt="Be terse.", data_sources='["yahoo","rag"]')
    spec = agent_to_spec(a)
    assert spec == {"system": "Be terse.", "allowed_tools": ["yahoo", "rag"], "backend": "gemini"}


@respx.mock
def test_agents_list_includes_templates(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    agents = client.get("/agents", headers=_hdr("ab@u.com")).json()["agents"]
    ids = {a["id"] for a in agents}
    assert "tpl_desk" in ids   # the single fully-loaded default agent
    tpl = next(a for a in agents if a["id"] == "tpl_desk")
    # data_sources == [] means UNRESTRICTED (every tool); the default desk uses all tools
    assert tpl["is_template"] and tpl["editable"] is False and tpl["data_sources"] == []
    assert tpl["model"] == "gemini"  # default is Gemini, never stub


@respx.mock
def test_agent_create_get_update_delete(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "builder@u.com"
    created = client.post("/agents", headers=_hdr(email), json={
        "name": "My Filings Bot", "description": "filings only",
        "system_prompt": "Cite filings.", "data_sources": ["sec_edgar", "rag"],
    }).json()
    aid = created["id"]
    assert created["editable"] and created["data_sources"] == ["sec_edgar", "rag"]
    assert created["model"] == "gemini"  # gemini-only (invariant #7)
    # it appears in the list alongside templates
    assert aid in {a["id"] for a in client.get("/agents", headers=_hdr(email)).json()["agents"]}
    # update
    upd = client.patch(f"/agents/{aid}", headers=_hdr(email), json={"data_sources": ["sec_edgar"], "model": "gemini"})
    assert upd.json()["data_sources"] == ["sec_edgar"] and upd.json()["model"] == "gemini"
    # delete
    assert client.delete(f"/agents/{aid}", headers=_hdr(email)).status_code == 200
    assert client.get(f"/agents/{aid}", headers=_hdr(email)).status_code == 404


@respx.mock
def test_agent_model_is_gemini_only(monkeypatch):
    # Gemini-only (invariant #7): any requested model (incl. legacy "stub" or a bogus value) is
    # accepted and normalized to gemini — there is no other backend to reject toward.
    _cfg(monkeypatch)
    _mock_control_plane()
    r = client.post("/agents", headers=_hdr("v@u.com"), json={"name": "coerced", "model": "gpt-9"})
    assert r.status_code == 200 and r.json()["model"] == "gemini"


@respx.mock
def test_template_not_editable_or_deletable(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    h = _hdr("t@u.com")
    assert client.patch("/agents/tpl_desk", headers=h, json={"name": "hijack"}).status_code == 404
    assert client.delete("/agents/tpl_desk", headers=h).status_code == 404
    # but it can be read (to clone)
    assert client.get("/agents/tpl_desk", headers=h).status_code == 200


@respx.mock
def test_agents_are_user_scoped(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    a = client.post("/agents", headers=_hdr("alice@u.com"), json={"name": "Alice bot"}).json()
    # bob cannot see or fetch alice's private agent
    bob_ids = {x["id"] for x in client.get("/agents", headers=_hdr("bob@u.com")).json()["agents"]}
    assert a["id"] not in bob_ids
    assert client.get(f"/agents/{a['id']}", headers=_hdr("bob@u.com")).status_code == 404


@respx.mock
@respx.mock
def test_board_pin_list_unpin_user_scoped(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    spec = {"kind": "timeseries", "title": "AAPL 종가", "source": "Yahoo Finance", "tool": "yahoo__prices",
            "series": [{"label": "종가", "points": [{"x": "2024-01-02", "y": 185.6}]}]}
    pinned = client.post("/board", headers=_hdr("b@u.com"), json={"spec": spec}).json()["pinned"][0]
    assert pinned["id"].startswith("pin") and pinned["title"] == "AAPL 종가"
    assert pinned["spec"]["tool"] == "yahoo__prices" and pinned["board_id"]  # lands on the default board
    mine = client.get("/board", headers=_hdr("b@u.com")).json()["pinned"]
    assert any(p["id"] == pinned["id"] for p in mine)
    # user-scoped: another user can't see it
    other = client.get("/board", headers=_hdr("intruder-b@u.com")).json()["pinned"]
    assert all(p["id"] != pinned["id"] for p in other)
    # unpin
    assert client.delete(f"/board/{pinned['id']}", headers=_hdr("b@u.com")).status_code == 200
    assert client.get("/board", headers=_hdr("b@u.com")).json()["pinned"] == []
    # a non-allowed spec kind is rejected
    assert client.post("/board", headers=_hdr("b@u.com"), json={"spec": {"title": "x"}}).status_code == 422


@respx.mock
def test_boards_multi_pin_layout_and_source_text(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    h = _hdr("brd@u.com")
    # a default board always exists
    boards = client.get("/boards", headers=h).json()["boards"]
    assert len(boards) == 1 and boards[0]["name"]
    b1 = boards[0]["id"]
    # create a second board + rename it
    b2 = client.post("/boards", headers=h, json={"name": "리서치"}).json()["id"]
    assert client.patch(f"/boards/{b2}", headers=h, json={"name": "반도체 리서치"}).json()["name"] == "반도체 리서치"

    # pin a SOURCE card to BOTH boards at once (multi-select), plus a text block to b2
    src = {"kind": "source", "title": "SEC 10-K", "source": "SEC EDGAR", "url": "https://sec.gov/x"}
    res = client.post("/board", headers=h, json={"spec": src, "board_ids": [b1, b2]}).json()["pinned"]
    assert len(res) == 2 and {r["board_id"] for r in res} == {b1, b2}
    txt = client.post("/board", headers=h, json={"spec": {"kind": "text", "text": "메모"}, "board_ids": [b2]}).json()["pinned"][0]

    # move/resize the text block on the canvas (layout persists)
    moved = client.patch(f"/board/{txt['id']}", headers=h, json={"x": 40, "y": 20, "w": 300, "h": 120}).json()
    assert (moved["x"], moved["y"], moved["w"], moved["h"]) == (40, 20, 300, 120)
    # edit the text block content
    edited = client.patch(f"/board/{txt['id']}", headers=h, json={"spec": {"kind": "text", "text": "수정됨"}}).json()
    assert edited["spec"]["text"] == "수정됨"

    # b2 has the source + the text; b1 has only the source
    b2_items = client.get(f"/board?board_id={b2}", headers=h).json()["pinned"]
    assert {i["spec"]["kind"] for i in b2_items} == {"source", "text"}
    b1_items = client.get(f"/board?board_id={b1}", headers=h).json()["pinned"]
    assert [i["spec"]["kind"] for i in b1_items] == ["source"]
    # deleting a board removes its pins
    client.delete(f"/boards/{b2}", headers=h)
    assert client.get(f"/board?board_id={b2}", headers=h).json()["pinned"] == []


@respx.mock
def test_board_refresh_updates_spec(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    spec = {"kind": "timeseries", "title": "AAPL 종가", "tool": "yahoo__prices",
            "args": {"ticker": "AAPL"}, "as_of": "2024-01-02", "series": []}
    pinned = client.post("/board", headers=_hdr("rf@u.com"), json={"spec": spec}).json()["pinned"][0]
    # agent-engine re-fetches → a fresher artifact (new as_of)
    respx.post("http://ae.test/agent/artifact/refresh").mock(return_value=httpx.Response(200, json={"artifact": {
        "kind": "timeseries", "title": "AAPL 종가", "tool": "yahoo__prices", "args": {"ticker": "AAPL"},
        "as_of": "2024-03-01", "series": [{"label": "종가", "points": [{"x": "2024-03-01", "y": 200.0}]}]}}))
    r = client.post(f"/board/{pinned['id']}/refresh", headers=_hdr("rf@u.com"))
    assert r.status_code == 200 and r.json()["spec"]["as_of"] == "2024-03-01"  # refreshed in place


@respx.mock
def test_board_annotate_saves_and_survives_refresh(monkeypatch):
    # PH-VIZ-5: user drawings persist on the pin and are carried across a live refresh.
    _cfg(monkeypatch)
    _mock_control_plane()
    spec = {"kind": "candlestick", "title": "AAPL 주가", "tool": "yahoo__prices",
            "args": {"ticker": "AAPL"}, "as_of": "2024-01-02", "series": [], "candles": []}
    pinned = client.post("/board", headers=_hdr("dr@u.com"), json={"spec": spec}).json()["pinned"][0]
    ann = {"hlines": [{"price": 190.0, "label": "저항"}], "lines": []}
    r = client.post(f"/board/{pinned['id']}/annotate", headers=_hdr("dr@u.com"),
                    json={"user_annotations": ann})
    assert r.status_code == 200 and r.json()["spec"]["user_annotations"]["hlines"][0]["price"] == 190.0
    # a live refresh re-fetches data but keeps the drawings
    respx.post("http://ae.test/agent/artifact/refresh").mock(return_value=httpx.Response(200, json={"artifact": {
        "kind": "candlestick", "title": "AAPL 주가", "tool": "yahoo__prices", "args": {"ticker": "AAPL"},
        "as_of": "2024-03-01", "series": [], "candles": []}}))
    rf = client.post(f"/board/{pinned['id']}/refresh", headers=_hdr("dr@u.com")).json()
    assert rf["spec"]["as_of"] == "2024-03-01"
    assert rf["spec"]["user_annotations"]["hlines"][0]["price"] == 190.0  # drawings survived
    # clearing removes them
    cleared = client.post(f"/board/{pinned['id']}/annotate", headers=_hdr("dr@u.com"),
                          json={"user_annotations": None}).json()
    assert "user_annotations" not in cleared["spec"]
    # user-scoped
    assert client.post(f"/board/{pinned['id']}/annotate", headers=_hdr("intruder-dr@u.com"),
                       json={"user_annotations": ann}).status_code == 404


@respx.mock
def test_prices_proxy_forwards_to_gateway(monkeypatch):
    # the chart's history fetch → studio /prices → gateway /prices (tenant key, entitled)
    _cfg(monkeypatch)
    _mock_control_plane()
    route = respx.get("http://cp.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"time": "2024-01-02", "close": 185.6}]}))
    r = client.get("/prices?ticker=AAPL&market=US&interval=day&start_date=2018-01-01&end_date=2024-01-01",
                   headers=_hdr("px@u.com"))
    assert r.status_code == 200 and r.json()["prices"][0]["close"] == 185.6
    sent = route.calls.last.request
    assert sent.headers["X-API-KEY"] == "vgk_demo"
    assert "ticker=AAPL" in str(sent.url) and "start_date=2018-01-01" in str(sent.url)
    # ticker is required
    assert client.get("/prices?market=US", headers=_hdr("px@u.com")).status_code == 400


@respx.mock
def test_financials_proxy_forwards_to_gateway(monkeypatch):
    # the revenue chart's history fetch → studio /financials → gateway /financials/income-statements
    _cfg(monkeypatch)
    _mock_control_plane()
    route = respx.get("http://cp.test/financials/income-statements").mock(
        return_value=httpx.Response(200, json={"income_statements": [{"report_period": "2023-12-31", "revenue": 3.9e11}]}))
    r = client.get("/financials?ticker=AAPL&market=US&limit=40", headers=_hdr("fin@u.com"))
    assert r.status_code == 200 and r.json()["income_statements"][0]["revenue"] == 3.9e11
    sent = route.calls.last.request
    assert sent.headers["X-API-KEY"] == "vgk_demo"
    assert "ticker=AAPL" in str(sent.url) and "period=annual" in str(sent.url)  # default period added
    assert client.get("/financials?market=US", headers=_hdr("fin@u.com")).status_code == 400


@respx.mock
def test_connectors_grouped_by_category(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    # the catalog now carries user-facing categories + a `category` on each resource
    respx.get("http://cp.test/catalog").mock(return_value=httpx.Response(200, json={
        "categories": [
            {"id": "market", "label": "금융시장 현황", "description": "지수·시세"},
            {"id": "filings", "label": "공시·문서", "description": "공시"},
            {"id": "macro", "label": "거시경제 분석", "description": "금리"},  # no tools → omitted
        ],
        "connectors": [
            {"id": "yahoo", "name": "Yahoo Finance", "markets": ["US", "KR"], "resources": [
                {"name": "prices", "description": "Historical EOD OHLCV.", "category": "market",
                 "provenance": {"source": "Yahoo Finance"}},
                {"name": "sector_heatmap", "description": "Sector heatmap.", "category": "market",
                 "provenance": {"source": "Yahoo Finance"}}]},
            {"id": "sec_edgar", "name": "SEC EDGAR", "markets": ["US"], "resources": [
                {"name": "filings", "description": "SEC filings.", "category": "filings",
                 "provenance": {"source": "SEC EDGAR"}}]},
        ]}))
    cats = client.get("/connectors", headers=_hdr("c@u.com")).json()["categories"]
    # categories WITH tools only, in catalog order; API grouping is gone
    assert [c["id"] for c in cats] == ["market", "filings"]
    market = next(c for c in cats if c["id"] == "market")
    # tools carry FULLY-QUALIFIED ids (what gets stored in data_sources), grouped across APIs
    assert {t["name"] for t in market["tools"]} == {"yahoo__prices", "yahoo__sector_heatmap"}
    assert all(t["source"] == "Yahoo Finance" for t in market["tools"])
    filings = next(c for c in cats if c["id"] == "filings")
    assert filings["tools"][0]["name"] == "sec_edgar__filings"


@respx.mock
def test_chat_with_agent_sends_spec_and_records_agent(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    captured = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n')

    respx.post("http://ae.test/agent/chat").mock(side_effect=_capture)

    email = "agentchat@u.com"
    r = client.post("/chat/stream", headers=_hdr(email),
                    json={"messages": [{"role": "user", "content": "AAPL filings?"}], "agent_id": "tpl_desk"})
    assert r.status_code == 200
    spec = captured["body"]["spec"]
    # default desk = Gemini + unrestricted tools (data_sources [] → allowed_tools None = every tool)
    assert spec["backend"] == "gemini" and spec["allowed_tools"] is None
    # the conversation remembers which agent drove it
    conv = client.get("/conversations", headers=_hdr(email)).json()["conversations"][0]
    assert conv["agent_id"] == "tpl_desk"


# --- F2: prompt library ---------------------------------------------------
@respx.mock
def test_community_prompts_seeded(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    cat = client.get("/prompts/community", headers=_hdr("p1@u.com")).json()["prompts"]
    ids = {p["id"] for p in cat}
    assert {"cpr_earnings", "cpr_macro_rates"} <= ids
    one = next(p for p in cat if p["id"] == "cpr_earnings")
    assert one["community"] and one["editable"] is False and one["body"]


@respx.mock
def test_prompt_create_list_update_delete(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "p2@u.com"
    created = client.post("/prompts", headers=_hdr(email), json={
        "title": "내 요약 프롬프트", "body": "{TICKER} 실적 요약해줘", "category": "리서치",
    }).json()
    pid = created["id"]
    assert created["editable"] and created["community"] is False
    # personal library lists it; community catalog does not
    assert pid in {p["id"] for p in client.get("/prompts", headers=_hdr(email)).json()["prompts"]}
    assert pid not in {p["id"] for p in client.get("/prompts/community", headers=_hdr(email)).json()["prompts"]}
    # update + delete
    upd = client.patch(f"/prompts/{pid}", headers=_hdr(email), json={"title": "수정됨"})
    assert upd.json()["title"] == "수정됨"
    assert client.delete(f"/prompts/{pid}", headers=_hdr(email)).status_code == 200
    assert client.get(f"/prompts/{pid}", headers=_hdr(email)).status_code == 404


@respx.mock
def test_import_community_prompt_is_editable_copy_and_idempotent(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "p3@u.com"
    imp = client.post("/prompts/cpr_earnings/import", headers=_hdr(email)).json()
    assert imp["editable"] and imp["source_id"] == "cpr_earnings" and imp["id"] != "cpr_earnings"
    assert imp["body"]  # copied content
    # it now lives in the personal library
    mine = {p["id"] for p in client.get("/prompts", headers=_hdr(email)).json()["prompts"]}
    assert imp["id"] in mine
    # importing again returns the same copy (no duplicates)
    again = client.post("/prompts/cpr_earnings/import", headers=_hdr(email)).json()
    assert again["id"] == imp["id"]


@respx.mock
def test_community_prompt_not_editable(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    h = _hdr("p4@u.com")
    assert client.patch("/prompts/cpr_earnings", headers=h, json={"title": "x"}).status_code == 404
    assert client.delete("/prompts/cpr_earnings", headers=h).status_code == 404
    # importing a non-community (or unknown) id 404s
    assert client.post("/prompts/prm_nope/import", headers=h).status_code == 404


@respx.mock
def test_prompts_are_user_scoped(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    p = client.post("/prompts", headers=_hdr("ann@u.com"), json={"title": "Ann", "body": "x"}).json()
    bob_ids = {x["id"] for x in client.get("/prompts", headers=_hdr("bob2@u.com")).json()["prompts"]}
    assert p["id"] not in bob_ids
    assert client.get(f"/prompts/{p['id']}", headers=_hdr("bob2@u.com")).status_code == 404


# --- U1: watchlists / @groups ---------------------------------------------
@respx.mock
def test_watchlist_create_add_items_get_delete(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "wl@u.com"
    wl = client.post("/watchlists", headers=_hdr(email), json={"name": "반도체바스켓"}).json()
    wid = wl["id"]
    assert wl["handle"] == "@반도체바스켓" and wl["count"] == 0
    # add two companies (US + KR)
    client.post(f"/watchlists/{wid}/items", headers=_hdr(email),
                json={"market": "kr", "ticker": "005930", "name": "삼성전자"})
    client.post(f"/watchlists/{wid}/items", headers=_hdr(email),
                json={"market": "US", "ticker": "NVDA", "name": "NVIDIA"})
    got = client.get(f"/watchlists/{wid}", headers=_hdr(email)).json()
    assert got["count"] == 2
    assert ("KR", "005930") in {(i["market"], i["ticker"]) for i in got["items"]}  # market upper-cased
    # delete the whole group
    assert client.delete(f"/watchlists/{wid}", headers=_hdr(email)).status_code == 200
    assert client.get(f"/watchlists/{wid}", headers=_hdr(email)).status_code == 404


@respx.mock
def test_watchlist_add_item_idempotent_and_market_validated(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "wl2@u.com"
    wid = client.post("/watchlists", headers=_hdr(email), json={"name": "관심"}).json()["id"]
    a = client.post(f"/watchlists/{wid}/items", headers=_hdr(email), json={"market": "US", "ticker": "AAPL"}).json()
    b = client.post(f"/watchlists/{wid}/items", headers=_hdr(email), json={"market": "US", "ticker": "AAPL"}).json()
    assert a["id"] == b["id"]  # idempotent — no duplicate
    assert client.get(f"/watchlists/{wid}", headers=_hdr(email)).json()["count"] == 1
    # bad market rejected
    assert client.post(f"/watchlists/{wid}/items", headers=_hdr(email),
                       json={"market": "ZZ", "ticker": "X"}).status_code == 422


@respx.mock
def test_watchlist_rename_and_duplicate_handle_blocked(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "wl3@u.com"
    client.post("/watchlists", headers=_hdr(email), json={"name": "그룹A"})
    wb = client.post("/watchlists", headers=_hdr(email), json={"name": "그룹B"}).json()
    # creating a third with a taken name → 409
    assert client.post("/watchlists", headers=_hdr(email), json={"name": "그룹A"}).status_code == 409
    # renaming B to A (taken) → 409; renaming to a free name works
    assert client.patch(f"/watchlists/{wb['id']}", headers=_hdr(email), json={"name": "그룹A"}).status_code == 409
    ren = client.patch(f"/watchlists/{wb['id']}", headers=_hdr(email), json={"name": "그룹C"}).json()
    assert ren["name"] == "그룹C" and ren["handle"] == "@그룹C"


@respx.mock
def test_company_search_proxies_gateway_with_tenant_key(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    captured = {}

    def _capture(request):
        captured["key"] = request.headers.get("X-API-KEY")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"source": "SEC EDGAR", "query": "app",
                                         "results": [{"name": "Apple Inc.", "ticker": "AAPL", "market": "US", "cik": "0000320193"}]})

    respx.get("http://cp.test/company/search").mock(side_effect=_capture)
    r = client.get("/company/search?q=app&market=US", headers=_hdr("search@u.com"))
    assert r.status_code == 200
    body = r.json()
    assert body["results"][0]["ticker"] == "AAPL"
    assert captured["key"] and captured["key"].startswith("vgk_")  # tenant key, server-side
    assert "q=app" in captured["url"]


@respx.mock
def test_chat_expands_at_handle_to_tickers(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "handle@u.com"
    # build a group with two companies
    wid = client.post("/watchlists", headers=_hdr(email), json={"name": "반도체바스켓"}).json()["id"]
    client.post(f"/watchlists/{wid}/items", headers=_hdr(email),
                json={"market": "KR", "ticker": "005930", "name": "삼성전자"})
    client.post(f"/watchlists/{wid}/items", headers=_hdr(email),
                json={"market": "US", "ticker": "NVDA", "name": "NVIDIA"})

    captured = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n')

    respx.post("http://ae.test/agent/chat").mock(side_effect=_capture)

    r = client.post("/chat/stream", headers=_hdr(email),
                    json={"messages": [{"role": "user", "content": "@반도체바스켓 시가총액 합?"}]})
    assert r.status_code == 200
    sent = captured["body"]["messages"][-1]["content"]
    # the agent sees concrete tickers, not just the bare handle
    assert "005930" in sent and "NVDA" in sent and "삼성전자" in sent
    # but the persisted user message keeps the original handle text
    conv = client.get("/conversations", headers=_hdr(email)).json()["conversations"][0]
    msgs = client.get(f"/conversations/{conv['id']}/messages", headers=_hdr(email)).json()["messages"]
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert user_msg["content"] == "@반도체바스켓 시가총액 합?" and "005930" not in user_msg["content"]


@respx.mock
def test_chat_unknown_handle_is_graceful(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    email = "handle2@u.com"
    captured = {}

    def _capture(request):
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b'data: {"type":"token","text":"ok"}\n\ndata: {"type":"done","citations":[],"refused":false}\n\n')

    respx.post("http://ae.test/agent/chat").mock(side_effect=_capture)
    r = client.post("/chat/stream", headers=_hdr(email),
                    json={"messages": [{"role": "user", "content": "@없는그룹 알려줘"}]})
    assert r.status_code == 200
    sent = captured["body"]["messages"][-1]["content"]
    assert "알 수 없는 관심 그룹" in sent  # graceful note, not a crash


@respx.mock
def test_watchlists_are_user_scoped(monkeypatch):
    _cfg(monkeypatch)
    _mock_control_plane()
    mine = client.post("/watchlists", headers=_hdr("owner-wl@u.com"), json={"name": "내그룹"}).json()
    other_ids = {w["id"] for w in client.get("/watchlists", headers=_hdr("intruder@u.com")).json()["watchlists"]}
    assert mine["id"] not in other_ids
    assert client.get(f"/watchlists/{mine['id']}", headers=_hdr("intruder@u.com")).status_code == 404
    # the same ticker can live in two different users' groups (per-watchlist uniqueness, not global)
    client.post(f"/watchlists/{mine['id']}/items", headers=_hdr("owner-wl@u.com"),
                json={"market": "US", "ticker": "TSLA"})
    other = client.post("/watchlists", headers=_hdr("intruder@u.com"), json={"name": "남그룹"}).json()
    assert client.post(f"/watchlists/{other['id']}/items", headers=_hdr("intruder@u.com"),
                       json={"market": "US", "ticker": "TSLA"}).status_code == 200


@respx.mock
def test_evidence_html_proxy_streams_html_or_204(monkeypatch):
    """studio-api proxies the original filing HTML from the gateway with the user's tenant key
    (for the in-app viewer), and degrades to 204 when the gateway has no markup."""
    _cfg(monkeypatch)
    _mock_control_plane()
    route = respx.get("http://cp.test/evidence/html")
    route.mock(return_value=httpx.Response(
        200, content=b"<html><body>filing</body></html>",
        headers={"content-type": "text/html; charset=utf-8"}))
    q = "market=US&accession=0000320193-24-000123&cik=320193"
    r = client.get(f"/evidence/html?{q}", headers=_hdr("e@u.com"))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert b"filing" in r.content

    route.mock(return_value=httpx.Response(204))  # gateway → no markup
    assert client.get(f"/evidence/html?{q}", headers=_hdr("e@u.com")).status_code == 204


