"""ASK-5 — ask-feed composition tests (no network, no real LLM).

Covers the pieces the entry screen's trust depends on: the data signature (unchanged →
no LLM spend, cards kept), the citation-drop + QT-2 hook audit on synthesized cards, and
the kind allow-lists per scope.
"""

from __future__ import annotations

import pytest

import agentengine.askfeed as AF
from agentengine.askfeed import AskFeedRequest, _signature, build_ask_feed
from agentengine.models import Citation

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _g(idx: int, tool: str, data: dict, source: str = "Yahoo Finance") -> dict:
    return {"idx": idx, "tool": tool, "args": {"ticker": "AAPL"}, "why": "테스트",
            "data": data,
            "citation": Citation(tool=tool, source=source, as_of="2026-07-05", kind="data",
                                 ticker="AAPL", used=True)}


def test_signature_stable_and_sensitive():
    a = [_g(1, "yahoo__price_snapshot", {"price": 210.5, "as_of": "2026-07-05"})]
    same = [_g(1, "yahoo__price_snapshot", {"price": 210.5, "as_of": "2026-07-05"})]
    moved = [_g(1, "yahoo__price_snapshot", {"price": 211.9, "as_of": "2026-07-06"})]
    assert _signature(a) == _signature(same)          # same records → same digest
    assert _signature(a) != _signature(moved)         # new data → new digest


class _FakeClient:
    def __init__(self, tools, gathered):
        self._tools, self._gathered = tools, gathered

    async def fetch_tools(self):
        return self._tools


async def test_unchanged_signature_skips_llm(monkeypatch):
    gathered = [_g(1, "yahoo__price_snapshot", {"price": 210.5})]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"yahoo__price_snapshot": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("yahoo__price_snapshot", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    called = {"synth": 0}

    async def fake_synth(prompt):
        called["synth"] += 1
        return []
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    sig = _signature(gathered)
    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL",
                                              prev_signature=sig), api_key="k")
    assert out["unchanged"] is True and out["signature"] == sig
    assert called["synth"] == 0                        # no LLM spend when nothing changed


async def test_citation_drop_and_kind_filter(monkeypatch):
    gathered = [_g(1, "sec_edgar__filings", {"filings": [{"accession": "1", "date": "2026-07-03"}]},
                   source="SEC EDGAR")]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"sec_edgar__filings": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("sec_edgar__filings", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_synth(prompt):
        return [
            {"kind": "filing_deep", "question": "새 공시에서 위험요소 바뀐 것 보여줘",
             "hook": "새 공시 접수 (2026-07-03)", "sources": [1]},          # ships
            {"kind": "filing_deep", "question": "근거 없는 카드", "hook": "허공", "sources": []},  # dropped: no citation
            {"kind": "macro", "question": "티커 스코프에 거시 카드", "hook": "x", "sources": [1]},  # dropped: kind not allowed
            {"kind": "price_context", "question": "숫자 지어냄", "hook": "주가 999.99달러",
             "sources": [1]},                                               # dropped: QT-2 unsupported figure
        ]
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL"), api_key="k")
    qs = [c["question"] for c in out["cards"]]
    assert qs == ["새 공시에서 위험요소 바뀐 것 보여줘"]
    assert out["cards"][0]["citations"][0]["source"] == "SEC EDGAR"
    assert out["signature"]                                             # new signature recorded


async def test_synthesis_failure_keeps_previous_generation(monkeypatch):
    gathered = [_g(1, "yahoo__price_snapshot", {"price": 210.5})]
    monkeypatch.setattr(AF, "PlatformClient", lambda key: _FakeClient({"yahoo__price_snapshot": {}}, gathered))
    monkeypatch.setattr(AF, "_ticker_plan", lambda tools, req: [("yahoo__price_snapshot", {}, "x")])

    async def fake_gather(client, tools, plan):
        return gathered
    monkeypatch.setattr(AF, "_gather", fake_gather)

    async def fake_synth(prompt):
        raise RuntimeError("no key")
    monkeypatch.setattr(AF, "_synthesize", fake_synth)

    out = await build_ask_feed(AskFeedRequest(scope="ticker", market="US", ticker="AAPL"), api_key="k")
    # no fabricated fallback for the entry screen: empty cards + no signature → caller keeps old rows
    assert out["cards"] == [] and out["signature"] is None and out["unchanged"] is False


def test_trend_plan_covers_macro_micro_market():
    tools = {"yahoo__asset_classes": {}, "fred__macro_panel": {}, "google_news__news": {}}
    plan = AF._trend_plan(tools)
    names = [p[0] for p in plan]
    assert "yahoo__asset_classes" in names and "fred__macro_panel" in names
    assert any(n == "google_news__news" for n in names)
