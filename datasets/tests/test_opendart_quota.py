"""Unit tests for the OpenDART quota-block (사용한도 초과) fail-fast path.

OpenDART signals errors as HTTP 200 + a small XML envelope instead of the requested payload.
When the daily quota is spent (status 020/021), EVERY OpenDART consumer must fail fast for a
while — the document fetch degrades to None (viewer falls back to the external link) and the
JSON endpoints raise the honest 503 — instead of burning further calls against a blocked key.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import app.providers.kr.opendart as od
from app.errors import APIError
from app.providers.kr import dart_document as DD

ERR_XML_QUOTA = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<result><status>020</status><message>사용한도를 초과하였습니다.</message></result>'
                 ).encode("utf-8")
ERR_XML_NODATA = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<result><status>013</status><message>조회된 데이타가 없습니다.</message></result>'
                  ).encode("utf-8")


@pytest.fixture(autouse=True)
def _reset_quota():
    # the block is module-level state — never let one test poison the rest of the suite
    od._quota_blocked_until = 0.0
    yield
    od._quota_blocked_until = 0.0


def _fake_bytes(payload: bytes):
    async def fetch(provider, url, **kw):
        return payload
    return fetch


# --- fetch_document_markup ------------------------------------------------------
@pytest.mark.asyncio
async def test_document_quota_error_returns_none_and_marks_block(monkeypatch):
    monkeypatch.setattr(DD.settings, "opendart_api_key", "test-key")
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(ERR_XML_QUOTA))
    assert od.quota_blocked() is False
    assert await DD.fetch_document_markup("20250318001192") is None
    assert od.quota_blocked() is True   # every OpenDART consumer now fails fast


@pytest.mark.asyncio
async def test_document_no_data_error_does_not_mark_block(monkeypatch):
    monkeypatch.setattr(DD.settings, "opendart_api_key", "test-key")
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(ERR_XML_NODATA))
    assert await DD.fetch_document_markup("20250318001192") is None
    assert od.quota_blocked() is False   # 013 is per-document, not a key-wide block


@pytest.mark.asyncio
async def test_document_skips_fetch_while_quota_blocked(monkeypatch):
    monkeypatch.setattr(DD.settings, "opendart_api_key", "test-key")

    async def boom(provider, url, **kw):
        raise AssertionError("fetch_bytes must not run while the quota block holds")
    monkeypatch.setattr(DD, "fetch_bytes", boom)
    od.mark_quota_blocked()
    assert await DD.fetch_document_markup("20250318001192") is None


@pytest.mark.asyncio
async def test_document_zip_happy_path_combines_files(monkeypatch):
    monkeypatch.setattr(DD.settings, "opendart_api_key", "test-key")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("body.xml", '<?xml version="1.0" encoding="UTF-8"?><BODY>사업의 내용</BODY>')
        zf.writestr("audit.html", "<p>감사보고서</p>")
        zf.writestr("readme.txt", "not markup — skipped")
    monkeypatch.setattr(DD, "fetch_bytes", _fake_bytes(buf.getvalue()))

    out = await DD.fetch_document_markup("20250318001192")
    assert out is not None
    assert "사업의 내용" in out and "감사보고서" in out       # both files concatenated
    assert "<?xml" not in out                                # declarations dropped for nesting
    assert od.quota_blocked() is False


# --- _parse_error_envelope --------------------------------------------------------
def test_parse_error_envelope_reads_status_and_message():
    status, message = od._parse_error_envelope(ERR_XML_QUOTA)
    assert status == "020" and "사용한도" in message


def test_parse_error_envelope_is_none_for_real_payloads():
    # a real ZIP (corpCode.xml / document.xml success) must never parse as an error
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", "<result/>")
    assert od._parse_error_envelope(buf.getvalue()) == (None, None)
    assert od._parse_error_envelope(b"not xml at all") == (None, None)
    # oversized bodies are data, not an error envelope
    assert od._parse_error_envelope(b"<?xml " + b"a" * 4096) == (None, None)


# --- _dart_json fail-fast + block round-trip ------------------------------------
@pytest.mark.asyncio
async def test_dart_json_fails_fast_while_quota_blocked(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("fetch_json must not be called while quota-blocked")
    monkeypatch.setattr(od, "fetch_json", boom)
    od.mark_quota_blocked()
    with pytest.raises(APIError) as ei:
        await od._dart_json("list.json", {"corp_code": "00126380"})
    assert ei.value.status_code == 503 and "020" in ei.value.message


def test_mark_quota_blocked_round_trip():
    assert od.quota_blocked() is False
    od.mark_quota_blocked()
    assert od.quota_blocked() is True
    od._quota_blocked_until = 0.0        # deadline passed → unblocked again
    assert od.quota_blocked() is False
