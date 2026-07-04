"""History Lab REST (HL-4) — /history/* endpoints over a seeded PriceBar store.

Covers the envelope contract (source/method/as_of/label/history_span), the honest error paths
(404 no bars, 422 insufficient/unsupported), store-vs-live episode serving, regime seeding, and
the regime-compare composition. No network."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app
from app.store.db import SessionLocal, init_db
from app.store.models import PriceBar

client = TestClient(app)

LABEL = "과거 기록 · 전망 아님"
T = "ZHIST"  # synthetic anchor, US market (isolated from other tests' tickers)


def _seed(ticker: str, prices: list[float], start: str = "2020-01-01", market: str = "US"):
    init_db()
    d0 = date.fromisoformat(start)
    with SessionLocal() as db:
        for i, p in enumerate(prices):
            db.add(PriceBar(market=market, ticker=ticker, interval="day",
                            bar_date=d0 + timedelta(days=i), close=p, source="t"))
        db.commit()


def setup_module(_m):
    # sawtooth with a −50% episode then recovery + new high (same fixture as the analytics tests)
    _seed(T, [100, 120, 60, 120, 130])


def test_drawdowns_envelope_and_series():
    r = client.get("/history/drawdowns", params={"ticker": T, "market": "US"})
    assert r.status_code == 200
    b = r.json()
    # the envelope contract — every /history/* response carries these
    assert b["label"] == LABEL and b["method"] == "dd-v1" and b["cadence"] == "daily"
    assert b["source"].startswith("derived:") and b["history_span"]["from"] == "2020-01-01"
    dd = [v for _, v in b["data"]["series"]]
    assert min(dd) == -50.0 and b["data"]["current"]["dd_pct"] == 0.0


def test_drawdowns_404_when_no_bars():
    r = client.get("/history/drawdowns", params={"ticker": "ZNONE", "market": "US"})
    assert r.status_code == 404 and "pipeline" in r.json()["detail"]


def test_episodes_live_then_stored():
    r = client.get("/history/episodes", params={"ticker": T, "market": "US", "threshold": 20})
    assert r.status_code == 200
    b = r.json()
    assert b["data"]["from_store"] is False and b["data"]["n"] == 1
    assert b["data"]["episodes"][0]["depth_pct"] == -50.0

    # after the sweep persists them, the endpoint serves the stored rows (same algorithm/values)
    from app.store.history import recompute_episodes
    assert recompute_episodes("US", T) > 0
    b2 = client.get("/history/episodes", params={"ticker": T, "market": "US", "threshold": 20}).json()
    assert b2["data"]["from_store"] is True and b2["data"]["episodes"][0]["depth_pct"] == -50.0
    assert b2["label"] == LABEL


def test_vol_context_shape():
    _seed("ZVOL", [100 + (i % 7) for i in range(40)], start="2021-01-01")
    b = client.get("/history/vol-context", params={"ticker": "ZVOL", "market": "US"}).json()
    assert b["label"] == LABEL and "20" in b["data"]["windows"]
    w = b["data"]["windows"]["20"]
    assert w["realized_vol_pct"] is not None and 0 <= w["percentile"] <= 100


def test_base_rates_endpoint_and_event_validation():
    _seed("ZBR", [100, 90, 95, 100, 105], start="2022-01-01")
    r = client.get("/history/base-rates", params={
        "ticker": "ZBR", "market": "US", "event": '{"daily_return_lte": -10.0}', "horizons": "1,3"})
    assert r.status_code == 200
    b = r.json()
    assert b["label"] == LABEL and b["data"]["n"] == 1
    assert b["data"]["event_dates"] == ["2022-01-02"]
    assert {h["h"] for h in b["data"]["horizons"]} == {1, 3}

    # honest 422s: malformed JSON / unsupported event kind / bad horizons
    assert client.get("/history/base-rates", params={
        "ticker": "ZBR", "market": "US", "event": "notjson"}).status_code == 422
    r = client.get("/history/base-rates", params={
        "ticker": "ZBR", "market": "US", "event": '{"nope": 1}'})
    assert r.status_code == 422 and "supported" in r.json()["detail"]
    assert client.get("/history/base-rates", params={
        "ticker": "ZBR", "market": "US", "event": '{"daily_return_lte": -10.0}',
        "horizons": "0,-3"}).status_code == 422


def test_analogues_endpoint_and_insufficient_history():
    _seed("ZANA", list(range(100, 160)), start="2019-01-01")
    b = client.get("/history/analogues",
                   params={"ticker": "ZANA", "market": "US", "window": 10, "k": 3}).json()
    assert b["label"] == LABEL and b["data"]["current"]["path"][0] == 100.0
    # a window larger than the history → 422 with the gap explained
    r = client.get("/history/analogues", params={"ticker": "ZANA", "market": "US", "window": 500})
    assert r.status_code == 422 and "backfill" in r.json()["detail"]


def test_regimes_seed_on_demand_and_filter():
    b = client.get("/history/regimes").json()
    slugs = {x["slug"] for x in b["data"]["regimes"]}
    assert {"dotcom-bust", "gfc-2008", "kr-imf-1997"} <= slugs
    assert b["label"] == LABEL and b["n"] >= 15
    for x in b["data"]["regimes"]:
        assert x["sources"], f"{x['slug']} must cite sources"
    kr = client.get("/history/regimes", params={"market": "KR"}).json()["data"]["regimes"]
    assert kr and all(x["market"] == "KR" for x in kr)


def test_regime_compare_composition():
    # seed the covid regime's anchor (^GSPC) INSIDE its window (2020-02-19 ~ 2020-03-23)
    d0, prices = "2020-02-19", [3380, 3350, 3300, 3200, 3000, 2800, 2600, 2500, 2400, 2300, 2237, 2400, 2600]
    _seed("^GSPC", prices, start=d0)
    r = client.get("/history/regime-compare",
                   params={"ticker": T, "market": "US", "slug": "covid-crash-2020", "window": 5})
    assert r.status_code == 200
    b = r.json()
    assert b["label"] == LABEL and b["data"]["regime"]["slug"] == "covid-crash-2020"
    assert b["data"]["then"]["path"][0] == 100.0 and b["data"]["then"]["depth_pct"] < -20
    assert b["data"]["now"]["path"] and "days_since_peak" in b["data"]["now"]

    assert client.get("/history/regime-compare",
                      params={"ticker": T, "market": "US", "slug": "no-such"}).status_code == 404


def test_recompute_episodes_idempotent():
    from app.store.history import list_episodes, recompute_episodes
    n1 = recompute_episodes("US", T)
    n2 = recompute_episodes("US", T)         # re-run replaces, never duplicates
    assert n1 == n2 == len(list_episodes("US", T, 20.0)) + len(list_episodes("US", T, 10.0))


# --- HL-1: deep backfill + history universe --------------------------------
def test_history_universe_symbols_parse(monkeypatch):
    from app.config import settings
    from app.store.prices_ingest import history_universe_symbols
    monkeypatch.setattr(settings, "history_universe", " ^gspc , ^VIX,, GC=F ")
    assert history_universe_symbols() == {"^GSPC", "^VIX", "GC=F"}


async def test_prices_ingest_deep_start_for_universe_anchor(monkeypatch):
    """HL-1: a history-universe anchor with no stored bars starts at history_backfill_start
    ("max"); an ordinary ticker keeps the N-year window; both stay incremental afterwards."""
    from datetime import date as _d

    import app.store.prices_ingest as PI
    from app.config import settings

    monkeypatch.setattr(settings, "history_universe", "^GSPC")
    monkeypatch.setattr(settings, "history_backfill_start", "1920-01-01")
    monkeypatch.setattr(PI, "_last_bar_date", lambda market, t: None)  # nothing stored yet
    starts: dict[str, _d] = {}

    async def fake_ingest(mk, t, start, end, retries=1):
        starts[t] = start
        return 1

    monkeypatch.setattr(PI, "ingest_prices_ticker", fake_ingest)
    await PI.run_prices_ingest("US", ["^GSPC", "AAPL"], years=10)
    assert starts["^GSPC"] == _d(1920, 1, 1)                    # anchor → max backfill
    assert starts["AAPL"].year == _d.today().year - 10          # ordinary → 10y window


async def test_prices_sweep_includes_universe_and_recomputes(monkeypatch):
    """HL-1/HL-2: the US prices sweep appends the anchor universe (deduped) and re-derives
    episodes + seeds regimes afterwards; the KR sweep is untouched."""
    import app.store.history as SH
    import app.store.prices_ingest as PI
    from app.config import settings
    from app.pipelines import _run_prices

    monkeypatch.setattr(settings, "history_universe", "^GSPC,^VIX")
    calls: dict = {"tickers": None, "recomputed": [], "seeded": 0}

    async def fake_run(market, tickers, years):
        calls["tickers"] = list(tickers)
        return {}

    monkeypatch.setattr(PI, "run_prices_ingest", fake_run)
    monkeypatch.setattr(SH, "recompute_episodes",
                        lambda m, t, **k: calls["recomputed"].append(t) or 0)
    monkeypatch.setattr(SH, "seed_regimes", lambda: calls.update(seeded=calls["seeded"] + 1) or 0)

    await _run_prices("US", ["AAPL", "^GSPC"])                  # ^GSPC already present → deduped
    assert calls["tickers"].count("^GSPC") == 1 and "^VIX" in calls["tickers"]
    assert calls["recomputed"] == ["^GSPC", "^VIX"] and calls["seeded"] == 1

    calls["tickers"] = None
    await _run_prices("KR", ["005930"])                         # KR sweep: no anchors appended
    assert calls["tickers"] == ["005930"]
