"""M-SA — standing questions: 구독 CRUD·캡·중복 방지, signature 비교 → standing_update 카드."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from studioapi.db import init_db
from studioapi.main import app
from studioapi.standing import signature_of

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


def test_signature_prefers_latest_date_and_accession():
    a = signature_of({"filings": [{"accession_number": "0001-24-1", "filed": "2026-06-01"},
                                  {"accession_number": "0001-24-2", "filed": "2026-07-01"}]})
    b = signature_of({"filings": [{"accession_number": "0001-24-2", "filed": "2026-07-01"}]})
    c = signature_of({"filings": [{"accession_number": "0001-24-3", "filed": "2026-07-04"}]})
    assert a == b            # same latest identifiers → same signature (list shape irrelevant)
    assert a != c            # a NEW filing changes it
    assert signature_of({"note": "no dates here"}) is None


@respx.mock
def test_subscribe_dedup_cap_and_unsubscribe(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    body = {"question": "삼성전자 실적 어때?", "ticker": "005930", "market": "KR",
            "cadence": "event", "probe": {"path": "/filings", "args": {"ticker": "005930"}}}
    a = client.post("/standing", headers=_hdr("sa@u.com"), json=body).json()
    b = client.post("/standing", headers=_hdr("sa@u.com"), json=body).json()
    assert a["id"] == b["id"]                       # idempotent re-tap
    assert client.get("/standing", headers=_hdr("sa@u.com")).json()["standing"][0]["cadence"] == "event"
    assert client.post("/standing", headers=_hdr("sa@u.com"),
                       json={**body, "cadence": "hourly"}).status_code == 422
    client.delete(f"/standing/{a['id']}", headers=_hdr("sa@u.com"))
    assert client.get("/standing", headers=_hdr("sa@u.com")).json()["standing"] == []


@respx.mock
async def test_check_standing_baselines_then_cards_on_change(monkeypatch):
    from studioapi.config import settings
    from studioapi.deps import current_user
    from studioapi.standing import check_standing
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    client.post("/standing", headers=_hdr("sa2@u.com"), json={
        "question": "애플 새 공시 있어?", "ticker": "AAPL", "cadence": "event",
        "probe": {"path": "/filings", "args": {"ticker": "AAPL"}, "source": "SEC EDGAR"}})
    # resolve the provisioned user (carries api_key)
    from studioapi.db import SessionLocal
    from studioapi.models import User
    with SessionLocal() as db:
        user = db.get(User, "sa2@u.com")

    probe = respx.get("http://cp.test/filings").mock(return_value=httpx.Response(200, json={
        "filings": [{"accession_number": "0001-26-100", "filed": "2026-07-01"}]}))
    # first check: BASELINE only — nothing "changed" yet, no card
    assert await check_standing(user) == []
    # same payload again: still no card
    assert await check_standing(user) == []
    # a NEW filing appears → one standing_update card with the factual hook
    probe.mock(return_value=httpx.Response(200, json={
        "filings": [{"accession_number": "0001-26-101", "filed": "2026-07-05"}]}))
    cards = await check_standing(user)
    assert len(cards) == 1 and cards[0]["kind"] == "standing_update"
    assert cards[0]["question"] == "애플 새 공시 있어?"
    assert cards[0]["citations"][0]["source"] == "SEC EDGAR"
    assert "AAPL" in cards[0]["hook"]
