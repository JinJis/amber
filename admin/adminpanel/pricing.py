"""COST-1 — the pricing registry + cost math for the admin cost dashboard.

Prices are **operator-editable data, not code**: the defaults below (USD per 1M tokens, Gemini API
list prices verified 2026-07-16 against ai.google.dev/gemini-api/docs/pricing) can be overridden
wholesale with the ``PRICING_JSON`` env var, and the dashboard always shows the registry's ``as_of``
so a stale rate is visible, never silent. Unknown models are shown with tokens but NO invented dollar
figure (honesty over fake data — CLAUDE §2.6).

A rule matches a model id by **longest matching substring** (``rate_for``), so version-specific rules
(``gemini-2.5-flash``) win over generic fallbacks (``flash``) — the fallbacks exist so the ``-latest``
aliases this platform runs (``gemini-flash-latest``/``gemini-pro-latest``/``gemini-flash-lite-latest``)
still price when the resolved ``model_version`` is unavailable. Rule fields:
  ``in`` / ``out``        USD per 1M input / output tokens (required for token-priced models)
  ``cached_in``           USD per 1M *cached* input tokens (cache-hit discount; defaults to ``in``)
  ``premium_over_200k``   {in,out,cached_in} used instead when the request context exceeds 200k tokens
  ``per_call``            USD per call for request-priced products (e.g. Vertex Ranking) — no tokens

PRICING_JSON example:
  {"as_of": "2026-07-16", "source_url": "https://ai.google.dev/gemini-api/docs/pricing", "rules": [
     {"match": "gemini-2.5-flash", "in": 0.30, "out": 2.50, "cached_in": 0.03},
     {"match": "semantic-ranker",  "per_call": 0.001}]}
FIXED_COSTS_JSON adds flat monthly line items:
  {"FMP Ultimate": {"usd": 149, "note": "consensus estimates + transcripts + 13F"}}
"""

from __future__ import annotations

import json
import os

# Gemini API list prices — USD per 1M tokens, text/image/video base tier, verified 2026-07-16 at
# https://ai.google.dev/gemini-api/docs/pricing. Version-specific rules price a pinned model exactly;
# the generic flash/pro/flash-lite fallbacks catch the `-latest` aliases and assume they resolve to
# the newest tier (so a `-latest` bump can't silently under-count). Operators pin exact rates via
# PRICING_JSON if their aliases resolve differently.
_DEFAULT = {
    "as_of": "2026-07-16 (ai.google.dev/gemini-api/docs/pricing — .env PRICING_JSON으로 수정)",
    "source_url": "https://ai.google.dev/gemini-api/docs/pricing",
    "rules": [
        # --- embeddings (input-only) ---
        {"match": "gemini-embedding-001", "in": 0.15, "out": 0.0},
        {"match": "gemini-embedding", "in": 0.20, "out": 0.0},   # gemini-embedding-2 (current default)
        # --- Gemini 3.x ---
        {"match": "gemini-3.5-flash", "in": 1.50, "out": 9.00, "cached_in": 0.15},
        {"match": "gemini-3.1-flash-lite", "in": 0.25, "out": 1.50, "cached_in": 0.025},
        {"match": "gemini-3.1-pro", "in": 2.00, "out": 12.00, "cached_in": 0.20,
         "premium_over_200k": {"in": 4.00, "out": 18.00, "cached_in": 0.40}},
        # --- Gemini 2.5 ---
        {"match": "gemini-2.5-flash-lite", "in": 0.10, "out": 0.40, "cached_in": 0.01},
        {"match": "gemini-2.5-flash", "in": 0.30, "out": 2.50, "cached_in": 0.03},
        {"match": "gemini-2.5-pro", "in": 1.25, "out": 10.00, "cached_in": 0.125,
         "premium_over_200k": {"in": 2.50, "out": 15.00, "cached_in": 0.25}},
        # --- generic `-latest` alias fallbacks (assume newest tier; override via PRICING_JSON) ---
        {"match": "flash-lite", "in": 0.25, "out": 1.50, "cached_in": 0.025},   # → 3.1 flash-lite
        {"match": "flash", "in": 1.50, "out": 9.00, "cached_in": 0.15},          # → 3.5 flash
        {"match": "pro", "in": 1.25, "out": 10.00, "cached_in": 0.125,           # → 2.5 pro (latest stable)
         "premium_over_200k": {"in": 2.50, "out": 15.00, "cached_in": 0.25}},
        # --- request-priced (per-call) products ---
        {"match": "semantic-ranker", "per_call": 0.001},   # Vertex Ranking ≈ $1 / 1k queries
        {"match": "document-ai", "per_call": 0.01},        # Document AI Layout Parser $10 / 1k pages
    ],
}


def registry() -> dict:
    raw = os.environ.get("PRICING_JSON", "")
    if raw:
        try:
            d = json.loads(raw)
            if isinstance(d, dict) and d.get("rules"):
                return d
        except ValueError:
            pass
    return _DEFAULT


def rate_for(model: str) -> dict | None:
    """The pricing rule for a model id — longest matching substring wins; None = unknown."""
    m = (model or "").lower()
    best = None
    for r in registry().get("rules", []):
        s = str(r.get("match", "")).lower()
        if s and s in m and (best is None or len(s) > len(str(best.get("match", "")))):
            best = r
    return best


def is_per_call(model: str) -> bool:
    """True when the model's rule is request-priced (per_call) rather than token-priced."""
    r = rate_for(model)
    return bool(r and r.get("per_call") is not None)


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0, *,
             cached_input_tokens: int = 0, context_tokens: int | None = None,
             calls: int = 0) -> float | None:
    """USD for one usage row, or None when the model has no configured rate (shown as such).

    Backward compatible with the old ``cost_usd(model, in, out)`` positional call. Extras:
      - ``cached_input_tokens``  the subset of input already in ``input_tokens`` that hit context
        cache — billed at the rule's ``cached_in`` discount instead of full ``in``.
      - ``context_tokens``       a *single call's* context size, used to pick the
        ``premium_over_200k`` tier. Opt-in (default None = base tier): pass it only for per-call
        costing — never for aggregated rows, where a 30-day token SUM would falsely trip >200k.
      - ``calls``                number of calls, for request-priced (``per_call``) products.
    """
    r = rate_for(model)
    if r is None:
        return None
    # request-priced products (e.g. Vertex Ranking) — dollars = per_call × calls, no tokens.
    if r.get("per_call") is not None:
        return max(0, int(calls)) * float(r["per_call"])
    # token-priced: pick the >200k premium tier only when an explicit per-call context exceeds 200k.
    rate = r
    prem = r.get("premium_over_200k")
    if prem and context_tokens is not None and int(context_tokens) > 200_000:
        rate = {**r, **prem}
    in_rate = float(rate.get("in", 0))
    out_rate = float(rate.get("out", 0))
    cached_rate = float(rate.get("cached_in", in_rate))   # default: cache priced at full input
    cached = min(max(0, int(cached_input_tokens)), max(0, int(input_tokens)))
    billable_in = max(0, int(input_tokens)) - cached
    return (billable_in / 1e6) * in_rate + (cached / 1e6) * cached_rate + \
           (max(0, int(output_tokens)) / 1e6) * out_rate


def fixed_costs() -> list[dict]:
    """Flat monthly subscriptions (operator-declared): [{name, usd, note}]."""
    raw = os.environ.get("FIXED_COSTS_JSON", "")
    out: list[dict] = []
    if raw:
        try:
            d = json.loads(raw)
            for name, v in (d.items() if isinstance(d, dict) else []):
                if isinstance(v, dict):
                    out.append({"name": name, "usd": float(v.get("usd", 0)), "note": str(v.get("note", ""))})
                else:
                    out.append({"name": name, "usd": float(v), "note": ""})
        except (ValueError, TypeError):
            pass
    return out
