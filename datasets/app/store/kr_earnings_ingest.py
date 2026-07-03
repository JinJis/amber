"""KR earnings disclosures (잠정실적 공정공시) → RAG corpus — the KR analog of the US earnings-call ingest.

There is no free KR earnings-call transcript/audio API (Alpha Vantage is US-only), so we index the
next-best free, point-in-time source: the '영업(잠정)실적(공정공시)' disclosure on DART — management's
own preliminary results + commentary, the KR equivalent of a US earnings press release/call. We fetch
the SAME OpenDART ``document.xml`` markup the in-app viewer renders, extract its text into RAG
(doc_type ``earnings``), and warm the viewer cache — so ``rag__search`` returns real earnings-
announcement passages and a citation highlights them in the original DART document (accession =
rcept_no, market = KR → the existing KR filing viewer path, no new viewer code).

KR only; US uses the Alpha Vantage transcript pipeline. Best-effort (0 on no key / no disclosure).
"""

from __future__ import annotations

import asyncio
import logging
import traceback

from app.config import settings
from app.providers.registry import get_financials_provider
from app.store.filing_html import get_filing_html
from app.store.filing_ingest import _html_to_docs
from app.store.jobs import finish_job, log_activity, start_job, update_progress
from app.store.news_ingest import _ingest_to_rag  # reuse the RAG /rag/ingest POST helper
from app.symbols import Market, build_ref

log = logging.getLogger(__name__)

_SOURCE = "OpenDART (잠정실적 공정공시)"


async def ingest_kr_earnings_for_ticker(market: str, ticker: str, limit: int | None = None,
                                        rag_url: str | None = None) -> int:
    """Index one KR ticker's recent 잠정실적 공정공시 into RAG + warm the viewer cache; return the
    chunk count. KR only. Best-effort (0 on no key / no disclosure / provider w/o the resolver)."""
    if (market or "").upper() != "KR":
        return 0
    limit = limit or settings.kr_earnings_ingest_limit
    prov = get_financials_provider(Market.KR)
    getter = getattr(prov, "earnings_disclosures", None)
    if getter is None:  # provider without the resolver (e.g. no OPENDART key path) → dark, not error
        return 0
    ref = build_ref(Market.KR, ticker)
    discs = await getter(ref, limit)
    docs: list[dict] = []
    for d in discs:
        rcp = d.get("rcept_no")
        if not rcp:
            continue
        # fetch + cache the original DART markup (shared with the in-app viewer)
        html = await get_filing_html("KR", rcp)
        if not html:
            continue
        docs += await asyncio.to_thread(
            _html_to_docs, html, "KR", ticker.upper(), rcp, _SOURCE, d.get("url"), "earnings")
    if not docs:
        return 0
    chunks = await _ingest_to_rag(rag_url or settings.rag_url, docs)
    log.info("kr-earnings: %s → %d disclosures, %d chunks indexed", ticker.upper(), len(discs), chunks)
    return chunks


async def run_kr_earnings_ingest(market: str, tickers: list[str]) -> None:
    """Index each KR ticker's recent 잠정실적 공정공시 into RAG, tracked as an IngestionJob
    (kind ``kr_earnings``); best-effort per ticker. US no-ops (use the transcript pipeline)."""
    market = (market or "").upper()
    tickers = tickers or []
    job = start_job("kr_earnings", market, f"kr_earnings · {len(tickers)} tickers", len(tickers))
    if market != "KR":
        await asyncio.to_thread(
            finish_job, job, "success", 0,
            "잠정실적 공정공시는 KR 전용 — US는 어닝콜 트랜스크립트(Alpha Vantage) 파이프라인 사용")
        await asyncio.to_thread(
            log_activity, "kr_earnings", market,
            "건너뜀 · 잠정실적 공정공시는 KR 전용 (US는 어닝콜 트랜스크립트)", job, "warn")
        return
    log_activity("kr_earnings", market,
                 f"▶ 시작 · {len(tickers)}종목 · OpenDART 잠정실적 공정공시 → RAG", job_id=job)
    total = 0
    failed: dict[str, str] = {}
    empty: list[str] = []
    try:
        for i, tk in enumerate(tickers, 1):
            await asyncio.to_thread(
                log_activity, "kr_earnings", market,
                f"[{tk}] 잠정실적 공정공시 수집·인덱싱 중… ({i}/{len(tickers)})", job)
            try:
                got = await ingest_kr_earnings_for_ticker(market, tk)
                total += got
                if got == 0:
                    empty.append(tk)
                    await asyncio.to_thread(log_activity, "kr_earnings", market,
                                            f"[{tk}] 잠정실적 공시 없음 (0 chunks)", job, "warn")
                else:
                    await asyncio.to_thread(log_activity, "kr_earnings", market,
                                            f"[{tk}] OpenDART → RAG {got} chunks 인덱싱 ✓", job)
            except Exception as exc:  # noqa: BLE001 — one ticker never aborts the run
                reason = f"{type(exc).__name__}: {exc}".strip().rstrip(":")
                failed[tk] = reason or type(exc).__name__
                log.warning("kr-earnings: %s failed: %s", tk, reason)
                await asyncio.to_thread(log_activity, "kr_earnings", market,
                                        f"[{tk}] 실패 — {reason}", job, "error")
            await asyncio.to_thread(update_progress, job, i)
        ok = len(tickers) - len(failed) - len(empty)
        note_parts = [f"{ok}/{len(tickers)} tickers indexed, {total} chunks"]
        if failed:
            note_parts.append("FAILED " + "; ".join(
                f"{tk}:{r}" for tk, r in list(failed.items())[:8]))
        if empty:
            note_parts.append(f"no disclosure ×{len(empty)} ({', '.join(empty[:8])}{'…' if len(empty) > 8 else ''})")
        note = " · ".join(note_parts)
        status = "error" if failed and ok == 0 and total == 0 else "success"
        await asyncio.to_thread(finish_job, job, status, total, note)
        await asyncio.to_thread(log_activity, "kr_earnings", market,
                                f"{'✓' if status == 'success' else '✗'} 완료 · {note}", job,
                                "info" if status == "success" else "error")
    except Exception:  # noqa: BLE001 — the loop itself blew up
        await asyncio.to_thread(finish_job, job, "error", total, traceback.format_exc()[-1800:])
