"""Earnings-call transcripts → RAG corpus (Phase 1 of the research-grade expansion).

Mirrors ``filing_ingest``: pull a ticker's recent quarterly transcripts (Alpha Vantage), index their
text into RAG with provenance, and warm the in-app HTML preview. The transcript carries a synthetic
``accession`` ``TR:{ticker}:{quarter}`` so the SAME evidence chain that opens a filing opens the
transcript — the agent can quote management/analyst remarks and the user verifies them in-app.

US coverage (Alpha Vantage). KR earnings-call transcripts are not freely API-available (see DART
filings/IR for the KR analog); this runner no-ops for KR.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from app.config import settings
from app.providers.transcripts import recent_transcripts
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.store.news_ingest import _ingest_to_rag  # reuse the RAG /rag/ingest POST helper
from app.store.transcript_html import make_accession, store_transcript_html

log = logging.getLogger(__name__)

_SECTION_CHARS = 4000   # target RAG section size (RAG sub-chunks within each)


def _transcript_to_docs(t: dict) -> list[dict]:
    """A transcript → section-sized RAG IngestDocs. `accession` TR:{ticker}:{quarter} routes the
    in-app preview through the existing /evidence/html chain; `section` (s.N) anchors a hit."""
    accession = make_accession(t["ticker"], t["quarter"])
    blocks: list[str] = []
    cur: list[str] = []
    size = 0
    for s in t.get("segments") or []:
        spk = s.get("speaker")
        line = f"{spk}: {s.get('content', '')}" if spk else str(s.get("content", ""))
        cur.append(line)
        size += len(line)
        if size >= _SECTION_CHARS:
            blocks.append("\n\n".join(cur))
            cur, size = [], 0
    if cur:
        blocks.append("\n\n".join(cur))
    out: list[dict] = []
    for i, blk in enumerate(blocks, 1):
        if len(blk.strip()) < 50:
            continue
        out.append({"text": blk, "source": t["source"], "doc_type": "transcript",
                    "doc_id": f"{accession}:s.{i}", "ticker": t["ticker"], "market": "US",
                    "accession": accession, "section": f"s.{i}", "as_of": t["quarter"]})
    return out


async def ingest_transcript_for_ticker(market: str, ticker: str, limit: int | None = None,
                                       rag_url: str | None = None) -> int:
    """Index a ticker's recent earnings-call transcripts into RAG + warm the preview cache; return
    the chunk count. US only (Alpha Vantage). Best-effort (0 on no key / no data)."""
    if (market or "").upper() != "US":
        return 0
    limit = limit or settings.transcript_ingest_limit
    transcripts = await recent_transcripts(ticker, limit)
    docs: list[dict] = []
    for t in transcripts:
        await store_transcript_html(t)   # render + cache so the in-app preview is ready
        docs += _transcript_to_docs(t)
    if not docs:
        return 0
    chunks = await _ingest_to_rag(rag_url or settings.rag_url, docs)
    log.info("transcript: %s → %d quarters, %d chunks indexed", ticker.upper(), len(transcripts), chunks)
    return chunks


async def run_transcript_text_ingest(market: str, tickers: list[str]) -> None:
    """Index each ticker's recent earnings-call transcripts into RAG, tracked as an IngestionJob
    (kind `transcript`); best-effort per ticker, with a live activity feed."""
    market = (market or "").upper()
    tickers = tickers or []
    job = start_job("transcript", market, f"transcript · {len(tickers)} tickers", len(tickers))
    if market != "US":
        await asyncio.to_thread(finish_job, job, "success", 0,
                                "KR 어닝콜 트랜스크립트는 무료 API 미제공 — US만 인덱싱 (KR은 DART 공시/IR 참고)")
        return
    if not settings.alphavantage_api_key:
        await asyncio.to_thread(finish_job, job, "error", 0,
                                "ALPHAVANTAGE_API_KEY 미설정 — 무료 키를 .env에 넣으면 인덱싱됩니다")
        return
    log_activity("transcript", market, f"▶ 시작 · {len(tickers)}종목 · Alpha Vantage 어닝콜 → RAG", job_id=job)
    total = 0
    failed: dict[str, str] = {}
    empty: list[str] = []
    try:
        for i, tk in enumerate(tickers, 1):
            await asyncio.to_thread(log_activity, "transcript", market,
                                    f"[{tk}] 어닝콜 트랜스크립트 수집·인덱싱 중… ({i}/{len(tickers)})", job)
            try:
                got = await ingest_transcript_for_ticker(market, tk)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "transcript", market,
                                            f"[{tk}] 트랜스크립트 없음/제한 (0 chunks)", job, "warn")
                else:
                    await asyncio.to_thread(log_activity, "transcript", market,
                                            f"[{tk}] → RAG {got} chunks ✓", job)
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                failed[tk] = reason or type(exc).__name__
                log.warning("transcript: %s failed: %s", tk, reason)
                await asyncio.to_thread(log_activity, "transcript", market, f"[{tk}] 실패 — {reason}", job, "error")
            await asyncio.to_thread(update_progress, job, i)
        ok = len(tickers) - len(failed) - len(empty)
        note = f"{ok}/{len(tickers)} tickers, {total} chunks"
        if empty:
            note += f" · 트랜스크립트 없음/제한 ×{len(empty)}"
        if failed:
            note += " · FAILED " + "; ".join(f"{r} ×{len(t)}" for r, t in
                                              {v: [k for k, x in failed.items() if x == v] for v in set(failed.values())}.items())
        status = "error" if failed and ok == 0 and total == 0 else "success"
        await asyncio.to_thread(finish_job, job, status, total, note)
        await asyncio.to_thread(log_activity, "transcript", market,
                                f"{'✓' if status == 'success' else '✗'} 완료 · {note}", job,
                                "info" if status == "success" else "error")
    except Exception:  # noqa: BLE001
        await asyncio.to_thread(finish_job, job, "error", total, traceback.format_exc()[-1800:])
