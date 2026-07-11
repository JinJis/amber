"""Unit tests for log redaction + crash-safety (`app.logging_config._SafeFormatter`).

Upstream API keys ride in URL query params and httpx logs the full request URL — the formatter
must redact key values so secrets never land in `docker logs`, and must never raise on a
malformed logging call.
"""

from __future__ import annotations

import logging

from app.logging_config import _BASE_FMT, _SafeFormatter


def _format(msg: str, args=None) -> str:
    record = logging.LogRecord("httpx", logging.INFO, __file__, 42, msg, args, None)
    return _SafeFormatter(_BASE_FMT, "%H:%M:%S").format(record)


def test_redacts_opendart_crtfc_key():
    out = _format('HTTP Request: GET https://opendart.fss.or.kr/api/document.xml'
                  '?crtfc_key=SECRET&rcept_no=20250318001192 "HTTP/1.1 200 OK"')
    assert "SECRET" not in out
    assert "crtfc_key=***" in out
    assert "rcept_no=20250318001192" in out      # non-secret params stay readable


def test_redacts_datagokr_servicekey_case_insensitively():
    out = _format("GET https://apis.data.go.kr/x?serviceKey=ABC123&y=1 · retry"
                  " · https://other.example/z?SERVICEKEY=DEF456")
    assert "ABC123" not in out and "DEF456" not in out
    assert out.count("=***") == 2


def test_redacts_generic_key_and_token_params():
    out = _format("https://a.example/q?api_key=K1&apikey=K2&token=T1&authKey=A1&plain=ok")
    for secret in ("K1", "K2", "T1", "A1"):
        assert secret not in out
    assert "plain=ok" in out


def test_malformed_logging_call_never_raises():
    # some libraries mis-call logging (bad printf args) — the formatter must degrade to a repr
    out = _format("%d rows fetched", ("not-an-int",))
    assert "%d rows fetched" in out              # the repr fallback still names the message
