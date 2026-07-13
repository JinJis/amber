"""SC-0/CR-10: the admin console holds CRUD over every service DB — in production it must refuse to
boot on the default credentials or the forgeable dev session secret."""

from __future__ import annotations

import pytest

from adminpanel.config import assert_production_secrets, settings


def test_production_refuses_dev_credentials(monkeypatch):
    monkeypatch.setattr(settings, "env", "dev")
    assert_production_secrets()  # dev → no-op

    monkeypatch.setattr(settings, "env", "production")
    # all dev defaults present → refuse, naming the leaked ones
    with pytest.raises(RuntimeError, match="ADMINUI"):
        assert_production_secrets()

    monkeypatch.setattr(settings, "adminui_username", "ops")
    monkeypatch.setattr(settings, "adminui_password", "s3cret-pw")
    monkeypatch.setattr(settings, "adminui_secret", "real-session-secret")
    monkeypatch.setattr(settings, "admin_token", "real-admin-token")
    monkeypatch.setattr(settings, "service_token", "real-service-token")
    assert_production_secrets()  # all real → OK
