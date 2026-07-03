"""8-K investor/earnings presentation decks (PDF) → RAG (Phase 2).

Resolve a ticker's recent 8-K EX-99 presentation decks (``sec_decks``), fetch + cache each PDF (for
the in-app pdf.js preview), parse it with GCP Document AI Layout Parser (faithful, layout-aware
chunks WITH page anchors), and index the text into RAG. The deck carries a synthetic accession
``DECK:{ticker}:{accession}`` so the cited chunk opens the very PDF at the right page in-app.

US only (8-K is a US form). Needs a configured Document AI processor — without it the text isn't
parsed and the feature stays dark (never fabricated).
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import traceback

import httpx

from app.config import settings
from app.providers.document_ai import configured as docai_configured
from app.providers.document_ai import parse_pdf
from app.providers.sec_decks import recent_decks
from app.providers.us.sec_edgar import _UA
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.store.news_ingest import _ingest_to_rag

log = logging.getLogger(__name__)

_MAX_PDF = 40_000_000   # ~40 MB cap on a deck PDF


def make_accession(ticker: str, accession: str) -> str:
    return f"DECK:{ticker.upper()}:{accession}"


def parse_accession(syn: str) -> tuple[str, str] | None:
    """`DECK:AAPL:0000320193-24-000123` → ('AAPL', '0000320193-24-000123'); None if not a deck."""
    if not syn or not syn.startswith("DECK:"):
        return None
    parts = syn.split(":", 2)
    return (parts[1], parts[2]) if len(parts) == 3 else None


def _cache_path(ticker: str, accession: str) -> pathlib.Path:
    safe = accession.replace("/", "_")
    return pathlib.Path(settings.evidence_docs_dir) / "deck" / f"{ticker.upper()}_{safe}.pdf"


async def get_deck_pdf(syn_accession: str) -> bytes | None:
    """The cached deck PDF bytes for a `DECK:…` accession (served to the in-app pdf.js viewer)."""
    parsed = parse_accession(syn_accession)
    if not parsed:
        return None
    path = _cache_path(*parsed)
    if not path.exists():
        return None
    return await asyncio.to_thread(path.read_bytes)


async def _fetch_pdf(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(url, headers=_UA)
        if r.status_code != 200 or len(r.content) > _MAX_PDF:
            return None
        if "pdf" not in (r.headers.get("content-type", "").lower()) and not r.content[:5].startswith(b"%PDF"):
            return None
        return r.content
    except httpx.HTTPError as exc:
        log.info("deck pdf fetch failed %s: %s", url[:120], exc)
        return None


def _chunks_to_docs(deck: dict, chunks: list[dict]) -> list[dict]:
    accession = make_accession(deck["ticker"], deck["accession"])
    out: list[dict] = []
    for i, ch in enumerate(chunks, 1):
        text = (ch.get("text") or "").strip()
        if len(text) < 40:
            continue
        page = ch.get("page")
        out.append({"text": text, "source": "SEC 8-K (investor presentation)", "doc_type": "presentation",
                    "doc_id": f"{accession}:c{i}", "ticker": deck["ticker"], "market": "US",
                    "accession": accession, "section": (f"p.{page}" if page else f"c.{i}"),
                    "url": deck.get("pdf_url"), "as_of": deck.get("filed")})
    return out


async def ingest_deck_for_ticker(market: str, ticker: str, limit: int | None = None,
                                 rag_url: str | None = None) -> int:
    """Index a ticker's recent 8-K presentation decks into RAG + cache each PDF for preview; return
    the chunk count. US only; needs Document AI. Best-effort (0 on no config / no decks)."""
    if (market or "").upper() != "US" or not docai_configured():
        return 0
    limit = limit or settings.deck_ingest_limit
    decks = await recent_decks(ticker, limit)
    docs: list[dict] = []
    for d in decks:
        pdf = await _fetch_pdf(d["pdf_url"])
        if not pdf:
            continue
        path = _cache_path(d["ticker"], d["accession"])     # cache the PDF so the viewer can serve it
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, pdf)
        chunks = await parse_pdf(pdf)
        if chunks:
            docs += _chunks_to_docs(d, chunks)
    if not docs:
        return 0
    chunks = await _ingest_to_rag(rag_url or settings.rag_url, docs)
    log.info("deck: %s → %d decks, %d chunks indexed", ticker.upper(), len(decks), chunks)
    return chunks


async def run_presentation_text_ingest(market: str, tickers: list[str]) -> None:
    """Index each ticker's recent 8-K presentation decks into RAG, tracked as an IngestionJob
    (kind `presentation`); best-effort per ticker, with a live activity feed."""
    market = (market or "").upper()
    tickers = tickers or []
    job = start_job("presentation", market, f"presentation · {len(tickers)} tickers", len(tickers))
    if market != "US":
        await asyncio.to_thread(finish_job, job, "success", 0, "8-K 발표자료는 US 전용 (KR은 DART IR 참고)")
        return
    if not docai_configured():
        await asyncio.to_thread(finish_job, job, "error", 0,
                                "Document AI 미설정 — DOCAI_PROCESSOR_ID/SA를 설정하면 PDF 덱이 파싱됩니다")
        return
    log_activity("presentation", market, f"▶ 시작 · {len(tickers)}종목 · 8-K 덱 → Document AI → RAG", job_id=job)
    total = 0
    failed: dict[str, str] = {}
    empty: list[str] = []
    try:
        for i, tk in enumerate(tickers, 1):
            await asyncio.to_thread(log_activity, "presentation", market,
                                    f"[{tk}] 8-K 발표자료(PDF) 수집·파싱·인덱싱 중… ({i}/{len(tickers)})", job)
            try:
                got = await ingest_deck_for_ticker(market, tk)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "presentation", market,
                                            f"[{tk}] 발표자료 없음 (0 chunks)", job, "warn")
                else:
                    await asyncio.to_thread(log_activity, "presentation", market,
                                            f"[{tk}] → RAG {got} chunks ✓", job)
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                failed[tk] = reason or type(exc).__name__
                log.warning("deck: %s failed: %s", tk, reason)
                await asyncio.to_thread(log_activity, "presentation", market, f"[{tk}] 실패 — {reason}", job, "error")
            await asyncio.to_thread(update_progress, job, i)
        ok = len(tickers) - len(failed) - len(empty)
        note = f"{ok}/{len(tickers)} tickers, {total} chunks"
        if empty:
            note += f" · 발표자료 없음 ×{len(empty)}"
        status = "error" if failed and ok == 0 and total == 0 else "success"
        await asyncio.to_thread(finish_job, job, status, total, note)
        await asyncio.to_thread(log_activity, "presentation", market,
                                f"{'✓' if status == 'success' else '✗'} 완료 · {note}", job,
                                "info" if status == "success" else "error")
    except Exception:  # noqa: BLE001
        await asyncio.to_thread(finish_job, job, "error", total, traceback.format_exc()[-1800:])
