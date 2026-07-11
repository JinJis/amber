"""Unit tests for the OpenDART quota management (사용한도 초과 → per-key block + rotation).

OpenDART signals errors as HTTP 200 + a small XML envelope instead of the requested payload.
When a key's daily quota is spent (status 020) that KEY is blocked until the KST-midnight
reset and requests rotate to the next configured key (OPENDART_API_KEYS). With every key
spent, consumers fail fast — the document fetch degrades to None (viewer falls back to the
external link) and the JSON endpoints raise the honest 503 — instead of burning calls.
Read endpoints (list.json / fnlttSinglAcntAll.json / company.json) are response-cached so
repeat pulls don't re-spend quota.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import app.providers.kr.opendart as od
from app.cache import cache
from app.errors import APIError
from app.providers.kr import dart_document as DD

ERR_XML_QUOTA = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<result><status>020</status><message>사용한도를 초과하였습니다.</message></result>'
                 ).encode("utf-8")
ERR_XML_NODATA = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<result><status>013</status><message>조회된 데이타가 없습니다.</message></result>'
                  ).encode("utf-8")


def _zip_payload() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("body.xml", '<?xml version="1.0" encoding="UTF-8"?><BODY>사업의 내용</BODY>')
        zf.writestr("audit.html", "<p>감사보고서</p>")
        zf.writestr("readme.txt", "not markup — skipped")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _reset_quota(monkeypatch):
    # per-key block state + the response cache are module-level — never let one test poison
    # the rest of the suite. Default: one configured key.
    monkeypatch.setattr(od.settings, "opendart_api_key", "test-key")
    monkeypatch.setattr(od.settings, "opendart_api_keys", "", raising=False)
    od.reset_quota_blocks()
    cache.clear()
    yield
    od.reset_quota_blocks()
    cache.clear()


def _fake_bytes(payload: bytes):
    async def fetch(provider, url, **kw):
        return payload
    return fetch


# --- key pool ---------------------------------------------------------------------
def test_key_pool_falls_back_to_single_key(monkeypatch):
    assert od._keys() == ["test-key"]
    monkeypatch.setattr(od.settings, "opendart_api_keys", "k1, k2 ,k3")
    assert od._keys() == ["k1", "k2", "k3"]
    assert od.available_keys() == ["k1", "k2", "k3"]


def test_mark_quota_blocked_round_trip():
    assert od.quota_blocked() is False
    od.mark_quota_blocked()               # no arg = block every configured key
    assert od.quota_blocked() is True
    od.reset_quota_blocks()
    assert od.quota_blocked() is False


def test_per_key_block_leaves_others_available(monkeypatch):
    monkeypatch.setattr(od.settings, "opendart_api_keys", "k1,k2")
    od.mark_quota_blocked("k1")
    assert od.available_keys() == ["k2"]
    assert od.quota_blocked() is False     # a key remains → not globally blocked
    od.mark_quota_blocked("k2")
    assert od.quota_blocked() is True


def test_kst_midnight_block_window():
    # the block must last until (at most) the next KST midnight + safety margin
    assert 0 < od._seconds_to_kst_midnight() <= 24 * 3600 + 61


# --- fetch_document_markup ----------------------------------------------------------
@pytest.mark.asyncio
async def test_document_quota_error_returns_none_and_marks_block(monkeypatch):
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(ERR_XML_QUOTA))
    assert od.quota_blocked() is False
    assert await DD.fetch_document_markup("20250318001192") is None
    assert od.quota_blocked() is True   # the only key is spent → consumers fail fast


@pytest.mark.asyncio
async def test_document_rotates_to_next_key_on_quota(monkeypatch):
    monkeypatch.setattr(od.settings, "opendart_api_keys", "k1,k2")
    zip_payload = _zip_payload()

    async def fetch(provider, url, **kw):
        return ERR_XML_QUOTA if "crtfc_key=k1" in url else zip_payload
    monkeypatch.setattr(DD, "fetch_bytes", fetch)

    out = await DD.fetch_document_markup("20250318001192")
    assert out is not None and "사업의 내용" in out   # served by k2 after k1 hit 020
    assert od.available_keys() == ["k2"]              # k1 parked until the KST reset
    assert od.quota_blocked() is False


@pytest.mark.asyncio
async def test_document_no_data_error_does_not_mark_block(monkeypatch):
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(ERR_XML_NODATA))
    assert await DD.fetch_document_markup("20250318001192") is None
    assert od.quota_blocked() is False   # 013 is per-document, not a key-wide block


@pytest.mark.asyncio
async def test_document_skips_fetch_while_quota_blocked(monkeypatch):
    async def boom(provider, url, **kw):
        raise AssertionError("fetch_bytes must not run while the quota block holds")
    monkeypatch.setattr(DD, "fetch_bytes", boom)
    od.mark_quota_blocked()
    assert await DD.fetch_document_markup("20250318001192") is None


@pytest.mark.asyncio
async def test_document_zip_happy_path_combines_files(monkeypatch):
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(_zip_payload()))
    out = await DD.fetch_document_markup("20250318001192")
    assert out is not None
    assert "사업의 내용" in out and "감사보고서" in out       # both files concatenated
    assert "<?xml" not in out                                # declarations dropped for nesting
    assert od.quota_blocked() is False


# --- _parse_error_envelope ----------------------------------------------------------
def test_parse_error_envelope_reads_status_and_message():
    status, message = od._parse_error_envelope(ERR_XML_QUOTA)
    assert status == "020" and "사용한도" in message


def test_parse_error_envelope_is_none_for_real_payloads():
    # a real ZIP (corpCode.xml / document.xml success) must never parse as an error
    assert od._parse_error_envelope(_zip_payload()) == (None, None)
    assert od._parse_error_envelope(b"not xml at all") == (None, None)
    # oversized bodies are data, not an error envelope
    assert od._parse_error_envelope(b"<?xml " + b"a" * 4096) == (None, None)


# --- _dart_json: fail-fast, rotation, response caching ------------------------------
@pytest.mark.asyncio
async def test_dart_json_fails_fast_while_quota_blocked(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("fetch_json must not be called while quota-blocked")
    monkeypatch.setattr(od, "fetch_json", boom)
    od.mark_quota_blocked()
    with pytest.raises(APIError) as ei:
        await od._dart_json("list.json", {"corp_code": "00126380"})
    assert ei.value.status_code == 503 and "020" in ei.value.message


@pytest.mark.asyncio
async def test_dart_json_rotates_keys_on_quota(monkeypatch):
    monkeypatch.setattr(od.settings, "opendart_api_keys", "k1,k2")
    calls = []

    async def fetch(provider, url, params=None, **kw):
        calls.append(params["crtfc_key"])
        if params["crtfc_key"] == "k1":
            return {"status": "020", "message": "사용한도를 초과하였습니다."}
        return {"status": "000", "list": [{"corp_code": "x"}]}
    monkeypatch.setattr(od, "fetch_json", fetch)

    data = await od._dart_json("list.json", {"corp_code": "00000001"})
    assert data["list"] and calls == ["k1", "k2"]     # rotated within one request
    assert od.available_keys() == ["k2"]


@pytest.mark.asyncio
async def test_dart_json_caches_read_endpoints(monkeypatch):
    calls = []

    async def fetch(provider, url, params=None, **kw):
        calls.append(url)
        return {"status": "000", "list": [{"account_nm": "매출액"}]}
    monkeypatch.setattr(od, "fetch_json", fetch)

    p = {"corp_code": "00000002", "bsns_year": "2025", "reprt_code": "11011", "fs_div": "CFS"}
    a = await od._dart_json("fnlttSinglAcntAll.json", p)
    b = await od._dart_json("fnlttSinglAcntAll.json", p)      # cache hit — no second call
    assert a == b and len(calls) == 1
    # different params → its own entry
    await od._dart_json("fnlttSinglAcntAll.json", {**p, "fs_div": "OFS"})
    assert len(calls) == 2
