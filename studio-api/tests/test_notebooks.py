"""M-NB (NB-1) — notebooks: CRUD, block snapshots, 왜-담았는지 메모, 재정렬, 사용자 격리."""

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
    respx.post("http://cp.test/admin/tenants").mock(return_value=httpx.Response(200, json={"id": "t"}))
    respx.post("http://cp.test/admin/tenants/t/projects").mock(return_value=httpx.Response(200, json={"id": "p"}))
    respx.post("http://cp.test/admin/projects/p/keys").mock(return_value=httpx.Response(200, json={"api_key": "k"}))
    respx.post("http://cp.test/admin/projects/p/activations").mock(return_value=httpx.Response(200, json={}))


@respx.mock
def test_notebook_lifecycle_blocks_and_reorder(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    nb = client.post("/notebooks", headers=_hdr("nb@u.com"), json={"title": "반도체 리서치"}).json()

    # pin an artifact snapshot with the WHY note, then a text block between pins
    b1 = client.post(f"/notebooks/{nb['id']}/blocks", headers=_hdr("nb@u.com"), json={
        "kind": "pin_artifact", "note": "가이던스 상회 근거",
        "payload": {"kind": "table", "title": "AAPL 매출", "source": "SEC EDGAR", "as_of": "2026-07-01"}}).json()
    b2 = client.post(f"/notebooks/{nb['id']}/blocks", headers=_hdr("nb@u.com"), json={
        "kind": "text", "payload": {"md": "## 내 가설\nHBM 수요가 견조하다."}}).json()
    b3 = client.post(f"/notebooks/{nb['id']}/blocks", headers=_hdr("nb@u.com"), json={
        "kind": "pin_ledger", "payload": {"raw": "391.0B", "citation_idx": 1, "source": "SEC EDGAR"}}).json()

    doc = client.get(f"/notebooks/{nb['id']}", headers=_hdr("nb@u.com")).json()
    assert [b["kind"] for b in doc["block_list"]] == ["pin_artifact", "text", "pin_ledger"]
    assert doc["block_list"][0]["note"] == "가이던스 상회 근거"
    assert doc["block_list"][0]["payload"]["as_of"] == "2026-07-01"   # snapshot rides verbatim

    # reorder: move the ledger pin to the top (positions are sparse ints)
    client.patch(f"/notebooks/{nb['id']}/blocks/{b3['id']}", headers=_hdr("nb@u.com"), json={"position": 1})
    doc = client.get(f"/notebooks/{nb['id']}", headers=_hdr("nb@u.com")).json()
    assert doc["block_list"][0]["id"] == b3["id"]

    # text edits allowed; PIN SNAPSHOTS immutable (only the note may change)
    client.patch(f"/notebooks/{nb['id']}/blocks/{b2['id']}", headers=_hdr("nb@u.com"), json={"md": "수정"})
    assert client.patch(f"/notebooks/{nb['id']}/blocks/{b1['id']}", headers=_hdr("nb@u.com"),
                        json={"md": "해킹"}).status_code == 422
    client.patch(f"/notebooks/{nb['id']}/blocks/{b1['id']}", headers=_hdr("nb@u.com"), json={"note": "수정된 메모"})

    # delete a block, then the notebook (cascade)
    client.delete(f"/notebooks/{nb['id']}/blocks/{b2['id']}", headers=_hdr("nb@u.com"))
    assert client.get(f"/notebooks/{nb['id']}", headers=_hdr("nb@u.com")).json()["blocks"] == 2
    client.delete(f"/notebooks/{nb['id']}", headers=_hdr("nb@u.com"))
    assert client.get(f"/notebooks/{nb['id']}", headers=_hdr("nb@u.com")).status_code == 404


@respx.mock
def test_notebook_user_isolation_and_validation(monkeypatch):
    from studioapi.config import settings
    monkeypatch.setattr(settings, "control_plane_url", "http://cp.test")
    _cp()
    nb = client.post("/notebooks", headers=_hdr("owner@u.com"), json={"title": "내 노트"}).json()
    # another user can't see/mutate it
    assert client.get(f"/notebooks/{nb['id']}", headers=_hdr("thief@u.com")).status_code == 404
    assert client.delete(f"/notebooks/{nb['id']}", headers=_hdr("thief@u.com")).status_code == 404
    # unknown block kind / empty text rejected
    assert client.post(f"/notebooks/{nb['id']}/blocks", headers=_hdr("owner@u.com"),
                       json={"kind": "widget", "payload": {}}).status_code == 422
    assert client.post(f"/notebooks/{nb['id']}/blocks", headers=_hdr("owner@u.com"),
                       json={"kind": "text", "payload": {"md": "  "}}).status_code == 422
