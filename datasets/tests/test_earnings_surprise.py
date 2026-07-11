"""V-8 — earnings calendar: API Ninjas 우선(서프라이즈% 포함), FMP 폴백. 모두 respx 목."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)
_H = {"X-API-KEY": "test"} if settings.accepted_api_keys else {}

_NINJAS = [{"date": "2026-05-20", "ticker": "NVDA", "earnings_timing": "after_market",
            "actual_revenue": 81_615_000_000, "estimated_revenue": 78_423_370_000,
            "revenue_difference": 3_191_630_000.0, "revenue_difference_pct": 4.0697,
            "actual_eps": 1.87, "estimated_eps": 1.76,
            "eps_difference": 0.11, "eps_difference_pct": 6.25},
           {"date": "2026-02-25", "ticker": "NVDA", "actual_eps": 1.62, "estimated_eps": 1.60,
            "eps_difference": 0.02, "eps_difference_pct": 1.25,
            "actual_revenue": None, "estimated_revenue": None}]


@respx.mock
def test_ninjas_primary_with_surprise_pct(monkeypatch):
    monkeypatch.setattr(settings, "api_ninjas_key", "nk")
    respx.get("https://api.api-ninjas.com/v1/earningscalendar").mock(
        return_value=httpx.Response(200, json=_NINJAS))
    r = client.get("/earnings-calendar", params={"ticker": "NVDA", "limit": 8}, headers=_H)
    assert r.status_code == 200
    body = r.json()
    assert body["source"].startswith("API Ninjas")
    ev = body["events"][0]
    assert ev["eps_actual"] == 1.87 and ev["eps_surprise_pct"] == 6.25
    assert ev["revenue_surprise_pct"] == 4.0697


@respx.mock
def test_falls_back_to_fmp_without_key_or_on_error(monkeypatch):
    monkeypatch.setattr(settings, "api_ninjas_key", "")
    monkeypatch.setattr(settings, "fmp_api_key", "fk")
    respx.get("https://financialmodelingprep.com/stable/earnings-calendar").mock(
        return_value=httpx.Response(200, json=[{"symbol": "NVDA", "date": "2026-05-20",
                                                "epsActual": "1.87", "epsEstimated": "1.76"}]))
    r = client.get("/earnings-calendar", params={"ticker": "NVDA"}, headers=_H)
    assert r.status_code == 200 and r.json()["source"] == "FMP"
    # ninjas key set but upstream down → FMP still answers
    monkeypatch.setattr(settings, "api_ninjas_key", "nk")
    respx.get("https://api.api-ninjas.com/v1/earningscalendar").mock(
        return_value=httpx.Response(200, json=[]))
    r2 = client.get("/earnings-calendar", params={"ticker": "NVDA"}, headers=_H)
    assert r2.status_code == 200 and r2.json()["source"] == "FMP"
