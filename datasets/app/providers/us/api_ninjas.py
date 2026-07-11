"""API Ninjas (premium) — earnings calendar with consensus vs actual (surprise precomputed).

V-8: the earnings-surprise data source. Same response contract as the FMP provider (the
/earnings-calendar route swaps between them), but deeper history (~50 quarters) and the
difference/% fields come precomputed from the upstream — we pass them through as-sourced,
never recompute-and-overwrite (reconcile, don't fabricate).
"""

from __future__ import annotations

from app.config import settings
from app.errors import bad_request
from app.http import fetch_json

_URL = "https://api.api-ninjas.com/v1/earningscalendar"


def _key() -> str:
    if not settings.api_ninjas_key:
        raise bad_request("API_NINJAS_KEY is not configured.")
    return settings.api_ninjas_key


async def earnings_calendar(symbol: str, limit: int = 8) -> dict:
    """A company's earnings dates with consensus vs actual EPS/revenue + surprise %, newest first.
    Upcoming events ride along with null actuals (drawn as gaps, never fabricated)."""
    rows = await fetch_json("api_ninjas", _URL, params={"ticker": symbol.upper()},
                            headers={"X-Api-Key": _key()})
    events = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        events.append({
            "date": r.get("date"),
            "eps_estimated": r.get("estimated_eps"), "eps_actual": r.get("actual_eps"),
            "eps_surprise": r.get("eps_difference"), "eps_surprise_pct": r.get("eps_difference_pct"),
            "revenue_estimated": r.get("estimated_revenue"), "revenue_actual": r.get("actual_revenue"),
            "revenue_surprise": r.get("revenue_difference"),
            "revenue_surprise_pct": r.get("revenue_difference_pct"),
            "timing": r.get("earnings_timing"),
        })
        if len(events) >= limit:
            break
    return {"symbol": symbol.upper(), "source": "API Ninjas (earnings calendar)", "events": events}
