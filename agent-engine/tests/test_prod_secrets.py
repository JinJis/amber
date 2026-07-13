"""SC-0/CR-10: agent-engine must refuse to boot in production on the dev telemetry token. AGENT_
uses an env_prefix, so the shared ENV flag is read from os.environ directly."""

from __future__ import annotations

import pytest

from agentengine.config import assert_production_secrets, settings


def test_production_refuses_dev_token(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    assert_production_secrets()  # dev → no-op

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "admin_token", "dev-admin-token")
    with pytest.raises(RuntimeError, match="AGENT_ADMIN_TOKEN"):
        assert_production_secrets()

    monkeypatch.setattr(settings, "admin_token", "real-token")
    assert_production_secrets()  # real token → OK
