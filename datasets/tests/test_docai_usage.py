"""COST-2 — Document AI 페이지 원가 계측.

Layout Parser는 페이지당 과금(~$0.01/page)이라 지금까지 원가 대시보드에 전혀 안 잡혔다.
파싱 성공 시 실제 페이지 수를 세어 control-plane으로 보고한다(콜당=페이지 과금 매칭).
"""

from __future__ import annotations

import io

from app.providers import document_ai as D


def _make_pdf(n: int) -> bytes:
    import pypdf
    w = pypdf.PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_count_pages():
    assert D._count_pages(_make_pdf(3)) == 3
    assert D._count_pages(_make_pdf(1)) == 1
    assert D._count_pages(b"not a pdf at all") == 0     # unreadable → 0 (no crash)


def test_report_docai_pages_emits(monkeypatch):
    captured: list = []
    import app.telemetry as T
    monkeypatch.setattr(T, "report_usage", lambda p: captured.append(p))
    D._report_docai_pages(12)
    assert len(captured) == 1
    p = captured[0]
    assert p["service"] == "datasets" and p["kind"] == "docai_pages"
    assert p["model"] == "document-ai-layout" and p["calls"] == 12
    assert p["estimated"] is False


def test_report_docai_pages_skips_non_positive(monkeypatch):
    captured: list = []
    import app.telemetry as T
    monkeypatch.setattr(T, "report_usage", lambda p: captured.append(p))
    D._report_docai_pages(0)
    D._report_docai_pages(-5)
    assert captured == []     # nothing processed → nothing billed
