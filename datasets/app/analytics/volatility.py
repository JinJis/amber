"""Volatility context (HL-3) — realized vol over rolling windows, percentile vs own history,
and (for a level index like ^VIX) level percentile. HISTORY_LAB_SPEC §4.3."""

from __future__ import annotations

import math

from app.analytics._common import (
    LABEL, TRADING_DAYS, Bar, clean_bars, distribution, log_returns, percentile_rank,
)

METHOD = "vol-v1"


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def realized_vol(closes: list[float], window: int) -> float | None:
    """Annualized realized vol (%) over the last ``window`` daily log returns:
    ``stdev(r[-window:]) * sqrt(252) * 100``. None if too few returns."""
    r = log_returns(closes)
    if len(r) < window:
        return None
    return _stdev(r[-window:]) * math.sqrt(TRADING_DAYS) * 100.0


def _rv_history(closes: list[float], window: int) -> list[float]:
    """Every historical rolling-window realized-vol observation (for the percentile denominator)."""
    r = log_returns(closes)
    if len(r) < window:
        return []
    return [_stdev(r[i - window:i]) * math.sqrt(TRADING_DAYS) * 100.0 for i in range(window, len(r) + 1)]


def vol_context(bars: list[Bar], windows: tuple[int, ...] = (20, 60, 252), is_level: bool = False) -> dict:
    """Current realized vol per window + its percentile vs the full own-history distribution.

    ``is_level=True`` (e.g. ^VIX) additionally reports the current LEVEL and its full-history
    percentile — VIX is itself a volatility measure, so its level is the headline number."""
    b = clean_bars(bars)
    closes = [c for _, c in b]
    span = {"from": b[0][0].isoformat(), "to": b[-1][0].isoformat()} if b else {}
    out: dict = {"method": METHOD, "label": LABEL, "history_span": span, "windows": {}}

    for w in windows:
        cur = realized_vol(closes, w)
        hist = _rv_history(closes, w)
        out["windows"][str(w)] = {
            "realized_vol_pct": round(cur, 4) if cur is not None else None,
            "percentile": percentile_rank(hist, cur) if (cur is not None and hist) else None,
            "distribution": distribution(hist) if hist else {"n": 0},
        }

    if is_level and closes:
        level = closes[-1]
        out["level"] = {
            "current": round(level, 4),
            "percentile": percentile_rank(closes, level),
            "distribution": distribution(closes),
            "as_of": b[-1][0].isoformat(),
        }
    return out
