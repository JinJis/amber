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
_SECTION_CHARS = 6000    # max section size before a same-heading split (RAG sub-chunks within)

# Section headings inside a filing — SEC "Item 1A. Risk Factors" / "PART II", KR 공시 "제N장·절".
# Matching a heading starts a new SECTION so a hit points at a named region, not "s.7".
_HEADING = re.compile(
    r"^\s*(item\s+\d+[a-z]?\.?|part\s+[ivx]+\.?|제\s*\d+\s*[장절관]|[IVX]{1,4}\.\s+[A-Z])",
    re.IGNORECASE)


def _serialize_block(el) -> str:
    """One block element → text. TABLES become one ``cell | cell | cell`` line per row (so the
    RAG chunker keeps rows atomic and the lexical leg can match a figure to its row label);
    everything else is its collapsed visible text."""
    if el.tag == "table":
        rows = []
        for tr in el.iter("tr"):
            cells = [re.sub(r"\s+", " ", (c.text_content() or "").strip())
                     for c in tr.iter("td", "th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)
    return re.sub(r"[ \t]+", " ", (el.text_content() or "")).strip()


def _structured_blocks(root) -> list[str]:
    """Walk the filing body into ordered block strings: headings, paragraphs, and serialized
    tables. Falls back to a single text_content block if the DOM has no block structure."""
    blocks: list[str] = []
    seen_tables: set = set()
    for el in root.iter("h1", "h2", "h3", "h4", "p", "div", "table", "li"):
        if el.tag == "table":
            blocks.append(_serialize_block(el))
            seen_tables.add(el)
            continue
        # skip a container whose text is only its child table (avoid double-emitting the table text)
        if any(t in seen_tables for t in el.iter("table")):
            continue
        txt = _serialize_block(el)
        if txt:
            blocks.append(txt)
    if not blocks:
        txt = re.sub(r"[ \t]+", " ", root.text_content() or "").strip()
        blocks = [b for b in re.split(r"\n\s*\n+", txt) if b.strip()]
    return blocks


def _html_to_docs(html: str, market: str, ticker: str, accession: str, source: str,
                  url: str | None, doc_type: str = "filing") -> list[dict]:
    """Visible filing text from the markup → structure-aware section IngestDocs (RQ-2): sections
    break on real headings (Item 1A / 제N장), tables keep their rows, and the section carries the
    heading name so a hit points at a named region. RAG sub-chunks (heading-prefixed) within each."""
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

    blocks = _structured_blocks(root)
    if sum(len(b) for b in blocks) < _MIN_CHARS:
        return []

    # group blocks into sections: a heading starts a new section (naming it), and a section is
    # also cut when it grows past _SECTION_CHARS (same-heading overflow → …/2, …/3).
    sections: list[tuple[str, str]] = []  # (section_name, text)
    cur_name, cur_blocks, cur_size = "", [], 0

    def _flush():
        nonlocal cur_blocks, cur_size
        body = "\n\n".join(cur_blocks).strip()
        if len(body) >= _MIN_CHARS:
            sections.append((cur_name or f"s.{len(sections) + 1}", body))
        cur_blocks, cur_size = [], 0

    for blk in blocks:
        head = _HEADING.match(blk)
        if head and cur_blocks:                      # new named section boundary
            _flush()
            cur_name = re.sub(r"\s+", " ", blk[:80]).strip()
        elif head and not cur_blocks:
            cur_name = re.sub(r"\s+", " ", blk[:80]).strip()
        cur_blocks.append(blk)
        cur_size += len(blk)
        if cur_size >= _SECTION_CHARS:               # same-heading overflow split
            _flush()
    _flush()

    out: list[dict] = []
    for i, (name, body) in enumerate(sections, 1):
        out.append({"text": body, "source": source, "doc_type": doc_type,
                    # stable per (filing, ordinal) → re-ingest replaces by accession (RQ-2)
                    "doc_id": f"{accession}:s.{i}",
                    "ticker": ticker, "market": market, "accession": accession,
                    "section": name, "url": url})
    return out


async def ingest_filing_text_for_ticker(market: str, ticker: str, limit: int = 4,
                                        rag_url: str | None = None, mode: str = "full") -> int:
    """Fetch one ticker's recent filings as HTML (shared with the viewer, cached) and index their
    text into RAG; return the chunk count. The unit of both the batch pipeline AND on-demand
    ingest, so a ticker the corpus has never seen becomes searchable live. Best-effort (0 on fail).

    ``mode="delta"`` skips accessions this pipeline already ingested (the IngestState cursor) —
    a universe re-run then only downloads + embeds NEW filings instead of re-spending the
    OpenDART quota and embedding cost on unchanged ones. Both modes record the cursor."""
    from app.store.ingest_state import done_items, mark_items

    market = (market or "").upper()
    source = "SEC EDGAR" if market == "US" else "OpenDART (FSS)"
    refs = await filing_refs(market, ticker, limit)
    if mode == "delta":
        seen = await asyncio.to_thread(done_items, "filing_text", market, ticker)
        refs = {a: i for a, i in refs.items() if a not in seen}
        if not refs:
            return 0   # nothing new — the caller logs the skip
    # Ingest per accession with replace-by-accession, so re-chunking a filing swaps its old
    # sections for the fresh structure-aware set instead of leaving orphaned stale chunks (RQ-2).
    total_sections, chunks = 0, 0
    ingested: set[str] = set()
    rag = rag_url or settings.rag_url
    for accn, info in refs.items():
        html = await get_filing_html(market, accn, info.get("cik"), info.get("fetch_url"))
        if not html:
            continue
        docs = await asyncio.to_thread(
            _html_to_docs, html, market, ticker.upper(), accn, source, info.get("canonical"))
        if not docs:
            continue
        total_sections += len(docs)
        chunks += await _ingest_to_rag(rag, docs, replace={"accession": accn})
        ingested.add(accn)
    if ingested:
        await asyncio.to_thread(mark_items, "filing_text", market, ticker, ingested)
    if not total_sections:
        return 0
    log.info("filing-text: %s %s → %d sections, %d chunks indexed", market, ticker.upper(), total_sections, chunks)
    return chunks


async def run_filing_text_ingest(market: str, tickers: list[str], mode: str = "full") -> None:
    """Index each ticker's recent filings' text into RAG, tracked as an IngestionJob
    (kind `filing_text`); best-effort per ticker. ``mode="delta"`` = new filings only.

    Per-ticker outcomes are summarised into the job's ``error`` note (which the admin shows) so a
    run that indexed little/nothing reveals WHY — e.g. `RAG ingest timeout` (the RAG embed POST
    exceeded the timeout) or `no filing HTML` — instead of silently finishing as success/0."""
    market = (market or "").upper()
    tickers = tickers or []
    # a short, readable spec — NOT the full ticker join (which overflowed the varchar(256) spec
    # column for 200-500 tickers and made the INSERT fail before the job row even existed).
    delta = mode == "delta"
    job = start_job("filing_text", market,
                    f"filing_text · {len(tickers)} tickers" + (" · delta" if delta else ""), len(tickers))
    src = "SEC EDGAR" if market == "US" else "OpenDART"
    log_activity("filing_text", market,
                 f"▶ 시작 · {len(tickers)}종목 · 원천 {src} → RAG" + (" · 델타(새 공시만)" if delta else ""),
                 job_id=job)
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
                got = await ingest_filing_text_for_ticker(market, tk, mode=mode)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "filing_text", market,
                                            f"[{tk}] " + ("변경 없음 (델타 스킵)" if delta else "공시 본문 없음 (0 chunks)"),
                                            job, "info" if delta else "warn")
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
            label = "unchanged (delta)" if delta else "no filing text"
            note_parts.append(f"{label} ×{len(empty)} ({', '.join(empty[:8])}{'…' if len(empty) > 8 else ''})")
        note = " · ".join(note_parts)
        # a run where EVERY ticker failed is an error, not a quiet success — surface it as such.
        status = "error" if failed and ok == 0 and total == 0 else "success"
        await asyncio.to_thread(finish_job, job, status, total, note)
        await asyncio.to_thread(log_activity, "filing_text", market,
                                f"{'✓' if status == 'success' else '✗'} 완료 · {note}", job,
                                "info" if status == "success" else "error")
    except Exception:  # noqa: BLE001 — the loop itself blew up
        await asyncio.to_thread(finish_job, job, "error", total, traceback.format_exc()[-1800:])
