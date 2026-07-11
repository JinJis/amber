"""Shared math for the History Lab analytics — pure stdlib (no numpy; matches the datasets store's
dependency-light convention). Closes-based, log returns, hand-rolled Pearson + percentile rank."""

from __future__ import annotations

import math
from datetime import date

# The descriptive-statistics badge every History Lab result carries (ROADMAP §2 invariant).
LABEL = "과거 기록 · 전망 아님"
TRADING_DAYS = 252


Bar = tuple[date, float]  # (bar_date, close)


def clean_bars(bars: list[Bar]) -> list[Bar]:
    """Drop points with a missing/non-finite close and sort by date. Gaps (absent dates) are left
    absent — the chart draws them; we never interpolate (honesty invariant)."""
    out = [(d, float(c)) for d, c in bars if c is not None and math.isfinite(float(c)) and float(c) > 0]
    out.sort(key=lambda x: x[0])
    return out


def log_returns(closes: list[float]) -> list[float]:
    """r_t = ln(C_t / C_{t-1}); length len(closes) - 1."""
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]


def pearson(a: list[float], b: list[float]) -> float | None:
    """Pearson correlation of two equal-length series; None if undefined (too short / zero variance)."""
    n = min(len(a), len(b))
    if n < 3:
        return None
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    return cov / math.sqrt(va * vb)


def zscore(path: list[float]) -> list[float] | None:
    """Z-normalize a path (mean 0, unit stdev). None for a flat/degenerate window (zero variance)."""
    n = len(path)
    if n < 2:
        return None
    m = sum(path) / n
    var = sum((x - m) ** 2 for x in path) / n
    if var <= 0:
        return None
    sd = math.sqrt(var)
    return [(x - m) / sd for x in path]


def percentile_rank(values: list[float], x: float) -> float | None:
    """Inclusive percentile rank of x within values, in PERCENT (0..100): share of observations
    ≤ x. None if values is empty."""
    if not values:
        return None
    le = sum(1 for v in values if v <= x)
    return round(100.0 * le / len(values), 2)


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation quantile (q in 0..1) on an already-sorted list."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def distribution(values: list[float]) -> dict:
    """p5/p25/p50/p75/p95 + min/max/n for a value set — lets the UI draw a strip without a 2nd call."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s), "min": round(s[0], 4), "max": round(s[-1], 4),
        "p5": round(_quantile(s, 0.05), 4), "p25": round(_quantile(s, 0.25), 4),
        "p50": round(_quantile(s, 0.50), 4), "p75": round(_quantile(s, 0.75), 4),
        "p95": round(_quantile(s, 0.95), 4),
    }


def rebase(closes: list[float], base: float = 100.0) -> list[float]:
    """Rebase a close series to `base` at its first point (for overlaying paths of different levels)."""
    if not closes or closes[0] <= 0:
        return []
    return [round(base * c / closes[0], 4) for c in closes]
