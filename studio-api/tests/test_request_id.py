"""HI-11 / SC-3.3: a service honors an inbound X-Request-ID (logs + echoes it) instead of always
minting its own — so a request forwarded across hops shares ONE id. Absent an inbound id, one is
generated. The trace middleware runs on every request (even a 401), so the echo is always present."""

from __future__ import annotations

from fastapi.testclient import TestClient

from studioapi.main import app

client = TestClient(app)


def test_inbound_request_id_is_honored_and_echoed():
    r = client.get("/conversations", headers={"X-Request-ID": "trace-abc-123",
                                              "X-Service-Token": "dev-service-token"})
    assert r.headers.get("X-Request-ID") == "trace-abc-123"


def test_request_id_is_minted_when_absent():
    r = client.get("/conversations", headers={"X-Service-Token": "dev-service-token"})
    rid = r.headers.get("X-Request-ID")
    assert rid and len(rid) == 8   # a fresh uuid4 hex[:8]


def test_metrics_endpoint_exposes_request_counts():
    """HI-11: the per-request middleware records Prometheus counters/histograms, served at /metrics."""
    client.get("/conversations", headers={"X-Service-Token": "dev-service-token"})  # generate a data point
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text
