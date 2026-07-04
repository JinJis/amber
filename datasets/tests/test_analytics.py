"""History Lab analytics (HL-3) — pure-function tests on synthetic fixtures with hand-computed
expected values (HISTORY_LAB_SPEC §10). No network, no store."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.analytics import _common as C
from app.analytics.analogue import analogues
from app.analytics.base_rates import base_rates
from app.analytics.drawdown import episodes, episodes_result, underwater
from app.analytics.regimes_seed import REGIMES, regimes_for
from app.analytics.volatility import realized_vol, vol_context


def _bars(prices, start="2020-01-01", step_days=1):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i * step_days), float(p)) for i, p in enumerate(prices)]


# --- _common -------------------------------------------------------------
def test_pearson_and_zscore_and_percentile():
    assert C.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert C.pearson([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert C.pearson([1, 1, 1], [1, 1, 1]) is None          # zero variance → undefined
    assert C.zscore([5, 5, 5]) is None                       # flat window → degenerate
    z = C.zscore([0, 1, 2, 3, 4])
    assert z is not None and z[0] == pytest.approx(-z[-1])   # symmetric around mean
    assert C.percentile_rank([1, 2, 3, 4], 4) == 100.0       # inclusive: ≤ max → 100%
    assert C.percentile_rank([1, 2, 3, 4], 1) == 25.0


def test_distribution_and_rebase():
    d = C.distribution([1, 2, 3, 4, 5])
    assert d["n"] == 5 and d["p50"] == 3 and d["min"] == 1 and d["max"] == 5
    assert C.rebase([50, 100, 25]) == [100.0, 200.0, 50.0]
    assert C.clean_bars([(date(2020, 1, 2), 10), (date(2020, 1, 1), None), (date(2020, 1, 1), 9)]) == \
        [(date(2020, 1, 1), 9.0), (date(2020, 1, 2), 10.0)]  # drops None, sorts by date


# --- drawdown ------------------------------------------------------------
def test_underwater_sawtooth():
    uw = underwater(_bars([100, 120, 60, 120, 130]))
    dd = [v for _, v in uw["series"]]
    assert dd == [0.0, 0.0, -50.0, 0.0, 0.0]                 # trough at 60 vs peak 120 → −50%
    assert uw["current"]["dd_pct"] == 0.0                     # ends at a new high
    assert uw["current"]["peak_date"] == "2020-01-05"


def test_underwater_needs_two_bars():
    with pytest.raises(ValueError):
        underwater(_bars([100]))


def test_episode_recovers_known_depth():
    eps = episodes(_bars([100, 120, 60, 120, 130]), threshold_pct=20)
    assert len(eps) == 1
    e = eps[0]
    assert e["depth_pct"] == -50.0 and e["is_open"] is False
    assert e["peak_date"] == "2020-01-02" and e["trough_date"] == "2020-01-03"
    assert e["recovery_date"] == "2020-01-04"
    assert e["decline_days"] == 1 and e["recovery_days"] == 1


def test_episode_open_when_never_recovers():
    eps = episodes(_bars([100, 120, 60]), threshold_pct=20)
    assert len(eps) == 1 and eps[0]["is_open"] is True and eps[0]["recovery_date"] is None


def test_multiple_episodes_and_threshold_gate():
    prices = [100, 79, 100, 70]  # −21% (opens, recovers), then −30% (opens, stays open)
    assert len(episodes(_bars(prices), threshold_pct=20)) == 2
    # a 25% threshold ignores the −21% episode → only the −30% one qualifies
    assert len(episodes(_bars(prices), threshold_pct=25)) == 1


def test_episodes_result_sorted_deepest_first():
    r = episodes_result(_bars([100, 79, 100, 70]), threshold_pct=20)
    assert r["label"] == C.LABEL and r["n"] == 2
    assert r["episodes"][0]["depth_pct"] <= r["episodes"][1]["depth_pct"]  # deepest (most −) first


# --- base rates ----------------------------------------------------------
def test_base_rates_forward_returns_and_dates():
    # one −10% day at index 1 (100→90); forward closes 95, 100, 105
    r = base_rates(_bars([100, 90, 95, 100, 105]), {"daily_return_lte": -10.0}, horizons=(1, 3))
    assert r["label"] == C.LABEL and r["n"] == 1
    assert r["event_dates"] == ["2020-01-02"]
    h1 = next(h for h in r["horizons"] if h["h"] == 1)
    h3 = next(h for h in r["horizons"] if h["h"] == 3)
    assert h1["n"] == 1 and h1["median"] == pytest.approx(95 / 90 * 100 - 100, abs=1e-2)   # +5.56%
    assert h3["median"] == pytest.approx(105 / 90 * 100 - 100, abs=1e-2)                    # +16.67%
    assert h1["pos_share"] == 100.0


def test_base_rates_clustering_guard():
    # two consecutive −10% days → clustered to ONE event (raw_n=2, n=1)
    r = base_rates(_bars([100, 90, 81, 100, 110]), {"daily_return_lte": -10.0},
                   horizons=(1,), min_gap_days=10)
    assert r["raw_n"] == 2 and r["n"] == 1


def test_base_rates_short_tail_horizon_drops_events():
    # event at the very end has no room for a 5-day horizon → n_h = 0 there
    r = base_rates(_bars([100, 90, 95]), {"daily_return_lte": -10.0}, horizons=(1, 5))
    assert next(h for h in r["horizons"] if h["h"] == 1)["n"] == 1
    assert next(h for h in r["horizons"] if h["h"] == 5)["n"] == 0


def test_base_rates_drawdown_event():
    r = base_rates(_bars([100, 120, 60, 120, 130]), {"drawdown_gte": 20.0}, horizons=(1,))
    assert r["n"] == 1  # one episode crossed −20%


def test_base_rates_rejects_unknown_event():
    with pytest.raises(ValueError):
        base_rates(_bars([100, 90]), {"nonsense": 1})


# --- analogue ------------------------------------------------------------
def test_analogue_finds_similar_past_window_and_excludes_self():
    query = _bars([100, 101, 102, 103, 104], start="2025-01-01")       # recent rising window
    hist = _bars([200, 100, 50, 51, 52, 53, 54, 40, 30], start="2010-01-01")  # contains a rising run
    r = analogues(query, {"X": hist}, window=5, k=3, step=1)
    assert r["label"] == C.LABEL and r["matches"]
    top = r["matches"][0]
    assert top["score"] > 0.9                                           # same z-normalized shape
    # no returned match overlaps the query's own dates (all candidate windows are in 2010)
    assert all(m["start_date"].startswith("2010") for m in r["matches"])
    assert r["current"]["path"][0] == 100.0                             # current path rebased to 100


def test_analogue_insufficient_and_degenerate():
    assert analogues(_bars([100, 101]), {"X": _bars([1, 2, 3, 4, 5])}, window=5)["matches"] == []
    flat = analogues(_bars([100] * 6, start="2025-01-01"),
                     {"X": _bars([1, 2, 3, 4, 5, 6])}, window=5)
    assert flat["matches"] == [] and flat.get("error") == "degenerate_query_window"


def test_analogue_non_overlap_suppression():
    # a candidate with a long monotonic ramp: many overlapping windows score high; only
    # non-overlapping (≥ window//2 apart) ones are kept.
    ramp = _bars(list(range(100, 140)), start="2000-01-01")
    query = _bars([100, 101, 102, 103, 104, 105], start="2025-01-01")
    r = analogues(query, {"R": ramp}, window=6, k=5, step=1)
    starts = sorted(m["start_date"] for m in r["matches"])
    assert len(starts) == len(set(starts))                              # no duplicate/overlapping starts


# --- volatility ----------------------------------------------------------
def test_realized_vol_zero_for_flat_series():
    assert realized_vol([100, 100, 100, 100], window=3) == 0.0
    assert realized_vol([100, 101], window=5) is None                   # too few returns


def test_vol_context_percentile_and_level():
    # a series with a calm stretch then a volatile stretch → current window sits high in its history
    prices = [100, 100.1, 100, 100.1, 100, 110, 95, 112, 90, 115]
    ctx = vol_context(_bars(prices), windows=(3,))
    w = ctx["windows"]["3"]
    assert w["realized_vol_pct"] is not None and 0 <= w["percentile"] <= 100
    assert ctx["label"] == C.LABEL
    # VIX-style level percentile: latest is the max → 100th percentile
    lvl = vol_context(_bars([10, 20, 30, 40]), windows=(3,), is_level=True)["level"]
    assert lvl["current"] == 40.0 and lvl["percentile"] == 100.0


# --- regimes seed --------------------------------------------------------
def test_regimes_seed_shape_and_sources():
    assert len(REGIMES) >= 15
    slugs = [r["slug"] for r in REGIMES]
    assert len(slugs) == len(set(slugs))                                # unique slugs
    for r in REGIMES:
        assert r["sources"] and all(s.get("url") and s.get("publisher") for s in r["sources"])
        assert r["market"] in ("US", "KR", "GLOBAL") and r["kind"] in (
            "bubble", "crisis", "bear", "rate_cycle", "recovery")
        assert r["start_date"] <= r["end_date"]
    assert {r["slug"] for r in regimes_for("KR")} and all(r["market"] == "KR" for r in regimes_for("KR"))
