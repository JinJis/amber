"""COST-1 — pricing registry + cost math tests.

Pure-function tests (no DB, no stack). Cover the substring matcher, the token/cached/premium/per-call
math, the honest unknown-model path, and the PRICING_JSON / FIXED_COSTS_JSON env overrides.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def pricing(monkeypatch):
    """Fresh pricing module with a clean env (defaults), reloaded so registry() re-reads env."""
    monkeypatch.delenv("PRICING_JSON", raising=False)
    monkeypatch.delenv("FIXED_COSTS_JSON", raising=False)
    import adminpanel.pricing as p
    return importlib.reload(p)


# --- rate_for: longest matching substring wins --------------------------------------------------

def test_versioned_model_beats_generic_fallback(pricing):
    # a pinned 3.5-flash must NOT fall through to the generic `flash` rule (which is also 3.5-priced,
    # but the version rule is the authoritative one).
    r = pricing.rate_for("gemini-3.5-flash")
    assert r["match"] == "gemini-3.5-flash" and r["in"] == 1.50 and r["out"] == 9.00


def test_25_pro_matches_its_own_rule_not_generic_pro(pricing):
    r = pricing.rate_for("gemini-2.5-pro")
    assert r["match"] == "gemini-2.5-pro" and r["in"] == 1.25
    assert "premium_over_200k" in r


def test_flash_lite_alias_beats_flash(pricing):
    # `gemini-flash-lite-latest` contains both "flash" and "flash-lite"; the longer wins.
    r = pricing.rate_for("gemini-flash-lite-latest")
    assert r["match"] == "flash-lite"


def test_latest_aliases_resolve_to_generic_rules(pricing):
    assert pricing.rate_for("gemini-flash-latest")["match"] == "flash"
    assert pricing.rate_for("gemini-pro-latest")["match"] == "pro"


def test_embedding_2_vs_001(pricing):
    # gemini-embedding-2 → the generic embedding rule ($0.20); the pinned -001 keeps $0.15.
    assert pricing.rate_for("gemini-embedding-2")["in"] == 0.20
    assert pricing.rate_for("gemini-embedding-001")["in"] == 0.15


def test_unknown_model_has_no_rule(pricing):
    assert pricing.rate_for("gpt-4o") is None
    assert pricing.rate_for("") is None


# --- cost_usd: token math -----------------------------------------------------------------------

def test_cost_basic_tokens(pricing):
    # 1M in + 1M out on 2.5-flash = 0.30 + 2.50
    assert pricing.cost_usd("gemini-2.5-flash", 1_000_000, 1_000_000) == pytest.approx(2.80)


def test_cost_backward_compatible_positional(pricing):
    # the old 3-positional call still works.
    assert pricing.cost_usd("gemini-2.5-pro", 2_000_000, 0) == pytest.approx(2.50)


def test_unknown_model_returns_none_never_zero(pricing):
    # honesty: an unpriced model is None (renders "요율 미설정"), never a fake $0.
    assert pricing.cost_usd("mystery-model", 1_000_000, 1_000_000) is None


def test_negative_tokens_clamped(pricing):
    assert pricing.cost_usd("gemini-2.5-flash", -5, -5) == 0.0


# --- cost_usd: cached input discount ------------------------------------------------------------

def test_cached_input_discounted(pricing):
    # 1M input of which 1M cached, on 2.5-flash: all billed at cached 0.03, none at full 0.30.
    full = pricing.cost_usd("gemini-2.5-flash", 1_000_000, 0)
    cached = pricing.cost_usd("gemini-2.5-flash", 1_000_000, 0, cached_input_tokens=1_000_000)
    assert full == pytest.approx(0.30)
    assert cached == pytest.approx(0.03)


def test_partial_cache_splits_rate(pricing):
    # 1M input, 600k cached: 400k @0.30 + 600k @0.03 (per 1M).
    got = pricing.cost_usd("gemini-2.5-flash", 1_000_000, 0, cached_input_tokens=600_000)
    assert got == pytest.approx((0.4 * 0.30) + (0.6 * 0.03))


def test_cached_never_exceeds_input(pricing):
    # a cached count larger than input is clamped (no negative billable input).
    got = pricing.cost_usd("gemini-2.5-flash", 100, 0, cached_input_tokens=10_000)
    assert got >= 0.0


def test_cache_defaults_to_full_rate_when_rule_lacks_cached_in(pricing, monkeypatch):
    monkeypatch.setenv("PRICING_JSON", '{"as_of":"x","rules":[{"match":"foo","in":1.0,"out":0.0}]}')
    p = importlib.reload(pricing)
    # no cached_in on the rule → cached tokens billed at the full input rate (0.30-style default).
    assert p.cost_usd("foo", 1_000_000, 0, cached_input_tokens=1_000_000) == pytest.approx(1.0)


# --- cost_usd: >200k premium tier (opt-in) ------------------------------------------------------

def test_premium_applies_only_with_explicit_context(pricing):
    # a 30-day SUM of 2M tokens must NOT trip the >200k premium (aggregate rows pass no context).
    base = pricing.cost_usd("gemini-2.5-pro", 2_000_000, 0)
    assert base == pytest.approx(2.50)   # base 1.25, not premium 2.50/1M


def test_premium_when_single_call_context_over_200k(pricing):
    # a per-call cost with 300k context → premium in-rate 2.50 (not 1.25).
    got = pricing.cost_usd("gemini-2.5-pro", 300_000, 0, context_tokens=300_000)
    assert got == pytest.approx(0.3 * 2.50)


def test_premium_not_applied_under_200k(pricing):
    got = pricing.cost_usd("gemini-2.5-pro", 100_000, 0, context_tokens=100_000)
    assert got == pytest.approx(0.1 * 1.25)


# --- cost_usd: per-call (request-priced) products -----------------------------------------------

def test_per_call_dollarizes_from_calls(pricing):
    # Vertex Ranking: 2500 calls × $0.001 = $2.50, regardless of tokens.
    assert pricing.cost_usd("semantic-ranker-default@latest", 0, 0, calls=2500) == pytest.approx(2.50)


def test_per_call_ignores_tokens(pricing):
    assert pricing.cost_usd("semantic-ranker-default-004", 5_000, 5_000, calls=10) == pytest.approx(0.01)


def test_is_per_call_flag(pricing):
    assert pricing.is_per_call("semantic-ranker-default-004") is True
    assert pricing.is_per_call("gemini-2.5-flash") is False
    assert pricing.is_per_call("unknown") is False


def test_docai_per_page_price(pricing):
    # Document AI Layout Parser: $10 / 1k pages = $0.01/page → 1500 pages = $15.
    assert pricing.cost_usd("document-ai-layout", calls=1000) == pytest.approx(10.0)
    assert pricing.cost_usd("document-ai-layout", calls=1500) == pytest.approx(15.0)
    assert pricing.is_per_call("document-ai-layout") is True


# --- registry / fixed_costs env overrides -------------------------------------------------------

def test_pricing_json_overrides_defaults(pricing, monkeypatch):
    monkeypatch.setenv("PRICING_JSON", '{"as_of":"2099","rules":[{"match":"flash","in":9,"out":9}]}')
    p = importlib.reload(pricing)
    assert p.registry()["as_of"] == "2099"
    assert p.cost_usd("gemini-2.5-flash", 1_000_000, 0) == pytest.approx(9.0)  # override wins


def test_malformed_pricing_json_falls_back(pricing, monkeypatch):
    monkeypatch.setenv("PRICING_JSON", "{not json")
    p = importlib.reload(pricing)
    assert "ai.google.dev" in p.registry()["source_url"]   # default kept


def test_pricing_json_without_rules_ignored(pricing, monkeypatch):
    monkeypatch.setenv("PRICING_JSON", '{"as_of":"x"}')   # no rules key
    p = importlib.reload(pricing)
    assert p.rate_for("gemini-2.5-flash")["match"] == "gemini-2.5-flash"  # default rules used


def test_default_registry_is_current(pricing):
    assert "2026-07-16" in pricing.registry()["as_of"]
    assert pricing.registry()["source_url"].startswith("https://ai.google.dev")


def test_fixed_costs_dict_and_scalar(pricing, monkeypatch):
    monkeypatch.setenv("FIXED_COSTS_JSON",
                       '{"FMP":{"usd":149,"note":"transcripts"},"Flat":29}')
    p = importlib.reload(pricing)
    fc = {f["name"]: f for f in p.fixed_costs()}
    assert fc["FMP"]["usd"] == 149.0 and fc["FMP"]["note"] == "transcripts"
    assert fc["Flat"]["usd"] == 29.0 and fc["Flat"]["note"] == ""


def test_fixed_costs_empty_by_default(pricing):
    assert pricing.fixed_costs() == []


def test_fixed_costs_malformed_ignored(pricing, monkeypatch):
    monkeypatch.setenv("FIXED_COSTS_JSON", "not json")
    p = importlib.reload(pricing)
    assert p.fixed_costs() == []
