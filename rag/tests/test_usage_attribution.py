"""METER-3 — rag 원가 귀속 테스트.

report_usage가 현재 검색 요청의 project_id(게이트웨이가 X-Tenant-Id로 주입)를 실어 보내고,
백그라운드 인제스트(프로젝트 없음)는 None(공용)으로 남기는지 확인. embed/rerank/multi-query가
모두 이 한 경로(report_usage)를 타므로 여기서 한 번에 검증된다.
"""

from __future__ import annotations

import pytest

from rag import telemetry as T
from rag import usage_context as UC


@pytest.fixture(autouse=True)
def _reset_project():
    UC.set_project(None)
    yield
    UC.set_project(None)


# --- contextvar basics --------------------------------------------------------------------------

def test_contextvar_default_none_and_set():
    assert UC.current_project() is None
    UC.set_project("prj_1")
    assert UC.current_project() == "prj_1"
    UC.set_project("")          # empty string coerces to None
    assert UC.current_project() is None


# --- report_usage attribution (no running loop → POST skipped, setdefault still runs) ------------

def test_report_usage_attaches_current_project():
    UC.set_project("prj_abc")
    payload = {"service": "rag", "kind": "embed_query", "model": "gemini-embedding-2",
               "input_tokens": 10, "output_tokens": 0, "calls": 1, "estimated": True}
    T.report_usage(payload)
    assert payload["project_id"] == "prj_abc"


def test_report_usage_none_when_background():
    payload = {"service": "rag", "kind": "embed_docs", "model": "gemini-embedding-2"}
    T.report_usage(payload)
    assert payload["project_id"] is None       # background ingest = 공용/shared cost


def test_report_usage_does_not_override_explicit_project():
    UC.set_project("prj_ctx")
    payload = {"service": "rag", "kind": "rerank", "project_id": "prj_explicit"}
    T.report_usage(payload)
    assert payload["project_id"] == "prj_explicit"   # setdefault: an explicit value wins


# --- endpoint wiring: the gateway's X-Tenant-Id (== project_id) stamps the contextvar -----------

def test_search_endpoint_stamps_project_from_gateway_header(monkeypatch):
    from fastapi.testclient import TestClient

    from rag.main import app

    seen: list = []
    monkeypatch.setattr(UC, "set_project", lambda pid: seen.append(pid))
    client = TestClient(app)
    # empty store → fail-safe search returns []; we only assert the project was stamped first.
    r = client.post("/rag/search", json={"query": "삼성전자 공급망 리스크", "top_k": 3},
                    headers={"X-Tenant-Id": "prj_gw"})
    assert r.status_code == 200
    assert seen == ["prj_gw"]


def test_search_without_header_leaves_project_unset(monkeypatch):
    from fastapi.testclient import TestClient

    from rag.main import app

    seen: list = []
    monkeypatch.setattr(UC, "set_project", lambda pid: seen.append(pid))
    client = TestClient(app)
    r = client.post("/rag/search", json={"query": "no tenant header here", "top_k": 3})
    assert r.status_code == 200
    assert seen == [None]     # direct/dev caller (no gateway) → None = shared
