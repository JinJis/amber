"""PH-DATA-6: descriptive technical indicators computed from real OHLCV.

Provenance workflow for this derived view:
  · FETCH   — the prices provider's real OHLCV (Yahoo, US+KR) through the registry.
  · PROCESS — pure math (SMA/EMA/RSI/MACD/Bollinger/volatility) over the close series.
  · STORE   — none (derived on demand from prices).
  · SHOW    — chart-ready lines (feeds PH-VIZ) + a data card; source = "computed from
              Yahoo Finance" + the price `as_of`.

These are **descriptive** overlays, never a trading signal/recommendation (the agent
guardrail still refuses advice). Gaps are dropped, never fabricated.
"""

from __future__ import annotations

import math
from datetime import date

from app.derivation import TECH_FORMULA, calc_row, computation
from app.providers.registry import get_prices_provider
from app.symbols import Market, build_ref

SOURCE = "Technical indicators (computed from Yahoo Finance)"
_NOTE = "Descriptive technical overlays computed from historical prices — not a trading signal or recommendation."

# default set when the caller doesn't name indicators
_DEFAULTS = ["sma_20", "sma_50", "rsi_14", "macd", "bbands_20"]
_MAX_WINDOW = 400


# --- pure series math (operate on a list of close floats; return aligned list) ---
def _sma(c: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(c)
    s = 0.0
    for i, v in enumerate(c):
        s += v
        if i >= n:
            s -= c[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema(c: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(c)
    if len(c) < n:
        return out
    k = 2 / (n + 1)
    prev = sum(c[:n]) / n  # seed with the SMA of the first window
    out[n - 1] = prev
    for i in range(n, len(c)):
        prev = c[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _rsi(c: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(c)
    if len(c) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def _macd(c: list[float]):
    fast, slow = _ema(c, 12), _ema(c, 26)
    macd = [(f - s) if (f is not None and s is not None) else None for f, s in zip(fast, slow)]
    vals = [m for m in macd if m is not None]
    sig_seed_idx = next((i for i, m in enumerate(macd) if m is not None), None)
    signal: list[float | None] = [None] * len(c)
    if sig_seed_idx is not None and len(vals) >= 9:
        sig_part = _ema(vals, 9)
        # map the signal EMA (computed on the compacted macd values) back onto the timeline
        j = 0
        for i in range(sig_seed_idx, len(c)):
            if macd[i] is not None:
                signal[i] = sig_part[j]
                j += 1
    hist = [(m - s) if (m is not None and s is not None) else None for m, s in zip(macd, signal)]
    return macd, signal, hist


def _bbands(c: list[float], n: int, k: float = 2.0):
    mid = _sma(c, n)
    upper: list[float | None] = [None] * len(c)
    lower: list[float | None] = [None] * len(c)
    for i in range(len(c)):
        if mid[i] is None:
            continue
        window = c[i - n + 1: i + 1]
        m = mid[i]
        std = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        upper[i], lower[i] = m + k * std, m - k * std
    return upper, mid, lower


def _volatility(c: list[float], n: int) -> list[float | None]:
    """Annualized realized volatility (%) from rolling std of daily log returns."""
    rets = [math.log(c[i] / c[i - 1]) if c[i - 1] > 0 else 0.0 for i in range(1, len(c))]
    out: list[float | None] = [None] * len(c)
    for i in range(n, len(c)):
        w = rets[i - n: i]
        mean = sum(w) / n
        std = math.sqrt(sum((r - mean) ** 2 for r in w) / n)
        out[i] = std * math.sqrt(252) * 100
    return out


def _line(label: str, dates: list[str], vals: list[float | None]) -> dict:
    pts = [{"date": d, "value": round(v, 4)} for d, v in zip(dates, vals) if v is not None]
    return {"label": label, "points": pts, "latest": (pts[-1]["value"] if pts else None)}


def _parse(token: str) -> tuple[str, int] | None:
    token = (token or "").strip().lower()
    if not token:
        return None
    kind, _, w = token.partition("_")
    defaults = {"sma": 20, "ema": 20, "rsi": 14, "bbands": 20, "volatility": 20, "macd": 0}
    if kind not in defaults:
        return None
    try:
        window = int(w) if w else defaults[kind]
    except ValueError:
        return None
    if kind != "macd" and not (2 <= window <= _MAX_WINDOW):
        return None
    return kind, window


def _build(kind: str, n: int, dates: list[str], closes: list[float]) -> dict | None:
    if kind == "sma":
        return {"key": f"sma_{n}", "name": f"SMA({n})", "pane": "price", "unit": "price",
                "lines": [_line(f"SMA({n})", dates, _sma(closes, n))]}
    if kind == "ema":
        return {"key": f"ema_{n}", "name": f"EMA({n})", "pane": "price", "unit": "price",
                "lines": [_line(f"EMA({n})", dates, _ema(closes, n))]}
    if kind == "rsi":
        return {"key": f"rsi_{n}", "name": f"RSI({n})", "pane": "sub", "unit": "ratio_0_100",
                "lines": [_line(f"RSI({n})", dates, _rsi(closes, n))]}
    if kind == "macd":
        macd, signal, hist = _macd(closes)
        return {"key": "macd", "name": "MACD(12,26,9)", "pane": "sub", "unit": "price",
                "lines": [_line("MACD", dates, macd), _line("Signal", dates, signal), _line("Histogram", dates, hist)]}
    if kind == "bbands":
        up, mid, lo = _bbands(closes, n)
        return {"key": f"bbands_{n}", "name": f"Bollinger({n},2σ)", "pane": "price", "unit": "price",
                "lines": [_line("Upper", dates, up), _line("Middle", dates, mid), _line("Lower", dates, lo)]}
    if kind == "volatility":
        return {"key": f"volatility_{n}", "name": f"Realized vol({n}d, ann.)", "pane": "sub", "unit": "percent",
                "lines": [_line(f"Vol({n}d)", dates, _volatility(closes, n))]}
    return None


def _tech_derivation(dates: list[str], closes: list[float], parsed: list[tuple]) -> dict:
    """M-DERIV (DRV-1): the definition of every requested indicator + its window — the
    derivation of a technical figure is its formula over the (sourced) close series."""
    kinds = [(k, n) for k, n in parsed if k]
    formulas = []
    seen = set()
    for k, _n in kinds:
        f = TECH_FORMULA.get(k)
        if f and k not in seen:
            seen.add(k)
            formulas.append(f)
    inputs = []
    if dates:
        inputs.append(calc_row("종가 시계열", f"{len(closes)} bars ({dates[0]} ~ {dates[-1]})", source=SOURCE))
    assumptions = [calc_row(f"{k.upper()} 윈도우", f"{n}일", symbol="n")
                   for k, n in kinds if k != "macd" and n]
    return computation("기술적 지표 (종가 파생 · 신호 아님)", " · ".join(formulas) or None,
                       inputs=inputs, assumptions=assumptions, note=_NOTE)


async def technical_indicators(market: str, ticker: str, indicators: str | None,
                               interval: str, start: date, end: date) -> dict:
    mk = Market(market)
    ref = build_ref(mk, ticker)
    prices = await get_prices_provider(mk).prices(ref, interval, start, end)
    rows = [p for p in prices if getattr(p, "close", None) is not None and getattr(p, "time", None)]
    rows.sort(key=lambda p: p.time)
    dates = [str(p.time)[:10] for p in rows]
    closes = [float(p.close) for p in rows]

    tokens = [t for t in (indicators.split(",") if indicators else _DEFAULTS)]
    seen, out = set(), []
    for tok in tokens:
        parsed = _parse(tok)
        if not parsed:
            continue
        kind, n = parsed
        key = f"{kind}_{n}" if kind != "macd" else "macd"
        if key in seen:
            continue
        seen.add(key)
        if not closes:
            continue
        ind = _build(kind, n, dates, closes)
        if ind:
            out.append(ind)
    return {"ticker": ref.ticker, "market": mk.value, "interval": interval, "source": SOURCE,
            "as_of": (dates[-1] if dates else None), "indicators": out, "note": _NOTE,
            # M-DERIV (DRV-1): how each indicator is derived from the close series
            "computation": _tech_derivation(dates, closes, [(_parse(t) or (None, 0)) for t in tokens])}
