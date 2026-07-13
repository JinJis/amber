"""SC-0.4/ME-9: the shared log-redaction port — secrets (URL query-param API keys + DB DSN
passwords) are stripped before a line reaches `docker logs`."""

from __future__ import annotations

from agentengine.logging_config import _redact


def test_redacts_query_param_keys():
    out = _redact("GET https://api.example.com/v1?api_key=SUPERSECRET&q=aapl")
    assert "api_key=***" in out and "SUPERSECRET" not in out
    assert "q=aapl" in out


def test_redacts_dsn_password():
    out = _redact("dsn=postgresql://rag:rag@postgres:5432/rag")
    assert "rag:***@" in out and "rag:rag@" not in out


def test_leaves_clean_lines_untouched():
    line = "planner: step 2/8 tool=prices rid=deadbeef"
    assert _redact(line) == line
