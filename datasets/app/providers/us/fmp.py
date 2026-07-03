"""CE-11: Financial Modeling Prep (FMP) — analyst consensus estimates + earnings calendar.

These are **third-party licensed DATA shown as-sourced** (publisher = analyst consensus via FMP),
NEVER re-stated as our forecast/target (CLAUDE §5 / DATA_EXPANSION §E). We deliberately surface
only forward financial ESTIMATES (revenue/EPS) + the earnings calendar — NOT price targets or
buy/sell ratings, which our guardrail brand refuses. Key stays server-side (FMP_API_KEY).

FMP's legacy v3 endpoints are retired for new keys; this uses the current `/stable` API.
"""

from __future__ import annotations

from app.config import settings
from app.errors import bad_request, upstream_error
from app.http import fetch_json
from app.providers._parse_utils import parse_float as _num  # comma-aware shared parser (RF-02)

_BASE = "https://financialmodelingprep.com/stable"


def _key() -> str:
    if not settings.fmp_api_key:
        raise bad_request("FMP_API_KEY is not configured.")
    return settings.fmp_api_key


async def _get(path: str, params: dict) -> list:
    data = await fetch_json("fmp", f"{_BASE}/{path}", params={**params, "apikey": _key()})
    if isinstance(data, dict) and data.get("Error Message"):
        raise upstream_error("fmp", str(data["Error Message"])[:160])
    return data if isinstance(data, list) else []


async def consensus_estimates(symbol: str, period: str = "annual", limit: int = 5) -> dict:
    """Analyst CONSENSUS estimates (revenue/EPS/net income, low/avg/high) per future period —
    third-party data, labelled as such. Newest first."""
    rows = await _get("analyst-estimates", {"symbol": symbol.upper(), "period": period, "limit": limit})
    out = []
    for r in rows:
        out.append({
            "date": r.get("date"),
            "revenue_avg": _num(r.get("revenueAvg")), "revenue_low": _num(r.get("revenueLow")),
            "revenue_high": _num(r.get("revenueHigh")),
            "eps_avg": _num(r.get("epsAvg")), "eps_low": _num(r.get("epsLow")), "eps_high": _num(r.get("epsHigh")),
            "net_income_avg": _num(r.get("netIncomeAvg")), "ebitda_avg": _num(r.get("ebitdaAvg")),
            "num_analysts": r.get("numAnalystsRevenue") or r.get("numAnalystsEps"),
        })
    return {"symbol": symbol.upper(), "period": period, "source": "FMP (애널리스트 컨센서스 · 제3자)",
            "estimates": out}


async def earnings_calendar(symbol: str, limit: int = 8) -> dict:
    """A company's earnings dates with consensus vs actual EPS/revenue (a 'surprise' read).
    The FMP calendar is market-wide on this tier, so we filter to the symbol client-side."""
    rows = await _get("earnings-calendar", {"symbol": symbol.upper(), "limit": 250})
    sym = symbol.upper()
    events = []
    for r in rows:
        if (r.get("symbol") or "").upper() != sym:
            continue
        eps_a, eps_e = _num(r.get("epsActual")), _num(r.get("epsEstimated"))
        events.append({
            "date": r.get("date"), "eps_estimated": eps_e, "eps_actual": eps_a,
            "eps_surprise": (eps_a - eps_e) if (eps_a is not None and eps_e is not None) else None,
            "revenue_estimated": _num(r.get("revenueEstimated")), "revenue_actual": _num(r.get("revenueActual")),
        })
        if len(events) >= limit:
            break
    return {"symbol": sym, "source": "FMP", "events": events}
