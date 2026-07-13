"""Shared test fixtures for agent-engine.

SC-1.2 introduced process-wide singletons for the Gemini path — a memoized ``genai_client()`` (so
every call site shares one client + httpx pool) and per-model concurrency semaphores. Tests
monkeypatch ``google.genai.Client`` and expect a FRESH client each test, so reset those caches
around every test. This restores the pre-memoization per-test behavior for tests while production
keeps the shared client.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_gemini_singletons():
    from agentengine import gemini_io, planner

    def _reset():
        gemini_io.genai_client.cache_clear()
        planner._build_planner.cache_clear()
        gemini_io._sems.clear()

    _reset()
    yield
    _reset()
