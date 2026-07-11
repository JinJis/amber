"""HL-5b — era-news RAG ingest: index each curated regime's window of period news so the
agent can quote "그 시절의 뉴스" (rag__search doc_type=era_news).

Per regime (or one slug), pick the source by coverage: GDELT DOC 2.0 (2017+, keyless) for
recent regimes, NYT Archive (1851+, key-gated) for the pre-2017 ones (dot-com, GFC, IMF).
A window whose source is unavailable (pre-2017 without NYT_API_KEY) draws a gap — 0 chunks,
never fabricated. Docs are tagged ``ticker={slug}`` so search can scope to one regime and
``doc_type="era_news"`` with the article's publish date as ``as_of``.
"""

from __future__ import annotations

import logging
from datetime import date

from app.config import settings
from app.providers.us.gdelt import _GDELT_START, GdeltProvider
from app.providers.us.nyt_archive import NytArchiveProvider
from app.store.history import get_regime, list_regimes
from app.store.jobs import finish_job, log_activity, start_job
from app.store.news_ingest import _ingest_to_rag

log = logging.getLogger(__name__)

# a compact search query per regime keeps GDELT/NYT focused on the crisis, not the whole month.
_QUERY = {
    "dotcom-bust": "dot-com Nasdaq crash",
    "gfc-2008": "financial crisis Lehman bank",
    "covid-crash-2020": "coronavirus market crash",
    "svb-2023": "Silicon Valley Bank collapse",
    "black-monday-1987": "stock market crash Black Monday",
    "imf-1997": "Korea IMF financial crisis won",
}


def _query_for(reg: dict) -> str:
    return _QUERY.get(reg["slug"]) or reg.get("name_en") or reg.get("name_kr") or "financial crisis"


def _to_docs(reg: dict, articles: list[dict], source: str) -> list[dict]:
    docs: list[dict] = []
    for a in articles:
        title = (a.get("title") or "").strip()
        if not title:
            continue
        body = title + (f" — {a['abstract']}" if a.get("abstract") else "")
        url = a.get("url")
        docs.append({
            "text": body,
            "doc_id": f"era:{reg['slug']}:{url or title}"[:180],
            "source": source, "doc_type": "era_news",
            "ticker": reg["slug"],           # scope search to one regime
            "market": reg.get("market", "US"),
            "as_of": a.get("date"), "url": url,
        })
    return docs


async def _articles_for(reg: dict) -> tuple[list[dict], str]:
    """Fetch the window's articles from the coverage-appropriate source. ([], note) on a gap."""
    start = date.fromisoformat(str(reg["start_date"]))
    end = date.fromisoformat(str(reg["end_date"]))
    query = _query_for(reg)
    if end >= _GDELT_START:
        out = await GdeltProvider().news_search(query, start, end, limit=60)
        return out.get("articles") or [], "GDELT DOC 2.0"
    # pre-2017 → NYT Archive (key-gated; graceful when absent)
    if settings.nyt_api_key:
        try:
            out = await NytArchiveProvider().era_news(query, start, end, limit=60)
            return out.get("articles") or [], "The New York Times Archive"
        except Exception as exc:  # noqa: BLE001 — a 501/upstream error is a gap, not a crash
            log.info("era-news NYT unavailable for %s: %s", reg["slug"], exc)
    return [], "gap: NYT_API_KEY 필요 (2017년 이전 구간)"


async def ingest_era_news_for_regime(slug: str, rag_url: str | None = None) -> int:
    reg = get_regime(slug)
    if not reg:
        return 0
    articles, source = await _articles_for(reg)
    docs = _to_docs(reg, articles, source)
    if not docs:
        return 0
    return await _ingest_to_rag((rag_url or settings.rag_url), docs)


async def run_era_news_ingest(market: str, tickers: list[str] | None = None,
                              rag_url: str | None = None) -> None:
    """Pipeline runner: index era news for the curated regimes (all, or the slugs in ``tickers``).
    Self-records an IngestionJob; best-effort per regime (one gap never sinks the run)."""
    regimes = list_regimes()
    if tickers:
        want = {t.lower() for t in tickers}
        regimes = [r for r in regimes if r["slug"].lower() in want]
    if not regimes:
        return
    job = start_job("era_news", market or None, ",".join(r["slug"] for r in regimes)[:256], total=len(regimes))
    total_chunks = 0
    try:
        for r in regimes:
            try:
                total_chunks += await ingest_era_news_for_regime(r["slug"], rag_url)
            except Exception as exc:  # noqa: BLE001 — per-regime isolation
                log.warning("era-news ingest failed for %s: %s", r["slug"], exc)
        finish_job(job, "done", rows=total_chunks)
        log_activity("era_news", market or None, f"era-news 색인 {total_chunks} chunks · {len(regimes)} 국면")
    except Exception:  # noqa: BLE001
        import traceback
        finish_job(job, "error", total_chunks, traceback.format_exc()[-1800:])
