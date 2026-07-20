"""ENT — cockpit market endpoints: pulse strip curation + watch tickers from the user's groups."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.db import init_db
from studioapi.main import app

client = TestClient(app)
SVC = "dev-service-token"


def setup_module(_m):
    init_db()


def _hdr(email):
    return {"X-Service-Token": SVC, "X-User-Email": email}


def _cp():
    respx.post("http://cp.test/admin/provision").mock(
        return_value=httpx.Response(200, json={"project_id": "p", "api_key": "k"}))
    respx.post("http://cp.test/admin/projects").mock(return_value=httpx.Response(200, json={"id": "p"}))
    respx.post("http://cp.test/admin/projects/p/keys").mock(return_value=httpx.Response(200, json={"api_key": "k"}))
    respx.post("http://cp.test/admin/projects/p/activations").mock(return_value=httpx.Response(200, json={}))


@respx.mock
def test_pulse_curates_the_strip(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    respx.get("http://cp.test/market/asset-classes").mock(return_value=httpx.Response(200, json={
        "as_of": "2026-07-05", "source": "Yahoo",
        "groups": [{"name": "주가지수", "members": [
            {"label": "S&P 500", "ticker": "^GSPC", "price": 6213.0, "change_percent": -0.4},
            {"label": "러셀 2000", "ticker": "^RUT", "price": 2996.0, "change_percent": 0.2},
            {"label": "KOSPI", "ticker": "^KS11", "price": 3120.0, "change_percent": 1.1},
        ]}, {"name": "환율", "members": [
            {"label": "USD/KRW", "ticker": "KRW=X", "price": 1368.0, "change_percent": 0.1}]}],
    }))
    r = client.get("/market/pulse", headers=_hdr("mkt@u.com"))
    assert r.status_code == 200
    labels = [i["label"] for i in r.json()["items"]]
    assert labels == ["S&P 500", "KOSPI", "USD/KRW"]      # curated order, 러셀 제외
    assert r.json()["as_of"] == "2026-07-05"
    # second call is served from the 60s cache (no new upstream hit)
    before = respx.calls.call_count
    assert client.get("/market/pulse", headers=_hdr("mkt@u.com")).status_code == 200
    assert respx.calls.call_count == before


@respx.mock
def test_watch_snapshots_the_users_tickers(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    # a watchlist with one KR + one US ticker
    r = client.post("/watchlists", headers=_hdr("mkt2@u.com"), json={"name": "코어"})
    assert r.status_code == 200, r.text
    wl = r.json()["id"]
    for it in ({"market": "KR", "ticker": "005930.KS", "name": "삼성전자"},
               {"market": "US", "ticker": "AAPL", "name": "Apple"}):
        assert client.post(f"/watchlists/{wl}/items", headers=_hdr("mkt2@u.com"), json=it).status_code == 200
    respx.get("http://cp.test/prices/snapshot").mock(side_effect=lambda req: httpx.Response(200, json={
        "snapshot": {"price": 100.0, "day_change_percent": 1.5, "source": "Yahoo Finance"}}))
    out = client.get("/market/watch", headers=_hdr("mkt2@u.com")).json()["tickers"]
    assert {t["ticker"] for t in out} == {"005930", "AAPL"}   # .KS suffix stripped, deduped
    assert all(t["change_percent"] == 1.5 for t in out)
