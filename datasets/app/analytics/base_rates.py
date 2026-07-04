"""Base rates (HL-3) — for a user-defined PAST event, the DESCRIPTIVE record of what followed
over fixed horizons. HISTORY_LAB_SPEC §4.4.

This is the guardrail-critical module: every result is an aggregate over an EXPLICIT, enumerable
set of past events (the dates are always returned), computed transparently, and labelled
"과거 기록 · 전망 아님". Field is ``pos_share`` ("상승 마감 비율(과거)") — NEVER "probability of rising".
"""

from __future__ import annotations

from datetime import date

from app.analytics._common import LABEL, Bar, clean_bars, distribution
from app.analytics.drawdown import episodes as _episodes

METHOD = "base-rates-v1"
DEFAULT_HORIZONS = (1, 5, 20, 60, 120)


def _event_dates(b: list[Bar], event: dict, min_gap_days: int) -> tuple[list[int], list[str], int]:
    """Resolve the event spec to the bar INDICES where it fires (+ their iso dates), applying the
    clustering guard for threshold-crossing conditions. Returns (indices, iso_dates, raw_count)."""
    closes = [c for _, c in b]
    dates = [d for d, _ in b]
    raw: list[int] = []

    if "daily_return_lte" in event:
        thr = float(event["daily_return_lte"])  # e.g. -5.0 (%)
        for i in range(1, len(closes)):
            # round to 6 dp so an exact boundary (e.g. 90/100 = −10.0%) isn't missed by float noise
            if round((closes[i] / closes[i - 1] - 1.0) * 100.0, 6) <= thr:
                raw.append(i)
    elif "drawdown_gte" in event:
        # one event per episode: the first day the episode crosses -y% (episode open day).
        y = abs(float(event["drawdown_gte"]))
        for ep in _episodes(b, threshold_pct=y):
            td = date.fromisoformat(ep["peak_date"])
            # the crossing day is the first bar at/after peak breaching -y%; find it
            peak_close = ep["peak_close"]
            for i, (d, c) in enumerate(b):
                if d >= td and round((c / peak_close - 1.0) * 100.0, 6) <= -y:
                    raw.append(i)
                    break
    else:
        raise ValueError(f"unsupported event spec: {sorted(event)}")

    raw_count = len(raw)
    # clustering guard: keep the FIRST of each cluster within min_gap_days.
    kept: list[int] = []
    last_date: date | None = None
    for i in raw:
        di = dates[i]
        if last_date is None or (di - last_date).days >= min_gap_days:
            kept.append(i)
            last_date = di
    return kept, [dates[i].isoformat() for i in kept], raw_count


def base_rates(bars: list[Bar], event: dict, horizons: tuple[int, ...] = DEFAULT_HORIZONS,
               min_gap_days: int = 10) -> dict:
    """For each event date t and horizon h, forward return ``F_h = C_{t+h}/C_t - 1`` (%), aggregated
    DESCRIPTIVELY: per-horizon {n, median, p25, p75, min, max, pos_share}. Always returns the event
    dates (enumerability is the trust feature) and a histogram for the default horizon."""
    b = clean_bars(bars)
    closes = [c for _, c in b]
    idx, iso_dates, raw_count = _event_dates(b, event, min_gap_days)

    rows = []
    per_h_returns: dict[int, list[float]] = {}
    for h in horizons:
        fwd = [(closes[i + h] / closes[i] - 1.0) * 100.0 for i in idx if i + h < len(closes)]
        per_h_returns[h] = fwd
        if fwd:
            s = sorted(fwd)
            d = distribution(fwd)
            pos = sum(1 for x in fwd if x > 0)
            rows.append({
                "h": h, "n": len(fwd), "median": d["p50"], "p25": d["p25"], "p75": d["p75"],
                "min": d["min"], "max": d["max"],
                "pos_share": round(100.0 * pos / len(fwd), 2),  # 상승 마감 비율(과거) — NOT a probability
            })
        else:
            rows.append({"h": h, "n": 0, "median": None, "p25": None, "p75": None,
                         "min": None, "max": None, "pos_share": None})

    # histogram for the default reference horizon (first horizon with data)
    h_ref = next((h for h in horizons if per_h_returns.get(h)), horizons[0])
    hist = _histogram(per_h_returns.get(h_ref, []))

    return {
        "method": METHOD, "label": LABEL,
        "event": event, "min_gap_days": min_gap_days,
        "n": len(idx), "raw_n": raw_count,  # clustered vs raw
        "event_dates": iso_dates,
        "horizons": rows,
        "histogram": {"h_ref": h_ref, "bins": hist},
    }


def _histogram(values: list[float], nbins: int = 11) -> list[dict]:
    """Fixed-count histogram over the value range (empty list if no values)."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [{"lo": round(lo, 4), "hi": round(hi, 4), "count": len(values)}]
    width = (hi - lo) / nbins
    bins = [{"lo": round(lo + k * width, 4), "hi": round(lo + (k + 1) * width, 4), "count": 0}
            for k in range(nbins)]
    for x in values:
        k = min(int((x - lo) / width), nbins - 1)
        bins[k]["count"] += 1
    return bins
