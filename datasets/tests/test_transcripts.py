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
    monkeypatch.setattr(T.settings, "api_ninjas_key", "")
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "")
    assert await T.fetch_transcript("AAPL", "2024Q3") is None   # dark without a key, never fabricated


@pytest.mark.asyncio
@respx.mock
async def test_fetch_transcript_parses_segments(monkeypatch):
    monkeypatch.setattr(T.settings, "api_ninjas_key", "")
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "demo")
    respx.get(T._AV_URL).mock(return_value=httpx.Response(200, json=_SAMPLE))
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
    monkeypatch.setattr(T.settings, "api_ninjas_key", "")
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "demo")
    monkeypatch.setattr(TH.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(TI.settings, "transcript_ingest_limit", 1)
    respx.get(T._AV_URL).mock(return_value=httpx.Response(200, json=_SAMPLE))
    rag = respx.post("http://rag.test/rag/ingest").mock(return_value=httpx.Response(200, json={"chunks": 2}))

    n = await TI.ingest_transcript_for_ticker("US", "AAPL", rag_url="http://rag.test")
    assert n == 2 and rag.called
    # preview cache is warm → the /evidence/html TR: branch can serve it
    html = await TH.get_transcript_html("AAPL", "2024Q3")
    assert html is not None and "Tim Cook" in html


@pytest.mark.asyncio
async def test_ingest_for_ticker_kr_noop_without_ninjas_key(monkeypatch):
    monkeypatch.setattr(T.settings, "api_ninjas_key", "")
    assert await TI.ingest_transcript_for_ticker("KR", "005930") == 0   # KR needs API Ninjas


_NINJAS_SAMPLE = {
    "date": "2024-05-02", "ticker": "AAPL", "year": "2024", "quarter": "2",
    "transcript": ("Suhasini Chandramouli: Good afternoon, and welcome to the call.\n"
                   "Tim Cook: Thank you. Revenue set a March quarter record in more than two dozen countries.\n"
                   "Luca Maestri: Revenue for the March quarter was $90.8 billion."),
}


def test_parse_turns_splits_speakers_and_keeps_preamble():
    segs = T.parse_turns(_NINJAS_SAMPLE["transcript"])
    assert [x["speaker"] for x in segs] == ["Suhasini Chandramouli", "Tim Cook", "Luca Maestri"]
    assert "90.8 billion" in segs[2]["content"]
    # no recognizable turns → one speaker-less segment, never dropped
    lone = T.parse_turns("just a raw paragraph with no speakers")
    assert len(lone) == 1 and lone[0]["speaker"] == ""


@pytest.mark.asyncio
@respx.mock
async def test_fetch_prefers_api_ninjas_and_carries_call_date(monkeypatch):
    monkeypatch.setattr(T.settings, "api_ninjas_key", "nk")
    monkeypatch.setattr(T.settings, "alphavantage_api_key", "demo")  # must NOT be hit
    nin = respx.get(T._NINJAS_URL).mock(return_value=httpx.Response(200, json=_NINJAS_SAMPLE))
    got = await T.fetch_transcript("AAPL", "2024Q2")
    assert nin.called and got and got["source"].startswith("API Ninjas")
    assert got["as_of"] == "2024-05-02" and len(got["segments"]) == 3
    assert nin.calls.last.request.url.params["quarter"] == "2"


@pytest.mark.asyncio
@respx.mock
async def test_kr_ingest_maps_code_to_ks_and_indexes(monkeypatch, tmp_path):
    monkeypatch.setattr(T.settings, "api_ninjas_key", "nk")
    monkeypatch.setattr(TI.settings, "rag_url", "http://rag.test")
    monkeypatch.setattr(TH.settings, "evidence_docs_dir", str(tmp_path))
    kr = dict(_NINJAS_SAMPLE, ticker="005930.KS")
    nin = respx.get(T._NINJAS_URL).mock(return_value=httpx.Response(200, json=kr))
    rag = respx.post("http://rag.test/rag/ingest").mock(return_value=httpx.Response(200, json={"chunks": 2}))
    got = await TI.ingest_transcript_for_ticker("KR", "005930", limit=1)
    assert got > 0 and rag.called
    assert nin.calls[0].request.url.params["ticker"] == "005930.KS"   # bare code → .KS
