"""SC-0/CR-10: rag must refuse to boot in production on the dev telemetry token or the default pg
password. RAG_ uses an env_prefix, so the shared ENV flag is read from os.environ directly."""

from __future__ import annotations

import pytest

from rag.config import assert_production_secrets, settings


def test_production_refuses_dev_defaults(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    assert_production_secrets()  # dev → no-op

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(settings, "admin_token", "dev-admin-token")
    monkeypatch.setattr(settings, "database_url", "")
    with pytest.raises(RuntimeError, match="RAG_ADMIN_TOKEN"):
        assert_production_secrets()

    monkeypatch.setattr(settings, "admin_token", "real-token")
    assert_production_secrets()  # real token, no pg → OK

    monkeypatch.setattr(settings, "database_url", "postgresql://rag:rag@postgres:5432/rag")
    with pytest.raises(RuntimeError, match="RAG_DATABASE_URL"):
        assert_production_secrets()
