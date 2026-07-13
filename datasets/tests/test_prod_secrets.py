"""SC-0/CR-10: the data plane must refuse to boot in production on unguarded auth or the default
pg password. dev (ENV unset) stays a no-op so the local stack is unaffected."""

from __future__ import annotations

import pytest

from app.config import assert_production_secrets, settings


def test_production_refuses_open_data_plane(monkeypatch):
    monkeypatch.setattr(settings, "env", "production")
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "datasets_api_keys", "")
    monkeypatch.setattr(settings, "database_url", "sqlite:///./datasets.db")

    # no client keys → anyone with a non-empty key passes (CR-10): refuse
    with pytest.raises(RuntimeError, match="DATASETS_API_KEYS"):
        assert_production_secrets()

    monkeypatch.setattr(settings, "datasets_api_keys", "real-client-key")
    assert_production_secrets()  # keys present → OK

    # auth entirely disabled → refuse regardless of keys
    monkeypatch.setattr(settings, "auth_disabled", True)
    with pytest.raises(RuntimeError, match="AUTH_DISABLED"):
        assert_production_secrets()

    # default pg password rag:rag → refuse
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://rag:rag@postgres:5432/datasets")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        assert_production_secrets()

    # dev → no-op even with dev defaults
    monkeypatch.setattr(settings, "env", "dev")
    monkeypatch.setattr(settings, "auth_disabled", True)
    assert_production_secrets()
