"""CR-6 / SC-1.1: the per-turn synthesis tier must be a call ARGUMENT, never stored on the planner.

get_planner() is a process-wide @cache singleton, so a per-turn `synthesis_override` attribute
raced across concurrent users — a free/guest turn's `flash` could downgrade a concurrent pro turn's
synthesis (or vice-versa). This pins the fix: synthesis_model is threaded per call, no shared state.
"""

from __future__ import annotations

import pytest


async def test_synthesis_model_is_per_call_no_shared_state(monkeypatch):
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock

    import google.genai

    from agentengine.config import settings
    from agentengine.planner import GeminiPlanner

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.candidates = []  # → _get_text_from_response returns None (no iteration on a MagicMock)
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)
    monkeypatch.setattr("agentengine.planner.report_usage", lambda *a, **k: None)

    planner = GeminiPlanner("flash-plan")
    # the racy per-turn slot is gone
    assert not hasattr(planner, "synthesis_override")

    async def synth_model_used(model):
        await planner.plan("q", {}, [], force_final=True, synthesis_model=model)
        return mock_client.models.generate_content.call_args.kwargs["model"]

    # each synthesis call honors ITS OWN tier — no leak between them
    assert await synth_model_used("pro-A") == "pro-A"
    assert await synth_model_used("flash-B") == "flash-B"

    # no override → the configured default synthesis model, not a leaked prior value
    await planner.plan("q", {}, [], force_final=True)
    assert mock_client.models.generate_content.call_args.kwargs["model"] == settings.synthesis_model
