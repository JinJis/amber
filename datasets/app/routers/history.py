"""History Lab REST (HL-4) — /history/*: descriptive market-history analytics over the store.

Thin wrappers: load bars from PriceBar → call the pure analytics (app.analytics) → wrap in the
common envelope {source, method, params, as_of, freshness, cadence, label, history_span, data}.
Every response carries the descriptive-statistics label "과거 기록 · 전망 아님" (ROADMAP §2 —
these are aggregates of the historical record, never forecasts). Errors are honest: no bars →
404; not enough history for the asked window → 422 with required/available (the UI draws the
gap); unsupported event kinds → 422 listing what exists.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

from fastapi import APIRouter, HTTPException

from app.analytics._common import LABEL
from app.analytics.analogue import analogues as calc_analogues
from app.analytics.base_rates import DEFAULT_HORIZONS, base_rates as calc_base_rates
from app.analytics.drawdown import episodes_result, underwater
from app.analytics.volatility import vol_context as calc_vol_context
from app.deps import ApiKeyDep, MarketParam
from app.derivation import HISTORY_FORMULA, calc_row, computation
from app.store import history as H
from app.symbols import Market

router = APIRouter(prefix="/history", tags=["History Lab"])

_SOURCE = "derived: ingested prices (close) via market_history"


def _freshness(last_bar: date | None) -> str:
    """Daily-series freshness: bars are EOD, so ≤7d (a trading week) = fresh, ≤30d aging, else stale."""
    if last_bar is None:
        return "gap"
    age = (date.today() - last_bar).days
    return "fresh" if age <= 7 else ("aging" if age <= 30 else "stale")


# Index-name → Yahoo-global symbol normalization (data routing, like symbols.normalize_ticker —
# NOT answer logic). The planner naturally says "S&P500"/"코스피"; the bars live under the anchor
# symbols in the US namespace (HL-1).
_INDEX_ALIASES = {
    "S&P500": "^GSPC", "S&P 500": "^GSPC", "SNP500": "^GSPC", "SPX": "^GSPC", "GSPC": "^GSPC",
    "NASDAQ": "^IXIC", "나스닥": "^IXIC", "IXIC": "^IXIC",
    "DOW": "^DJI", "다우": "^DJI", "DJI": "^DJI",
    "KOSPI": "^KS11", "코스피": "^KS11", "KS11": "^KS11",
    "KOSDAQ": "^KQ11", "코스닥": "^KQ11", "KQ11": "^KQ11",
    "VIX": "^VIX", "NIKKEI": "^N225", "닛케이": "^N225", "항셍": "^HSI", "HSI": "^HSI",
}


def _resolve_ticker(ticker: str) -> str:
    t = (ticker or "").strip()
    return _INDEX_ALIASES.get(t.upper(), _INDEX_ALIASES.get(t, t))


def _bars_or_404(market: str, ticker: str) -> list[tuple[date, float]]:
    t = _resolve_ticker(ticker)
    bars = H.load_closes(market, t)
    if not bars and market != "US":
        bars = H.load_closes("US", t)  # anchors (^KS11 …) live in the US namespace (HL-1)
    if not bars:
        raise HTTPException(404, f"No ingested price bars for {market}:{t} — "
                                 "run the prices pipeline first (gaps are drawn, never fabricated).")
    return bars


def _derivation(bars: list, method: str, params: dict, data: dict) -> dict | None:
    """M-DERIV (DRV-1): the derivation of this statistic, embedded where it was computed.
    Inputs = the close series (sourced, enumerated); assumptions = the caller's params;
    steps = the aggregate sizes that came out (n, matches). Unknown method → omit."""
    entry = HISTORY_FORMULA.get(method)
    if not entry:
        return None
    m, formula = entry
    inputs = [calc_row("종가 시계열", f"{len(bars)} bars ({bars[0][0].isoformat()} ~ {bars[-1][0].isoformat()})",
                       source=_SOURCE)]
    assumptions = [calc_row(k, v) for k, v in (params or {}).items() if v is not None and k != "ticker"]
    steps = []
    if isinstance(data.get("n"), int):
        steps.append(calc_row("표본 수 n", data["n"]))
    if isinstance(data.get("n_raw"), int):
        steps.append(calc_row("군집화 전 원표본", data["n_raw"]))
    if isinstance(data.get("matches"), list):
        steps.append(calc_row("유사 구간 수", len(data["matches"])))
    if isinstance(data.get("episodes"), list):
        steps.append(calc_row("에피소드 수", len(data["episodes"])))
    return computation(m, formula, inputs=inputs, assumptions=assumptions, steps=steps, note=LABEL)


def _envelope(bars: list[tuple[date, float]], method: str, params: dict, data: dict) -> dict:
    out = {
        "source": _SOURCE, "method": method, "params": params,
        "as_of": bars[-1][0].isoformat(), "freshness": _freshness(bars[-1][0]),
        "cadence": "daily", "label": LABEL,
        "history_span": {"from": bars[0][0].isoformat(), "to": bars[-1][0].isoformat()},
        "data": data,
    }
    deriv = _derivation(bars, method, params, data)
    if deriv:
        out["computation"] = deriv
    return out


@router.get("/drawdowns", dependencies=[ApiKeyDep],
            summary="낙폭(underwater) 시리즈 — 고점 대비 하락률 추이 + 현재 낙폭 (과거 기록)")
async def drawdowns(ticker: str, market: MarketParam = Market.US) -> dict:
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    try:
        uw = underwater(bars)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _envelope(bars, uw["method"], {"ticker": ticker.upper()},
                     {"series": uw["series"], "current": uw["current"]})


@router.get("/episodes", dependencies=[ApiKeyDep],
            summary="낙폭 에피소드 — 고점→저점→회복 구간 목록 (dd-v1, 과거 기록)")
async def episodes(ticker: str, threshold: float = 20.0, market: MarketParam = Market.US) -> dict:
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    # stored rows when the sweep has run; else derive live from bars (same algorithm, same result)
    stored = await asyncio.to_thread(H.list_episodes, market.value, ticker, threshold)
    if stored:
        data = {"threshold_pct": abs(threshold), "n": len(stored), "episodes": stored, "from_store": True}
        method = stored[0]["method"]
    else:
        r = episodes_result(bars, threshold_pct=threshold)
        data = {"threshold_pct": r["threshold_pct"], "n": r["n"], "episodes": r["episodes"],
                "from_store": False}
        method = r["method"]
    return _envelope(bars, method, {"ticker": ticker.upper(), "threshold": abs(threshold)}, data)


@router.get("/vol-context", dependencies=[ApiKeyDep],
            summary="변동성 컨텍스트 — 실현변동성·자체 히스토리 퍼센타일 (과거 기록)")
async def vol_context(ticker: str, market: MarketParam = Market.US, is_level: bool = False) -> dict:
    """``is_level=true`` for a volatility LEVEL index (^VIX): adds the level's own percentile."""
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    ctx = calc_vol_context(bars, is_level=is_level)
    return _envelope(bars, ctx["method"], {"ticker": ticker.upper(), "is_level": is_level},
                     {k: v for k, v in ctx.items() if k not in ("method", "label")})


@router.get("/base-rates", dependencies=[ApiKeyDep],
            summary="베이스레이트 — 과거 사건 뒤 구간별 수익률의 기술통계 (전망 아님)")
async def base_rates(ticker: str, event: str, market: MarketParam = Market.US,
                     horizons: str | None = None, min_gap_days: int = 10) -> dict:
    """``event``: JSON — {"daily_return_lte": -5.0} or {"drawdown_gte": 20.0}.
    ``horizons``: comma ints (trading days), default 1,5,20,60,120. Always returns the event
    dates (enumerability is the trust feature) and 상승 마감 비율(과거) — never a probability."""
    try:
        ev = json.loads(event)
        assert isinstance(ev, dict) and ev
    except (ValueError, AssertionError) as exc:
        raise HTTPException(422, 'event must be a JSON object, e.g. {"daily_return_lte": -5.0}') from exc
    hz = DEFAULT_HORIZONS
    if horizons:
        try:
            hz = tuple(int(x) for x in horizons.split(",") if x.strip())
            assert hz and all(h > 0 for h in hz)
        except (ValueError, AssertionError) as exc:
            raise HTTPException(422, "horizons must be positive comma-separated integers") from exc
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    try:
        r = calc_base_rates(bars, ev, horizons=hz, min_gap_days=min_gap_days)
    except ValueError as exc:  # unsupported event spec — say what IS supported
        raise HTTPException(422, f"{exc}; supported: daily_return_lte, drawdown_gte") from exc
    return _envelope(bars, r["method"],
                     {"ticker": ticker.upper(), "event": ev, "horizons": list(hz),
                      "min_gap_days": min_gap_days},
                     {k: v for k, v in r.items() if k not in ("method", "label")})


@router.get("/analogues", dependencies=[ApiKeyDep],
            summary="유사 국면 검색 — 최근 구간과 가장 닮은 과거 구간 top-k (과거 기록)")
async def analogue_search(ticker: str, market: MarketParam = Market.US,
                          window: int = 120, k: int = 5) -> dict:
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    r = calc_analogues(bars, {ticker.upper(): bars}, window=window, k=k)
    if r.get("error") == "insufficient_query_history":
        raise HTTPException(422, f"need {r['required']} bars, have {r['available']} — "
                                 "deep-backfill this ticker or use a smaller window")
    return _envelope(bars, r["method"], {"ticker": ticker.upper(), "window": window, "k": k},
                     {kk: vv for kk, vv in r.items() if kk not in ("method", "label")})


@router.get("/regimes", dependencies=[ApiKeyDep],
            summary="국면(레짐) 목록 — 큐레이션된 역사적 국면 + 파생 에피소드 조인 (출처 포함)")
async def regimes(market: str | None = None) -> dict:
    rows = await asyncio.to_thread(H.list_regimes, market)
    if not rows:  # not seeded yet — seed on demand (idempotent, curated reference data)
        await asyncio.to_thread(H.seed_regimes)
        rows = await asyncio.to_thread(H.list_regimes, market)
    return {"source": "curated reference data (sourced per regime) + derived episodes",
            "method": "regimes-v1", "params": {"market": market}, "cadence": "event",
            "label": LABEL, "n": len(rows), "data": {"regimes": rows}}


@router.get("/regime-compare", dependencies=[ApiKeyDep],
            summary="그때 vs 지금 — 현재 경로와 과거 국면 경로를 고점 기준으로 정렬 (과거 기록)")
async def regime_compare(ticker: str, slug: str, market: MarketParam = Market.US,
                         window: int = 252) -> dict:
    regime = await asyncio.to_thread(H.get_regime, slug)
    if regime is None:
        raise HTTPException(404, f"unknown regime '{slug}' — see /history/regimes")
    ticker = _resolve_ticker(ticker)  # "S&P500"/"코스피" → anchor symbol (envelope shows the resolved one)
    bars = await asyncio.to_thread(_bars_or_404, market.value, ticker)
    # anchors live in the US namespace (HL-1: Yahoo-global symbols incl. ^KS11) — try the regime's
    # own market first for flexibility, then fall back to US.
    then_bars = await asyncio.to_thread(H.load_closes, regime["market"], regime["anchor_ticker"])
    if not then_bars and regime["market"] != "US":
        then_bars = await asyncio.to_thread(H.load_closes, "US", regime["anchor_ticker"])
    if not then_bars:
        raise HTTPException(404, f"anchor {regime['anchor_ticker']} has no ingested bars — "
                                 "deep-backfill the history universe first")
    # THEN: the regime window (peak-anchored); NOW: the trailing `window` bars.
    start, end = date.fromisoformat(regime["start_date"]), date.fromisoformat(regime["end_date"])
    then_win = [(d, c) for d, c in then_bars if start <= d <= end]
    if len(then_win) < 2:
        raise HTTPException(422, f"anchor bars don't cover the regime window {start}–{end} "
                                 "(pre-coverage history is a drawn gap)")
    now_win = bars[-window:]
    then_uw = underwater(then_win)
    now_uw = underwater(now_win)
    from app.analytics._common import rebase
    data = {
        "regime": regime,
        "then": {"path": rebase([c for _, c in then_win]),
                 "dates": [d.isoformat() for d, _ in then_win],
                 "depth_pct": min(v for _, v in then_uw["series"])},
        "now": {"path": rebase([c for _, c in now_win]),
                "dates": [d.isoformat() for d, _ in now_win],
                "depth_pct": now_uw["current"]["dd_pct"],
                "days_since_peak": now_uw["current"]["days_since_peak"]},
    }
    return _envelope(bars, "regime-compare-v1",
                     {"ticker": ticker.upper(), "slug": slug, "window": window}, data)
