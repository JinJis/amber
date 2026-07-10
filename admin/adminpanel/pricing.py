"""COST-1 — the pricing registry + cost math for the admin cost dashboard.

Prices are **operator-editable data, not code**: the defaults below (USD per 1M tokens, Gemini API
list prices as of 2026-01) can be overridden wholesale with the ``PRICING_JSON`` env var, and the
dashboard always shows the registry's ``as_of`` so a stale rate is visible, never silent. Unknown
models are shown with tokens but NO invented dollar figure (honesty over fake data).

PRICING_JSON example:
  {"as_of": "2026-07-01", "rules": [
     {"match": "gemini-embedding", "in": 0.15, "out": 0.0},
     {"match": "flash-lite",       "in": 0.10, "out": 0.40},
     {"match": "flash",            "in": 0.30, "out": 2.50},
     {"match": "pro",              "in": 1.25, "out": 10.0}]}
Longest matching substring wins. FIXED_COSTS_JSON adds flat monthly line items:
  {"FMP Starter": {"usd": 29, "note": "consensus estimates + calendar"}}
"""

from __future__ import annotations

import json
import os

_DEFAULT = {
    "as_of": "2026-01 (Gemini API 정가 — .env PRICING_JSON으로 수정)",
    "rules": [
        {"match": "gemini-embedding", "in": 0.15, "out": 0.0},
        {"match": "flash-lite", "in": 0.10, "out": 0.40},
        {"match": "flash", "in": 0.30, "out": 2.50},
        {"match": "pro", "in": 1.25, "out": 10.0},
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


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD for one usage row, or None when the model has no configured rate (shown as such)."""
    r = rate_for(model)
    if r is None:
        return None
    return (max(0, input_tokens) / 1e6) * float(r.get("in", 0)) + \
           (max(0, output_tokens) / 1e6) * float(r.get("out", 0))


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
