"""History Lab store layer (HL-2/HL-4) — bar loading, derived-episode persistence, regime seeding.

Bars are the single source of truth (PriceBar closes); ``DrawdownEpisode`` rows are recomputed
idempotently from them (delete+reinsert per ticker/threshold/method — reproducible, never
hand-edited). ``MarketRegime`` rows are seeded from the curated, sourced reference data in
``app.analytics.regimes_seed``; the seed's date hints are cross-checked against the derived
episodes and mismatches are LOGGED, never silently overwritten (ROADMAP §2.3).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from sqlalchemy import delete, select

from app.analytics.drawdown import METHOD as DD_METHOD, episodes as detect_episodes
from app.analytics.regimes_seed import REGIMES
from app.store.db import SessionLocal
from app.store.models import DrawdownEpisode, MarketRegime, PriceBar

logger = logging.getLogger(__name__)

EPISODE_THRESHOLDS = (10.0, 20.0)


def load_closes(market: str, ticker: str) -> list[tuple[date, float]]:
    """All daily closes for (market, ticker), oldest first. Empty list when un-ingested."""
    with SessionLocal() as db:
        rows = db.execute(
            select(PriceBar.bar_date, PriceBar.close).where(
                PriceBar.market == market.upper(), PriceBar.ticker == ticker.upper(),
                PriceBar.interval == "day", PriceBar.close.isnot(None),
            ).order_by(PriceBar.bar_date)
        ).all()
    return [(bd, float(c)) for bd, c in rows]


def recompute_episodes(market: str, ticker: str,
                       thresholds: tuple[float, ...] = EPISODE_THRESHOLDS,
                       source: str = "derived: prices (close), dd-v1") -> int:
    """Re-derive and persist DrawdownEpisode rows for one anchor (idempotent: replace the
    ticker+threshold+method slice wholesale — episodes are a pure function of the bars).
    Returns how many rows were written."""
    bars = load_closes(market, ticker)
    if len(bars) < 2:
        return 0
    written = 0
    with SessionLocal() as db:
        for thr in thresholds:
            eps = detect_episodes(bars, threshold_pct=thr)
            db.execute(delete(DrawdownEpisode).where(
                DrawdownEpisode.market == market.upper(), DrawdownEpisode.ticker == ticker.upper(),
                DrawdownEpisode.threshold_pct == thr, DrawdownEpisode.method_version == DD_METHOD))
            for e in eps:
                db.add(DrawdownEpisode(
                    market=market.upper(), ticker=ticker.upper(),
                    peak_date=date.fromisoformat(e["peak_date"]), peak_close=e["peak_close"],
                    trough_date=date.fromisoformat(e["trough_date"]), trough_close=e["trough_close"],
                    depth_pct=e["depth_pct"], decline_days=e["decline_days"],
                    recovery_date=date.fromisoformat(e["recovery_date"]) if e["recovery_date"] else None,
                    recovery_days=e["recovery_days"], is_open=e["is_open"],
                    threshold_pct=thr, method_version=DD_METHOD, source=source,
                ))
                written += 1
        db.commit()
    return written


def list_episodes(market: str, ticker: str, threshold_pct: float = 20.0) -> list[dict]:
    """Stored derived episodes, deepest first."""
    with SessionLocal() as db:
        rows = db.execute(
            select(DrawdownEpisode).where(
                DrawdownEpisode.market == market.upper(), DrawdownEpisode.ticker == ticker.upper(),
                DrawdownEpisode.threshold_pct == float(threshold_pct),
            ).order_by(DrawdownEpisode.depth_pct)
        ).scalars().all()
    return [_episode_out(e) for e in rows]


def _episode_out(e: DrawdownEpisode) -> dict:
    return {
        "peak_date": e.peak_date.isoformat(), "peak_close": e.peak_close,
        "trough_date": e.trough_date.isoformat(), "trough_close": e.trough_close,
        "depth_pct": e.depth_pct, "decline_days": e.decline_days,
        "recovery_date": e.recovery_date.isoformat() if e.recovery_date else None,
        "recovery_days": e.recovery_days, "is_open": e.is_open,
        "threshold_pct": e.threshold_pct, "method": e.method_version, "source": e.source,
    }


def seed_regimes() -> int:
    """Upsert the curated regimes (idempotent by slug). The seed's peak/trough HINTS are
    cross-checked against derived episodes where available: a hint that doesn't fall inside any
    derived episode's peak±30d is logged as a warning — the curated row is still written (it is
    reference data with its own sources), but the discrepancy is visible, not silent."""
    n = 0
    with SessionLocal() as db:
        for r in REGIMES:
            peak_hint = date.fromisoformat(r["peak_hint"]) if r.get("peak_hint") else None
            # rate_cycle regimes (테이퍼 탠트럼, 금리 사이클)의 힌트는 금리 이벤트 구간이지 주가
            # 드로다운 피크가 아니다 — 에피소드 크로스체크는 drawdown류(kind != rate_cycle)에만.
            if peak_hint and r.get("kind") != "rate_cycle":
                # A hint checks out when a derived episode PEAKS near it, OR when it falls
                # INSIDE an episode's underwater span — events like 9·11 or the 카드사태(2003)
                # happen inside a larger bear (dot-com / IMF aftermath), so their hint can
                # never be an episode peak; containment is the correct cross-check for those.
                near = db.execute(select(DrawdownEpisode).where(
                    DrawdownEpisode.ticker == r["anchor_ticker"].upper(),
                    DrawdownEpisode.peak_date >= peak_hint - timedelta(days=30),
                    DrawdownEpisode.peak_date <= peak_hint + timedelta(days=30),
                )).scalars().first()
                if near is None:
                    near = db.execute(select(DrawdownEpisode).where(
                        DrawdownEpisode.ticker == r["anchor_ticker"].upper(),
                        DrawdownEpisode.peak_date <= peak_hint,
                        (DrawdownEpisode.recovery_date.is_(None))
                        | (DrawdownEpisode.recovery_date >= peak_hint),
                    )).scalars().first()
                if near is None:
                    logger.warning("regime %s: no derived episode near peak hint %s for %s "
                                   "(bars not ingested yet, or dates need review)",
                                   r["slug"], r["peak_hint"], r["anchor_ticker"])
            db.merge(MarketRegime(
                slug=r["slug"], name_kr=r["name_kr"], name_en=r["name_en"],
                market=r["market"], anchor_ticker=r["anchor_ticker"].upper(), kind=r["kind"],
                start_date=date.fromisoformat(r["start_date"]),
                end_date=date.fromisoformat(r["end_date"]),
                peak_date=peak_hint,
                trough_date=date.fromisoformat(r["trough_hint"]) if r.get("trough_hint") else None,
                description=r["description"], sources=json.dumps(r["sources"], ensure_ascii=False),
            ))
            n += 1
        db.commit()
    return n


def _regime_out(r: MarketRegime) -> dict:
    return {
        "slug": r.slug, "name_kr": r.name_kr, "name_en": r.name_en, "market": r.market,
        "anchor_ticker": r.anchor_ticker, "kind": r.kind,
        "start_date": r.start_date.isoformat(), "end_date": r.end_date.isoformat(),
        "peak_date": r.peak_date.isoformat() if r.peak_date else None,
        "trough_date": r.trough_date.isoformat() if r.trough_date else None,
        "description": r.description, "sources": json.loads(r.sources or "[]"),
    }


def list_regimes(market: str | None = None) -> list[dict]:
    with SessionLocal() as db:
        q = select(MarketRegime).order_by(MarketRegime.start_date)
        if market:
            q = q.where(MarketRegime.market == market.upper())
        rows = db.execute(q).scalars().all()
    return [_regime_out(r) for r in rows]


def get_regime(slug: str) -> dict | None:
    with SessionLocal() as db:
        r = db.get(MarketRegime, slug)
    return _regime_out(r) if r is not None else None
