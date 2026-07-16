"""COST-2 — Gemini usage_metadata 추출 + 원가 보고.

_tokens는 청구 총량(input=prompt, output=candidates+thoughts)은 그대로 두고 분해값
(cached/tool/thinking)을 함께 뽑는다(중복 합산 없음). report는 해상 model_version을 우선해
`-latest` 별칭 대신 실제 모델로 가격 매칭되게 한다.
"""

from __future__ import annotations

import asyncio

import pytest

import agentengine.usage as U


class _UM:
    def __init__(self, **kw):
        self.prompt_token_count = kw.get("prompt", 0)
        self.candidates_token_count = kw.get("candidates", 0)
        self.thoughts_token_count = kw.get("thoughts", 0)
        self.cached_content_token_count = kw.get("cached", 0)
        self.tool_use_prompt_token_count = kw.get("tool", 0)


class _Resp:
    def __init__(self, um, model_version=None):
        self.usage_metadata = um
        if model_version is not None:
            self.model_version = model_version


# --- _tokens -----------------------------------------------------------------------------------

def test_tokens_totals_and_breakdowns():
    got = U._tokens(_Resp(_UM(prompt=1000, candidates=200, thoughts=50, cached=300, tool=40)))
    assert got["input_tokens"] == 1000        # prompt (cached+tool already inside — NOT re-added)
    assert got["output_tokens"] == 250        # candidates + thoughts
    assert got["cached_input_tokens"] == 300
    assert got["tool_input_tokens"] == 40
    assert got["thinking_tokens"] == 50


def test_tokens_none_without_metadata():
    class _NoUM:
        usage_metadata = None
    assert U._tokens(_NoUM()) is None


def test_tokens_none_when_all_zero():
    assert U._tokens(_Resp(_UM())) is None


def test_tokens_missing_optional_fields_default_zero():
    class _Bare:
        prompt_token_count = 500     # only the input count present
    got = U._tokens(_Resp(_Bare()))
    assert got["input_tokens"] == 500 and got["output_tokens"] == 0
    assert got["cached_input_tokens"] == 0 and got["tool_input_tokens"] == 0 and got["thinking_tokens"] == 0


# --- report ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_resolved_model_and_breakdowns(monkeypatch):
    captured: dict = {}

    async def _fake_post(payload):
        captured.update(payload)

    monkeypatch.setattr(U, "_post", _fake_post)
    r = _Resp(_UM(prompt=800, candidates=100, thoughts=20, cached=200, tool=10),
              model_version="gemini-2.5-flash")
    U.report("plan", "gemini-flash-latest", r)     # alias in → resolved out
    await asyncio.sleep(0.02)
    assert captured["model"] == "gemini-2.5-flash"
    assert captured["service"] == "agent-engine" and captured["kind"] == "plan"
    assert captured["input_tokens"] == 800 and captured["output_tokens"] == 120
    assert captured["cached_input_tokens"] == 200 and captured["tool_input_tokens"] == 10
    assert captured["thinking_tokens"] == 20


@pytest.mark.asyncio
async def test_report_falls_back_to_alias_without_model_version(monkeypatch):
    captured: dict = {}

    async def _fake_post(payload):
        captured.update(payload)

    monkeypatch.setattr(U, "_post", _fake_post)
    U.report("intake", "gemini-flash-lite-latest", _Resp(_UM(prompt=10, candidates=5)))
    await asyncio.sleep(0.02)
    assert captured["model"] == "gemini-flash-lite-latest"


def test_report_no_metadata_sends_nothing(monkeypatch):
    calls: list = []

    async def _fake_post(payload):
        calls.append(payload)

    monkeypatch.setattr(U, "_post", _fake_post)

    class _NoUM:
        usage_metadata = None

    U.report("plan", "m", _NoUM())   # returns early before scheduling any task
    assert calls == []
