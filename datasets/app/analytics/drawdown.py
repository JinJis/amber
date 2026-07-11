"""Drawdown analytics (HL-3) — underwater series + episode detection (method ``dd-v1``).

Closes-based for cross-era comparability (pre-1962 indices have no reliable OHLC). See
HISTORY_LAB_SPEC §4.1–§4.2.
"""

from __future__ import annotations

from app.analytics._common import LABEL, Bar, clean_bars

METHOD = "dd-v1"


def underwater(bars: list[Bar]) -> dict:
    """Peak-to-date drawdown series: ``dd_t = C_t / max(C_0..t) - 1`` (≤ 0), as percent.

    Returns ``{series: [(iso_date, dd_pct)], current: {dd_pct, peak_date, peak_close,
    days_since_peak}, method, label}``. Raises ValueError on < 2 usable bars."""
    b = clean_bars(bars)
    if len(b) < 2:
        raise ValueError("need ≥2 price bars for a drawdown series")
    peak_close = b[0][1]
    peak_date = b[0][0]
    series: list[tuple[str, float]] = []
    for d, c in b:
        if c > peak_close:
            peak_close, peak_date = c, d
        dd = (c / peak_close - 1.0) * 100.0
        series.append((d.isoformat(), round(dd, 4)))
    last_date, last_close = b[-1]
    return {
        "method": METHOD, "label": LABEL,
        "series": series,
        "current": {
            "dd_pct": round((last_close / peak_close - 1.0) * 100.0, 4),
            "peak_date": peak_date.isoformat(), "peak_close": round(peak_close, 4),
            "days_since_peak": (last_date - peak_date).days,
            "as_of": last_date.isoformat(),
        },
    }


def episodes(bars: list[Bar], threshold_pct: float = 20.0) -> list[dict]:
    """Detect drawdown EPISODES with method ``dd-v1`` (HISTORY_LAB_SPEC §4.2).

    An episode opens when the close-based drawdown from the running peak first crosses
    ``-threshold_pct``, tracks the trough, and closes when the close first regains the peak
    (full recovery). An episode still underwater at the end is emitted with ``is_open=True``.
    ``threshold_pct`` is a positive magnitude (e.g. 20.0 for a bear market).
    """
    b = clean_bars(bars)
    thr = abs(threshold_pct)
    out: list[dict] = []
    if len(b) < 2:
        return out

    peak_date, peak_close = b[0]
    in_ep = False
    trough_close = peak_close
    trough_date = peak_date

    def emit(recovery_date, recovery_close, is_open):
        out.append({
            "peak_date": peak_date.isoformat(), "peak_close": round(peak_close, 4),
            "trough_date": trough_date.isoformat(), "trough_close": round(trough_close, 4),
            "depth_pct": round((trough_close / peak_close - 1.0) * 100.0, 4),
            "decline_days": (trough_date - peak_date).days,
            "recovery_date": recovery_date.isoformat() if recovery_date else None,
            "recovery_days": (recovery_date - trough_date).days if recovery_date else None,
            "is_open": is_open, "threshold_pct": thr, "method": METHOD,
        })

    for d, c in b:
        if not in_ep:
            if c > peak_close:
                peak_close, peak_date = c, d
            if round((c / peak_close - 1.0) * 100.0, 6) <= -thr:  # episode opens (round: exact-boundary safe)
                in_ep = True
                trough_close, trough_date = c, d
        else:
            if c < trough_close:
                trough_close, trough_date = c, d
            if c >= peak_close:  # full recovery closes the episode
                emit(d, c, is_open=False)
                in_ep = False
                peak_close, peak_date = c, d
    if in_ep:
        emit(None, None, is_open=True)
    return out


def episodes_result(bars: list[Bar], threshold_pct: float = 20.0) -> dict:
    """Episodes sorted deepest-first, wrapped with method+label for the API envelope."""
    eps = episodes(bars, threshold_pct)
    eps.sort(key=lambda e: e["depth_pct"])  # most negative (deepest) first
    return {"method": METHOD, "label": LABEL, "threshold_pct": abs(threshold_pct),
            "n": len(eps), "episodes": eps}
