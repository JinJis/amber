"""SC-0.4/ME-9: the log formatter redacts secrets (upstream API keys in URL query params + DB DSN
passwords) so they never land in `docker logs`."""

from __future__ import annotations

from app.logging_config import _redact


def test_redacts_query_param_keys():
    out = _redact("HTTP Request: GET https://opendart.fss.or.kr/api/list.json?crtfc_key=DEADBEEF&corp=005930")
    assert "crtfc_key=***" in out and "DEADBEEF" not in out
    assert "corp=005930" in out  # non-secret params survive


def test_redacts_dsn_password():
    out = _redact("connect postgresql+psycopg://rag:rag@postgres:5432/datasets")
    assert "rag:***@" in out and "rag:rag@" not in out


def test_leaves_clean_lines_untouched():
    line = "← GET /health 200 1.2ms rid=abc123"
    assert _redact(line) == line
