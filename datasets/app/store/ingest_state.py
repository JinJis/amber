"""Delta-ingest cursors + upstream quota accounting (OPS-2).

Two small, shared bookkeeping stores that make a full-universe re-run cheap and quota-visible:

* **IngestState** — per (kind, market, ticker) the item ids (filing accessions, transcript
  quarters, deck accessions) already ingested into RAG. A ``delta`` run skips items present
  here; both delta AND full runs record what they ingested so the baseline is always current.
* **UpstreamUsage** — per (provider, key, KST-day) call counter, incremented at every real
  OpenDART call (worker + datasets share the DB), so the admin shows each key's remaining
  daily quota instead of discovering exhaustion via 020 errors.

Everything here is best-effort bookkeeping: a failure must never sink an ingest run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.store.db import SessionLocal
from app.store.models import IngestState, UpstreamUsage

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))


def kst_today() -> str:
    """The OpenDART quota window is the KST calendar day."""
    return datetime.now(_KST).strftime("%Y-%m-%d")


# --- delta cursors -------------------------------------------------------------------


def done_items(kind: str, market: str, ticker: str) -> set[str]:
    """Item ids already ingested for this (pipeline, market, ticker) — empty set on any miss."""
    try:
        with SessionLocal() as db:
            row = db.get(IngestState, (kind[:16], market[:2].upper(), ticker[:20].upper()))
            if row is None:
                return set()
            val = json.loads(row.items or "[]")
            return {str(x) for x in val} if isinstance(val, list) else set()
    except Exception:  # noqa: BLE001 — bookkeeping never breaks a run
        return set()


def mark_items(kind: str, market: str, ticker: str, items: set[str]) -> None:
    """Merge ``items`` into the ticker's ingested set (idempotent; bounded to the last 200 ids)."""
    if not items:
        return
    try:
        with SessionLocal() as db:
            key = (kind[:16], market[:2].upper(), ticker[:20].upper())
            row = db.get(IngestState, key)
            if row is None:
                row = IngestState(kind=key[0], market=key[1], ticker=key[2], items="[]")
                db.add(row)
            merged = done_items_from_json(row.items) | {str(x) for x in items}
            # keep the cursor bounded — old items beyond any refetch window are irrelevant
            row.items = json.dumps(sorted(merged)[-200:], ensure_ascii=False)
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.info("ingest_state mark skipped %s/%s/%s: %s", kind, market, ticker, exc)


def done_items_from_json(raw: str | None) -> set[str]:
    try:
        val = json.loads(raw or "[]")
        return {str(x) for x in val} if isinstance(val, list) else set()
    except ValueError:
        return set()


# --- financials delta (store-freshness based) -----------------------------------------


def fresh_financials_tickers(market: str, tickers: list[str], fresh_days: int = 80) -> set[str]:
    """Tickers whose stored statements are still FRESH (latest report_period younger than
    ``fresh_days``) — a delta financials run skips these. 80 days ≈ within the current quarter:
    once the latest stored period is older, a new quarterly filing likely exists → refetch."""
    from sqlalchemy import func

    from app.store.models import FinancialFact

    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=fresh_days)).date()
        with SessionLocal() as db:
            rows = db.execute(
                select(FinancialFact.ticker, func.max(FinancialFact.report_period))
                .where(FinancialFact.market == market.upper(),
                       FinancialFact.ticker.in_([t.upper() for t in tickers]))
                .group_by(FinancialFact.ticker)
            ).all()
        return {t for t, latest in rows if latest and latest >= cutoff}
    except Exception as exc:  # noqa: BLE001 — on any miss, delta degrades to full (never skips wrongly)
        logger.info("fresh_financials_tickers skipped: %s", exc)
        return set()


# --- upstream quota accounting ---------------------------------------------------------


def key_label(key: str | None) -> str:
    """A safe display id for a key — its last 4 chars ('…abcd'); never the key itself."""
    k = (key or "").strip()
    return f"…{k[-4:]}" if len(k) >= 4 else "(unset)"


def record_upstream_call(provider: str, key: str | None, n: int = 1) -> None:
    """Increment today's (KST) call counter for (provider, key). Best-effort."""
    try:
        with SessionLocal() as db:
            pk = (provider[:16], key_label(key), kst_today())
            row = db.get(UpstreamUsage, pk)
            if row is None:
                row = UpstreamUsage(provider=pk[0], key_label=pk[1], day=pk[2], calls=0)
                db.add(row)
            row.calls = (row.calls or 0) + n
            row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.info("upstream usage record skipped (%s): %s", provider, exc)


def usage_today(provider: str) -> dict[str, int]:
    """{key_label: calls} for today's KST window."""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(UpstreamUsage.key_label, UpstreamUsage.calls)
                .where(UpstreamUsage.provider == provider[:16], UpstreamUsage.day == kst_today())
            ).all()
        return {label: calls or 0 for label, calls in rows}
    except Exception:  # noqa: BLE001
        return {}


def usage_history(provider: str, days: int = 14) -> list[dict]:
    """Recent daily totals per key (newest first) — the admin's quota trend view."""
    try:
        with SessionLocal() as db:
            rows = db.execute(
                select(UpstreamUsage.day, UpstreamUsage.key_label, UpstreamUsage.calls)
                .where(UpstreamUsage.provider == provider[:16])
                .order_by(UpstreamUsage.day.desc())
                .limit(days * 8)
            ).all()
        return [{"day": d, "key": k, "calls": c or 0} for d, k, c in rows]
    except Exception:  # noqa: BLE001
        return []
