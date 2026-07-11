"""M-DERIV (DRV-1) — the data plane embeds `computation` at the computation site."""

from __future__ import annotations

from app.derivation import calc_row, computation, fmt
from app.models.generated import FinancialMetricSnapshot
from app.providers.us.sec_edgar import _latest_row, _snapshot_derivation
from app.store.technical import _tech_derivation


def test_calc_row_and_fmt():
    r = calc_row("EPS", fmt(6.42), source="SEC EDGAR · 10-K", symbol="EPS",
                 evidence={"market": "US", "accession": "a", "concept": "EPS", "value": 6.42})
    assert r["symbol"] == "EPS" and r["evidence"]["accession"] == "a"
    assert fmt(391_035_000_000) == "391.04B" and fmt(2.5e12) == "2.50T" and fmt(None) == "—"
    c = computation("m", "f = a ÷ b", inputs=[r], note="n")
    assert c["formula"] == "f = a ÷ b" and c["inputs"][0]["label"] == "EPS" and c["note"] == "n"


def test_latest_row_carries_accession_and_concept():
    gaap = {"EarningsPerShareDiluted": {"units": {"USD/shares": [
        {"end": "2024-09-28", "val": 6.08, "accn": "0000320193-24-000123", "form": "10-K"},
        {"end": "2025-09-27", "val": 6.42, "accn": "0000320193-25-000073", "form": "10-K"},
    ]}}}
    val, row = _latest_row(gaap, ["EarningsPerShareDiluted", "EarningsPerShareBasic"])
    assert val == 6.42
    assert row["accn"] == "0000320193-25-000073" and row["concept"] == "EarningsPerShareDiluted"


def test_snapshot_derivation_inputs_have_evidence_and_symbols():
    snap = FinancialMetricSnapshot(ticker="AAPL", market_cap=3.2e12,
                                   price_to_earnings_ratio=32.79, price_to_book_ratio=45.1)
    eps_row = {"val": 6.42, "end": "2025-09-27", "accn": "0000320193-25-000073",
               "form": "10-K", "concept": "EarningsPerShareDiluted"}
    eq_row = {"val": 7.1e10, "end": "2025-09-27", "accn": "0000320193-25-000073",
              "form": "10-K", "concept": "StockholdersEquity"}
    d = _snapshot_derivation(210.5, None, eps_row, eq_row, "0000320193", snap)
    assert d["formula"].startswith("시가총액 = P × S")
    by_symbol = {r.get("symbol"): r for r in d["inputs"]}
    assert by_symbol["P"]["source"] == "가격 체인 (지연 시세)"
    assert by_symbol["EPS"]["evidence"]["accession"] == "0000320193-25-000073"
    assert by_symbol["EPS"]["evidence"]["cik"] == "0000320193"
    assert by_symbol["E"]["evidence"]["concept"] == "StockholdersEquity"
    steps = {r["label"]: r["value"] for r in d["steps"]}
    assert steps["PER"] == "32.79x" and "시가총액" in steps
    assert d["note"]  # honest period-mismatch caveat is mandatory


def test_snapshot_derivation_none_when_nothing_derived():
    snap = FinancialMetricSnapshot(ticker="ZZZ")  # no price → nothing computed
    assert _snapshot_derivation(None, None, None, None, "0000000000", snap) is None


def test_tech_derivation_formulas_and_windows():
    d = _tech_derivation(["2026-01-02", "2026-07-01"], [1.0] * 120,
                         [("sma", 20), ("rsi", 14), ("macd", 0)])
    assert "SMA(n)" in d["formula"] and "RSI(n)" in d["formula"] and "MACD" in d["formula"]
    assert d["inputs"][0]["label"] == "종가 시계열" and "120 bars" in d["inputs"][0]["value"]
    windows = {r["label"]: r["value"] for r in d["assumptions"]}
    assert windows["SMA 윈도우"] == "20일" and windows["RSI 윈도우"] == "14일"
    assert "MACD 윈도우" not in windows  # macd has fixed spans, no n row
    assert "신호 아님" in d["method"]
