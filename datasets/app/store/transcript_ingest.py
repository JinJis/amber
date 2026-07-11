"""Earnings-call transcripts → RAG corpus (Phase 1 of the research-grade expansion).

Mirrors ``filing_ingest``: pull a ticker's recent quarterly transcripts (Alpha Vantage), index their
text into RAG with provenance, and warm the in-app HTML preview. The transcript carries a synthetic
``accession`` ``TR:{ticker}:{quarter}`` so the SAME evidence chain that opens a filing opens the
transcript — the agent can quote management/analyst remarks and the user verifies them in-app.

US + KR coverage. Primary source is API Ninjas (KR calls ride Yahoo-style codes — 005930.KS /
.KQ — and are held in English); Alpha Vantage remains the free US-only fallback.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from app.config import settings
from app.providers.transcripts import has_transcript_key, recent_transcripts, transcripts_cover_kr
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.store.news_ingest import _ingest_to_rag  # reuse the RAG /rag/ingest POST helper
from app.store.transcript_html import make_accession, store_transcript_html

log = logging.getLogger(__name__)

_SECTION_CHARS = 6000   # section size cap (cut only at speaker-turn boundaries; RAG sub-chunks within)


def _transcript_to_docs(t: dict) -> list[dict]:
    """A transcript → section-sized RAG IngestDocs. `accession` TR:{ticker}:{quarter} routes the
    in-app preview through the existing /evidence/html chain; `section` names the speaker so a hit
    reads "CEO's remark", not "s.7". RQ-2: never split a speaker turn across sections — accumulate
    WHOLE turns, and start a new section on the size cap only at a turn boundary (a lone turn that
    itself exceeds the cap becomes its own oversized section; RAG sub-chunks it on sentences)."""
    accession = make_accession(t["ticker"], t["quarter"])
    turns: list[tuple[str, str]] = []
    for s in t.get("segments") or []:
        spk = (s.get("speaker") or "").strip()
        content = str(s.get("content", "")).strip()
        if content:
            turns.append((spk, f"{spk}: {content}" if spk else content))

    blocks: list[tuple[str, str]] = []   # (first_speaker, text)
    cur, cur_spk, size = [], "", 0
    for spk, line in turns:
        if cur and size + len(line) > _SECTION_CHARS:   # cut BETWEEN turns, never inside one
            blocks.append((cur_spk, "\n\n".join(cur)))
            cur, cur_spk, size = [], "", 0
        if not cur:
            cur_spk = spk
        cur.append(line)
        size += len(line)
    if cur:
        blocks.append((cur_spk, "\n\n".join(cur)))

    out: list[dict] = []
    for i, (spk, blk) in enumerate(blocks, 1):
        if len(blk.strip()) < 50:
            continue
        out.append({"text": blk, "source": t["source"], "doc_type": "transcript",
                    "doc_id": f"{accession}:s.{i}", "ticker": t["ticker"], "market": "US",
                    "accession": accession, "section": (spk or f"s.{i}")[:80],
                    "as_of": t.get("as_of") or t["quarter"]})
    return out


async def ingest_transcript_for_ticker(market: str, ticker: str, limit: int | None = None,
                                       rag_url: str | None = None, mode: str = "full") -> int:
    """Index a ticker's recent earnings-call transcripts into RAG + warm the preview cache; return
    the chunk count. US + KR (API Ninjas; AV fallback US). Best-effort (0 on no key / no data).
    ``mode="delta"`` skips quarters already ingested (IngestState cursor)."""
    from app.store.ingest_state import done_items, mark_items

    market = (market or "").upper()
    if market == "KR" and not transcripts_cover_kr():
        return 0
    limit = limit or settings.transcript_ingest_limit
    sym = ticker
    if market == "KR" and "." not in ticker:
        sym = f"{ticker}.KS"          # KOSPI first; KOSDAQ names retry below
    transcripts = await recent_transcripts(sym, limit)
    if not transcripts and market == "KR" and sym.endswith(".KS"):
        transcripts = await recent_transcripts(f"{ticker}.KQ", limit)
    if mode == "delta":
        seen = await asyncio.to_thread(done_items, "transcript", market, ticker)
        transcripts = [t for t in transcripts if str(t.get("quarter")) not in seen]
        if not transcripts:
            return 0   # nothing new — the caller logs the skip
    rag = rag_url or settings.rag_url
    total_docs, chunks = 0, 0
    for t in transcripts:
        await store_transcript_html(t)   # render + cache so the in-app preview is ready
        docs = _transcript_to_docs(t)
        if not docs:
            continue
        total_docs += len(docs)
        # replace by TR:{ticker}:{quarter} so a re-chunk (turn-preserving) swaps sections cleanly (RQ-2)
        chunks += await _ingest_to_rag(rag, docs, replace={"accession": docs[0]["accession"]})
        # ING-1: mark per-quarter right after success (partial failure re-does only the rest)
        await asyncio.to_thread(mark_items, "transcript", market, ticker, {str(t.get("quarter"))})
    if not total_docs:
        return 0
    log.info("transcript: %s → %d quarters, %d chunks indexed", ticker.upper(), len(transcripts), chunks)
    return chunks


async def run_transcript_text_ingest(market: str, tickers: list[str], mode: str = "full") -> None:
    """Index each ticker's recent earnings-call transcripts into RAG, tracked as an IngestionJob
    (kind `transcript`); best-effort per ticker, with a live activity feed. delta = new quarters only."""
    market = (market or "").upper()
    tickers = tickers or []
    delta = mode == "delta"
    job = start_job("transcript", market,
                    f"transcript · {len(tickers)} tickers" + (" · delta" if delta else ""), len(tickers))
    if market == "KR" and not transcripts_cover_kr():
        await asyncio.to_thread(finish_job, job, "success", 0,
                                "KR 어닝콜은 API_NINJAS_KEY(프리미엄)가 필요해요 — US는 AV 폴백으로 가능")
        return
    if not has_transcript_key():
        await asyncio.to_thread(finish_job, job, "error", 0,
                                "API_NINJAS_KEY(권장) 또는 ALPHAVANTAGE_API_KEY를 .env에 넣으면 인덱싱됩니다")
        return
    log_activity("transcript", market, f"▶ 시작 · {len(tickers)}종목 · 어닝콜 트랜스크립트 → RAG", job_id=job)
    total = 0
    failed: dict[str, str] = {}
    empty: list[str] = []
    try:
        for i, tk in enumerate(tickers, 1):
            await asyncio.to_thread(log_activity, "transcript", market,
                                    f"[{tk}] 어닝콜 트랜스크립트 수집·인덱싱 중… ({i}/{len(tickers)})", job)
            try:
                got = await ingest_transcript_for_ticker(market, tk, mode=mode)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "transcript", market,
                                            f"[{tk}] " + ("변경 없음 (델타 스킵)" if delta else "트랜스크립트 없음/제한 (0 chunks)"),
                                            job, "info" if delta else "warn")
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
