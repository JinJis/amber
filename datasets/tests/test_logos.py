"""Company-logo resolver + cache route. All upstreams (FMP profile, image CDN, favicon) are mocked
(respx); no network/key. Honesty: a miss returns 204 (UI draws a monogram), never a fake image."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routers import logos as L

client = TestClient(app)

# a minimal valid PNG (signature + IEND) — enough to pass the image content-type + size gate
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
) * 6  # ×6 so it clears the _MIN_BYTES floor


@pytest.fixture(autouse=True)
def _tmp_logo_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "_LOGO_DIR", tmp_path / "logos")
    monkeypatch.setattr(settings, "fmp_api_key", "k")
    monkeypatch.setattr(settings, "logodev_token", "")   # exercise the FMP/favicon path
    yield


def _fmp(profile: dict | None):
    body = [profile] if profile else []
    respx.get("https://financialmodelingprep.com/stable/profile").mock(
        return_value=httpx.Response(200, json=body))


@respx.mock
def test_resolves_from_fmp_image():
    _fmp({"symbol": "AAPL", "image": "https://img.fmp/AAPL.png", "website": "https://apple.com"})
    respx.get("https://img.fmp/AAPL.png").mock(
        return_value=httpx.Response(200, content=_PNG, headers={"content-type": "image/png"}))
    r = client.get("/logos", params={"market": "US", "ticker": "AAPL"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")
    assert r.content == _PNG
    # second call is served from the on-disk cache — no upstream needed (assert by clearing routes)
    respx.clear()
    r2 = client.get("/logos", params={"market": "US", "ticker": "AAPL"})
    assert r2.status_code == 200 and r2.content == _PNG


@respx.mock
def test_falls_back_to_domain_favicon():
    _fmp({"symbol": "MSFT", "website": "https://www.microsoft.com"})  # no image → domain favicon
    respx.get("https://www.google.com/s2/favicons").mock(
        return_value=httpx.Response(200, content=_PNG, headers={"content-type": "image/png"}))
    r = client.get("/logos", params={"market": "US", "ticker": "MSFT"})
    assert r.status_code == 200 and r.content == _PNG


@respx.mock
def test_kr_ticker_uses_domain_seed():
    # 005930.KS → market inferred KR → _KR_DOMAINS["005930"]=samsung.com → favicon
    fav = respx.get("https://www.google.com/s2/favicons").mock(
        return_value=httpx.Response(200, content=_PNG, headers={"content-type": "image/png"}))
    r = client.get("/logos", params={"market": "US", "ticker": "005930.KS"})
    assert r.status_code == 200 and r.content == _PNG
    assert fav.called and fav.calls.last.request.url.params["domain"] == "samsung.com"


def test_admin_upload_fills_a_gap_and_is_served():
    import base64
    data_url = "data:image/png;base64," + base64.b64encode(_PNG).decode()
    r = client.post("/logos", json={"market": "KR", "ticker": "999999.KS", "data_url": data_url})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["content_type"] == "image/png"
    # now the ticker serves the uploaded logo from cache — no resolver/network involved
    g = client.get("/logos", params={"market": "KR", "ticker": "999999.KS"})
    assert g.status_code == 200 and g.content == _PNG
    # a non-image upload is refused (never a fake/garbage logo)
    bad = "data:text/plain;base64," + base64.b64encode(b"not an image").decode()
    assert client.post("/logos", json={"market": "US", "ticker": "XX", "data_url": bad}).status_code == 422


@respx.mock
def test_miss_returns_204_and_caches_marker():
    _fmp(None)  # no profile, no domain → nothing resolvable
    r = client.get("/logos", params={"market": "US", "ticker": "ZZZZ"})
    assert r.status_code == 204
    # the miss is remembered → a second call short-circuits to 204 with no upstream
    respx.clear()
    r2 = client.get("/logos", params={"market": "US", "ticker": "ZZZZ"})
    assert r2.status_code == 204
