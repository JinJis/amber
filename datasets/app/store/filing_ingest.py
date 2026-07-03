"""Filing text → RAG corpus, from the ORIGINAL markup (no PDF, no Chromium, no PyMuPDF).

The same source markup the in-app viewer renders (US SEC iXBRL primary doc · KR OpenDART
document.xml) is the full-text corpus the agent searches. This extracts each recent filing's
visible text from that HTML and indexes it into RAG — so `rag__search` returns real filing
passages (MD&A, risk factors, notes, any line), grounded with provenance `{accession, section,
ticker, market, source}`. The same `accession` lets the viewer highlight the cited passage in the
very same document. Filings are public → indexed as a global (unscoped) corpus, like news.
"""

from __future__ import annotations

import asyncio
import logging
import re
import traceback

from lxml import html as lxml_html

from app.config import settings
from app.store.filing_html import get_filing_html
from app.store.filing_refs import filing_refs
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.store.news_ingest import _ingest_to_rag  # reuse the RAG /rag/ingest POST helper

log = logging.getLogger(__name__)

_MIN_CHARS = 50          # skip near-empty sections — not worth a chunk
_SECTION_CHARS = 4000    # target section size; RAG sub-chunks within each


def _html_to_docs(html: str, market: str, ticker: str, accession: str, source: str,
                  url: str | None, doc_type: str = "filing") -> list[dict]:
    """Visible filing text from the markup, split into section-sized RAG IngestDocs. `section`
    (s.N) lets a hit point back to a region; RAG sub-chunks within each for retrieval."""
    # US iXBRL primary docs begin with an `<?xml … encoding=…?>` declaration; lxml refuses to parse a
    # *Unicode* string that declares an encoding ("Unicode strings with encoding declaration are not
    # supported"), so US filings indexed 0 chunks. Strip the leading declaration before parsing — the
    # in-app viewer is unaffected (the browser handles the declaration); only this text-extraction path
    # tripped. (KR OpenDART document.xml has no such declaration.)
    html = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", html)
    try:
        root = lxml_html.fromstring(html)
    except Exception as exc:  # noqa: BLE001
        log.warning("filing-text: cannot parse %s %s: %s", market, accession, exc)
        return []
    for el in root.iter("script", "style"):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)
    text = re.sub(r"[ \t]+", " ", root.text_content() or "")
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    if len(text) < _MIN_CHARS:
        return []
    blocks: list[str] = []
    cur: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        cur.append(para)
        size += len(para)
        if size >= _SECTION_CHARS:
            blocks.append("\n\n".join(cur))
            cur, size = [], 0
    if cur:
        blocks.append("\n\n".join(cur))
    out: list[dict] = []
    for i, blk in enumerate(blocks, 1):
        blk = blk.strip()
        if len(blk) < _MIN_CHARS:
            continue
        out.append({"text": blk, "source": source, "doc_type": doc_type,
                    # stable per (filing, section) → re-ingest UPSERTs instead of duplicating
                    "doc_id": f"{accession}:s.{i}",
                    "ticker": ticker, "market": market, "accession": accession,
                    "section": f"s.{i}", "url": url})
    return out


async def ingest_filing_text_for_ticker(market: str, ticker: str, limit: int = 4,
                                        rag_url: str | None = None) -> int:
    """Fetch one ticker's recent filings as HTML (shared with the viewer, cached) and index their
    text into RAG; return the chunk count. The unit of both the batch pipeline AND on-demand
    ingest, so a ticker the corpus has never seen becomes searchable live. Best-effort (0 on fail)."""
    market = (market or "").upper()
    source = "SEC EDGAR" if market == "US" else "OpenDART (FSS)"
    refs = await filing_refs(market, ticker, limit)
    docs: list[dict] = []
    for accn, info in refs.items():
        html = await get_filing_html(market, accn, info.get("cik"), info.get("fetch_url"))
        if not html:
            continue
        docs += await asyncio.to_thread(
            _html_to_docs, html, market, ticker.upper(), accn, source, info.get("canonical"))
    if not docs:
        return 0
    chunks = await _ingest_to_rag(rag_url or settings.rag_url, docs)
    log.info("filing-text: %s %s → %d sections, %d chunks indexed", market, ticker.upper(), len(docs), chunks)
    return chunks


async def run_filing_text_ingest(market: str, tickers: list[str]) -> None:
    """Index each ticker's recent filings' text into RAG, tracked as an IngestionJob
    (kind `filing_text`); best-effort per ticker.

    Per-ticker outcomes are summarised into the job's ``error`` note (which the admin shows) so a
    run that indexed little/nothing reveals WHY — e.g. `RAG ingest timeout` (the RAG embed POST
    exceeded the timeout) or `no filing HTML` — instead of silently finishing as success/0."""
    market = (market or "").upper()
    tickers = tickers or []
    # a short, readable spec — NOT the full ticker join (which overflowed the varchar(256) spec
    # column for 200-500 tickers and made the INSERT fail before the job row even existed).
    job = start_job("filing_text", market, f"filing_text · {len(tickers)} tickers", len(tickers))
    src = "SEC EDGAR" if market == "US" else "OpenDART"
    log_activity("filing_text", market, f"▶ 시작 · {len(tickers)}종목 · 원천 {src} → RAG", job_id=job)
    total = 0
    failed: dict[str, str] = {}   # ticker → short failure reason (deduped in the note)
    empty: list[str] = []         # tickers that ran clean but produced no chunks
    try:
        for i, tk in enumerate(tickers, 1):
            # show what it's working on RIGHT NOW (which ticker, which upstream) before the calls
            await asyncio.to_thread(
                log_activity, "filing_text", market,
                f"[{tk}] {src} 공시 본문 수집·인덱싱 중… ({i}/{len(tickers)})", job)
            try:
                got = await ingest_filing_text_for_ticker(market, tk)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "filing_text", market,
                                            f"[{tk}] 공시 본문 없음 (0 chunks)", job, "warn")
                else:
                    await asyncio.to_thread(log_activity, "filing_text", market,
                                            f"[{tk}] {src} → RAG {got} chunks 인덱싱 ✓", job)
            except Exception as exc:  # noqa: BLE001 — one ticker never aborts the run
                reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                failed[tk] = reason or type(exc).__name__
                log.warning("filing-text: %s %s failed: %s", market, tk, reason)
                await asyncio.to_thread(log_activity, "filing_text", market,
                                        f"[{tk}] 실패 — {reason}", job, "error")
            await asyncio.to_thread(update_progress, job, i)
        # finalise with a human note: how many ok, what failed (with the actual error), what was empty.
        ok = len(tickers) - len(failed) - len(empty)
        note_parts = [f"{ok}/{len(tickers)} tickers indexed, {total} chunks"]
        if failed:
            # group identical reasons so a systemic failure (e.g. RAG timeout) reads at a glance
            by_reason: dict[str, list[str]] = {}
            for tk, r in failed.items():
                by_reason.setdefault(r, []).append(tk)
            note_parts.append("FAILED " + "; ".join(
                f"{r} ×{len(tks)} ({', '.join(tks[:8])}{'…' if len(tks) > 8 else ''})"
                for r, tks in by_reason.items()))
        if empty:
            note_parts.append(f"no filing text ×{len(empty)} ({', '.join(empty[:8])}{'…' if len(empty) > 8 else ''})")
        note = " · ".join(note_parts)
        # a run where EVERY ticker failed is an error, not a quiet success — surface it as such.
        status = "error" if failed and ok == 0 and total == 0 else "success"
        await asyncio.to_thread(finish_job, job, status, total, note)
        await asyncio.to_thread(log_activity, "filing_text", market,
                                f"{'✓' if status == 'success' else '✗'} 완료 · {note}", job,
                                "info" if status == "success" else "error")
    except Exception:  # noqa: BLE001 — the loop itself blew up
        await asyncio.to_thread(finish_job, job, "error", total, traceback.format_exc()[-1800:])
