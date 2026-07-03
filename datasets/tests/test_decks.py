"""8-K presentation-deck ingestion (Phase 2): EX-99 PDF resolver · Document AI adapter · RAG docs.

EDGAR, the PDF fetch, Document AI, and the RAG POST are all mocked — no network / GCP / key needed.
Live parsing is exercised by the user once a Document AI processor is configured.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.providers import sec_decks as D
from app.store import deck_ingest as DI


def test_pick_deck_prefers_ex992_presentation_pdf():
    items = [
        {"name": "ex991-pressrelease.pdf", "type": "EX-99.1", "size": "20000"},
        {"name": "ex992-investor-presentation.pdf", "type": "EX-99.2", "size": "900000"},
        {"name": "form8k.htm", "type": "8-K", "size": "5000"},
        {"name": "logo.gif", "type": "GRAPHIC", "size": "3000"},
    ]
    pick = D._pick_deck(items)
    assert pick and pick["name"] == "ex992-investor-presentation.pdf"


def test_pick_deck_none_when_no_pdf():
    assert D._pick_deck([{"name": "form8k.htm", "type": "8-K"}]) is None


def test_deck_accession_roundtrip():
    a = DI.make_accession("aapl", "0000320193-24-000123")
    assert a == "DECK:AAPL:0000320193-24-000123"
    assert DI.parse_accession(a) == ("AAPL", "0000320193-24-000123")
    assert DI.parse_accession("TR:AAPL:2024Q3") is None   # a transcript, not a deck


@pytest.mark.asyncio
async def test_recent_decks_finds_8k_ex99(monkeypatch):
    monkeypatch.setattr(D, "_resolve_cik", lambda ref: _coro("0000320193"))
    monkeypatch.setattr(D, "_submissions", lambda cik: _coro({"filings": {"recent": {
        "form": ["10-Q", "8-K", "8-K"], "accessionNumber": ["a-10q", "a-8k1", "a-8k2"],
        "filingDate": ["2024-08-01", "2024-07-30", "2024-05-01"],
        "primaryDocDescription": ["10-Q", "Earnings", "Earnings"]}}}))

    async def fake_index(cik, accn):
        if accn == "a-8k1":
            return [{"name": "ex992-deck.pdf", "type": "EX-99.2", "size": "800000"}]
        return []   # the older 8-K has no deck
    monkeypatch.setattr(D, "_filing_index", fake_index)

    decks = await D.recent_decks("AAPL", limit=4)
    assert len(decks) == 1 and decks[0]["accession"] == "a-8k1"
    assert decks[0]["pdf_url"].endswith("/ex992-deck.pdf") and decks[0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_ingest_deck_dark_without_document_ai(monkeypatch):
    monkeypatch.setattr(DI, "docai_configured", lambda: False)
    assert await DI.ingest_deck_for_ticker("US", "AAPL") == 0   # no processor → feature dark


@pytest.mark.asyncio
@respx.mock
async def test_ingest_deck_parses_and_indexes(monkeypatch, tmp_path):
    monkeypatch.setattr(DI.settings, "evidence_docs_dir", str(tmp_path))
    monkeypatch.setattr(DI, "docai_configured", lambda: True)
    monkeypatch.setattr(DI, "recent_decks", lambda tk, limit: _coro([
        {"accession": "a-8k1", "ticker": "AAPL", "filed": "2024-07-30", "title": "Earnings",
         "pdf_url": "https://sec.test/ex992-deck.pdf"}]))
    monkeypatch.setattr(DI, "parse_pdf", lambda pdf: _coro([
        {"text": "AI revenue tripled year over year on HBM demand.", "page": 3, "bbox": None},
        {"text": "Operating margin expanded to 28%.", "page": 4, "bbox": None}]))
    respx.get("https://sec.test/ex992-deck.pdf").mock(
        return_value=httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7 ..."))
    rag = respx.post("http://rag.test/rag/ingest").mock(return_value=httpx.Response(200, json={"chunks": 2}))

    n = await DI.ingest_deck_for_ticker("US", "AAPL", rag_url="http://rag.test")
    assert n == 2 and rag.called
    # the deck PDF was cached → the /evidence/deck route can serve it to the pdf.js viewer
    pdf = await DI.get_deck_pdf("DECK:AAPL:a-8k1")
    assert pdf and pdf.startswith(b"%PDF")


def test_chunks_to_docs_carries_page_and_synthetic_accession():
    docs = DI._chunks_to_docs(
        {"ticker": "AAPL", "accession": "a-8k1", "pdf_url": "u", "filed": "2024-07-30"},
        [{"text": "A faithful chunk of at least forty characters of deck text.", "page": 3, "bbox": None}])
    assert docs and docs[0]["doc_type"] == "presentation" and docs[0]["accession"] == "DECK:AAPL:a-8k1"
    assert docs[0]["section"] == "p.3" and docs[0]["market"] == "US"


async def _coro(v):
    return v
