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
    """The cached deck PDF bytes for a `DECK:…` accession (served to the in-app pdf.js viewer).

    Cache-first, then on-demand: on a miss, re-resolve the ticker's recent decks from SEC and
    fetch the matching accession's PDF into the cache (mirrors get_filing_html's shape) — a cited
    deck still opens even when the ingest-time cache was lost or lives on another host."""
    parsed = parse_accession(syn_accession)
    if not parsed:
        return None
    ticker, accession = parsed
    path = _cache_path(ticker, accession)
    if path.exists():
        return await asyncio.to_thread(path.read_bytes)
    try:
        decks = await recent_decks(ticker, 8)   # wider than the ingest default — older citations
    except Exception as exc:  # noqa: BLE001 — upstream/network → graceful (None → 204)
        log.info("deck refetch: recent_decks failed for %s: %s", ticker, exc)
        return None
    row = next((d for d in decks or []
                if d.get("accession") == accession and d.get("pdf_url")), None)
    if not row:
        return None
    pdf = await _fetch_pdf(row["pdf_url"])
    if not pdf:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_bytes, pdf)
    log.info("deck pdf refetched %s %s (%d KB)", ticker, accession, len(pdf) // 1024)
    return pdf


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
                                 rag_url: str | None = None, mode: str = "full") -> int:
    """Index a ticker's recent 8-K presentation decks into RAG + cache each PDF for preview; return
    the chunk count. US only; needs Document AI. Best-effort (0 on no config / no decks).
    ``mode="delta"`` skips decks already ingested (IngestState cursor) — Document AI parsing is
    the expensive step, so a universe re-run only parses NEW decks."""
    from app.store.ingest_state import done_items, mark_items

    if (market or "").upper() != "US" or not docai_configured():
        return 0
    limit = limit or settings.deck_ingest_limit
    decks = await recent_decks(ticker, limit)
    if mode == "delta":
        seen = await asyncio.to_thread(done_items, "presentation", "US", ticker)
        decks = [d for d in decks if str(d.get("accession")) not in seen]
        if not decks:
            return 0   # nothing new — the caller logs the skip
    rag = rag_url or settings.rag_url
    total_docs, chunks = 0, 0
    ingested: set[str] = set()
    for d in decks:
        pdf = await _fetch_pdf(d["pdf_url"])
        if not pdf:
            continue
        path = _cache_path(d["ticker"], d["accession"])     # cache the PDF so the viewer can serve it
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, pdf)
        parsed = await parse_pdf(pdf)
        if not parsed:
            continue
        docs = _chunks_to_docs(d, parsed)
        if not docs:
            continue
        total_docs += len(docs)
        # replace by the deck's synthetic accession so a re-parse swaps its chunks cleanly (RQ-2)
        chunks += await _ingest_to_rag(rag, docs, replace={"accession": docs[0]["accession"]})
        ingested.add(str(d.get("accession")))
    if ingested:
        await asyncio.to_thread(mark_items, "presentation", "US", ticker, ingested)
    if not total_docs:
        return 0
    log.info("deck: %s → %d decks, %d chunks indexed", ticker.upper(), len(decks), chunks)
    return chunks


async def run_presentation_text_ingest(market: str, tickers: list[str], mode: str = "full") -> None:
    """Index each ticker's recent 8-K presentation decks into RAG, tracked as an IngestionJob
    (kind `presentation`); best-effort per ticker, with a live activity feed. delta = new decks only."""
    market = (market or "").upper()
    tickers = tickers or []
    delta = mode == "delta"
    job = start_job("presentation", market,
                    f"presentation · {len(tickers)} tickers" + (" · delta" if delta else ""), len(tickers))
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
                got = await ingest_deck_for_ticker(market, tk, mode=mode)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "presentation", market,
                                            f"[{tk}] " + ("변경 없음 (델타 스킵)" if delta else "발표자료 없음 (0 chunks)"),
                                            job, "info" if delta else "warn")
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
