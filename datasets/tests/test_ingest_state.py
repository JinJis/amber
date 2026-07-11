"""Unit tests for the delta-ingest bookkeeping store (OPS-2).

IngestState cursors (done_items/mark_items), the store-freshness financials delta
(fresh_financials_tickers), and the per-key upstream usage counters
(record_upstream_call/usage_today/usage_history) — all against the real SQLite test DB,
no network. Tests use unique kinds/tickers/providers so they never collide with the rest
of the suite sharing the same DB file.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.store import ingest_state as IS
from app.store.db import SessionLocal, init_db
from app.store.models import FinancialFact


# --- delta cursors (done_items / mark_items) --------------------------------------
def test_done_items_unknown_key_is_empty():
    init_db()
    assert IS.done_items("filing_text", "US", "NEVERSEEN") == set()


def test_mark_items_round_trip_merges_and_is_idempotent():
    init_db()
    t = "CURSOR1"
    IS.mark_items("filing_text", "US", t, {"A1"})
    assert IS.done_items("filing_text", "US", t) == {"A1"}
    # merge: a later run adds to the baseline instead of replacing it
    IS.mark_items("filing_text", "US", t, {"A2", "A3"})
    assert IS.done_items("filing_text", "US", t) == {"A1", "A2", "A3"}
    # idempotent: re-marking the same items changes nothing
    IS.mark_items("filing_text", "US", t, {"A1", "A2", "A3"})
    assert IS.done_items("filing_text", "US", t) == {"A1", "A2", "A3"}
    # an empty set is a no-op (never creates/overwrites a row)
    IS.mark_items("filing_text", "US", "CURSOR-EMPTY", set())
    assert IS.done_items("filing_text", "US", "CURSOR-EMPTY") == set()


def test_mark_items_cursor_is_bounded_to_last_200():
    init_db()
    t = "CURSOR2"
    IS.mark_items("filing_text", "US", t, {f"i{i:03d}" for i in range(250)})
    done = IS.done_items("filing_text", "US", t)
    assert len(done) == 200                       # bounded — old ids beyond any refetch window drop
    assert "i249" in done and "i050" in done      # the newest (sorted) 200 survive
    assert "i000" not in done and "i049" not in done


def test_cursor_key_is_normalized_case_insensitively():
    init_db()
    IS.mark_items("filing_text", "us", "lower1", {"X1"})
    assert IS.done_items("filing_text", "US", "LOWER1") == {"X1"}


# --- financials delta (store-freshness) --------------------------------------------
def test_fresh_financials_tickers_only_recent_report_periods():
    init_db()
    today = date.today()
    with SessionLocal() as db:
        # FRSHA: latest stored period is 10 days old (within the current quarter) → FRESH
        db.add(FinancialFact(market="US", ticker="FRSHA", statement="income", line_item="revenue",
                             value=1.0, period="quarterly", report_period=today - timedelta(days=10),
                             source="TEST"))
        db.add(FinancialFact(market="US", ticker="FRSHA", statement="income", line_item="revenue",
                             value=1.0, period="quarterly", report_period=today - timedelta(days=200),
                             source="TEST"))  # an older row too — max(report_period) must win
        # FRSHB: latest stored period is 200 days old → a new quarterly likely exists → STALE
        db.add(FinancialFact(market="US", ticker="FRSHB", statement="income", line_item="revenue",
                             value=1.0, period="quarterly", report_period=today - timedelta(days=200),
                             source="TEST"))
        db.commit()

    fresh = IS.fresh_financials_tickers("US", ["FRSHA", "FRSHB", "NOROWS"])
    assert fresh == {"FRSHA"}    # stale + never-stored tickers are NOT fresh → delta refetches them
    # input tickers are matched case-insensitively
    assert IS.fresh_financials_tickers("US", ["frsha"]) == {"FRSHA"}


# --- upstream quota accounting --------------------------------------------------------
def test_key_label_masks_and_never_leaks_the_key():
    assert IS.key_label("secret-key-abcd") == "…abcd"
    assert IS.key_label("abcd") == "…abcd"
    assert IS.key_label("  padded-key-wxyz  ") == "…wxyz"   # stripped before masking
    assert IS.key_label("abc") == "(unset)"                 # too short to mask safely
    assert IS.key_label("") == "(unset)"
    assert IS.key_label(None) == "(unset)"


def test_kst_today_is_a_calendar_day():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", IS.kst_today())


def test_record_upstream_call_counts_per_key_per_day():
    init_db()
    IS.record_upstream_call("testprov", "unit-key-aaaa")
    IS.record_upstream_call("testprov", "unit-key-aaaa")   # two ticks on one key…
    IS.record_upstream_call("testprov", "unit-key-bbbb")   # …one on another
    assert IS.usage_today("testprov") == {"…aaaa": 2, "…bbbb": 1}
    # n>1 increments in one write (batch accounting)
    IS.record_upstream_call("testprov2", "unit-key-cccc", n=5)
    assert IS.usage_today("testprov2") == {"…cccc": 5}
    # history: today's rows appear, tagged with the KST day + the masked label
    hist = IS.usage_history("testprov")
    assert {"day": IS.kst_today(), "key": "…aaaa", "calls": 2} in hist
    # an unknown provider reads as empty, never an error
    assert IS.usage_today("no-such-provider") == {}
    assert IS.usage_history("no-such-provider") == []
