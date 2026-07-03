"""Earnings-call transcript ingestion (Phase 1): provider · HTML preview · RAG docs · evidence route.

Alpha Vantage + the RAG POST are mocked (respx); no network or key needed. End-to-end live data is
exercised by the user once a free ALPHAVANTAGE_API_KEY is set.
"""

from __future__ import annotations

import datetime

import httpx
import pytest
import respx

from app.providers import transcripts as T
from app.store import transcript_html as TH
from app.store import transcript_ingest as TI

_SAMPLE = {"symbol": "AAPL", "quarter": "2024Q3", "transcript": [
    {"speaker": "Tim Cook", "title": "CEO", "content": "Revenue grew on strong iPhone demand.", "sentiment": "0.6"},
    {"speaker": "Analyst", "title": "Morgan Stanley", "content": "What about gross margin guidance?", "sentiment": "0.1"},
]}


def test_recent_quarters_skips_current_and_counts_back():
    qs = T.recent_quarters(4, today=datetime.date(2026, 6, 28))  # Q2-2026 in progress → start Q1
    assert qs == ["2026Q1", "2025Q4", "2025Q3", "2025Q2"]


def test_accession_roundtrip():
    a = TH.make_accession("aapl", "2024Q3")
    assert a == "TR:AAPL:2024Q3"
    assert TH.parse_accession(a) == ("AAPL", "2024Q3")
    assert TH.parse_accession("0000320193-24-000123") is None   # a real filing accession → not a transcript


@pytest.mark.asyncio
async def test_fetch_transcript_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "")
    assert await T.fetch_transcript("AAPL", "2024Q3") is None   # dark without a key, never fabricated


@pytest.mark.asyncio
@respx.mock
async def test_fetch_transcript_parses_segments(monkeypatch):
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "demo")
    respx.get(T._URL).mock(return_value=httpx.Response(200, json=_SAMPLE))
    t = await T.fetch_transcript("AAPL", "2024Q3")
    assert t and t["ticker"] == "AAPL" and len(t["segments"]) == 2
    assert t["segments"][0]["speaker"] == "Tim Cook"


def test_render_html_is_sanitized_and_readable():
    html = TH.render({"ticker": "AAPL", "quarter": "2024Q3", "source": "Alpha Vantage",
                      "segments": _SAMPLE["transcript"]})
    assert "default-src 'none'" in html        # strict CSP, same as the filing viewer
    assert "Tim Cook" in html and "gross margin" in html
    assert "<script" not in html.lower()


def test_transcript_to_docs_chunks_with_synthetic_accession():
    docs = TI._transcript_to_docs({"ticker": "AAPL", "quarter": "2024Q3", "source": "AV",
                                   "segments": _SAMPLE["transcript"]})
    assert docs and all(d["doc_type"] == "transcript" and d["market"] == "US" for d in docs)
    assert docs[0]["accession"] == "TR:AAPL:2024Q3"
    assert docs[0]["doc_id"].startswith("TR:AAPL:2024Q3:s.")


@pytest.mark.asyncio
@respx.mock
async def test_ingest_for_ticker_indexes_and_warms_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "demo")
    monkeypatch.setattr(TH.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(TI.settings, "transcript_ingest_limit", 1)
    respx.get(T._URL).mock(return_value=httpx.Response(200, json=_SAMPLE))
    rag = respx.post("http://rag.test/rag/ingest").mock(return_value=httpx.Response(200, json={"chunks": 2}))

    n = await TI.ingest_transcript_for_ticker("US", "AAPL", rag_url="http://rag.test")
    assert n == 2 and rag.called
    # preview cache is warm → the /evidence/html TR: branch can serve it
    html = await TH.get_transcript_html("AAPL", "2024Q3")
    assert html is not None and "Tim Cook" in html


@pytest.mark.asyncio
async def test_ingest_for_ticker_kr_is_noop():
    assert await TI.ingest_transcript_for_ticker("KR", "005930") == 0   # AV transcripts are US-only
