"""US Bureau of Labor Statistics — public timeseries API (keyless, cloud-safe, FRESH).

Why this exists: we read US labor/price indicators (unemployment, nonfarm payrolls, CPI, PPI,
participation) from DBnomics, but DBnomics' **BLS mirror froze at 2025-01** — it served ~17-month
-old values as if current. The BLS public API itself is current (verified: LNS14000000 → 2026-05)
and reachable from datacenter IPs (unlike FRED's bot-wall), so we fetch those series here directly.

Provenance workflow:
  · FETCH   — POST api.bls.gov/publicAPI/v2/timeseries/data (batches up to 25 series keyless /
              50 with a free BLS_API_KEY → one request per panel).
  · PROCESS — typed {date "YYYY-MM", value} observations, ascending (latest last).
  · SHOW    — source "BLS" + the real series page data.bls.gov/timeseries/{id}.
"""

from __future__ import annotations

import datetime

from app.config import settings
from app.errors import upstream_error
from app.http import get_client

_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# month code (M01..M12) → calendar month; M13 is the annual average (skipped — not a point in time).
_MONTHS = {f"M{m:02d}": m for m in range(1, 13)}


def series_page(series_id: str) -> str:
    """The canonical, user-facing BLS page for a series (show the real source)."""
    return f"https://data.bls.gov/timeseries/{series_id}"


async def fetch_bls(series_ids: list[str], *, years: int = 3) -> dict[str, list[dict]]:
    """Fetch recent monthly observations for one or more BLS series in a SINGLE request.

    Returns ``{series_id: [{"date": "YYYY-MM", "value": float}, …ascending]}`` — missing/non-numeric
    points are dropped (never faked). Raises ``upstream_error`` on transport/HTTP failure so callers
    can degrade gracefully (the macro panel drops a failed indicator rather than fabricating it)."""
    ids = [s for s in series_ids if s]
    if not ids:
        return {}
    end = datetime.date.today().year
    payload: dict = {"seriesid": ids, "startyear": str(end - max(1, years)), "endyear": str(end)}
    if settings.bls_api_key:
        payload["registrationkey"] = settings.bls_api_key
    try:
        resp = await get_client().post(_API, json=payload, headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — normalize to our upstream error
        raise upstream_error("bls", str(exc))
    if not isinstance(data, dict) or data.get("status") != "REQUEST_SUCCEEDED":
        raise upstream_error("bls", f"BLS status={data.get('status') if isinstance(data, dict) else 'n/a'}")

    out: dict[str, list[dict]] = {}
    for s in (data.get("Results") or {}).get("series") or []:
        sid = s.get("seriesID")
        obs: list[dict] = []
        for row in s.get("data") or []:
            month = _MONTHS.get(row.get("period") or "")
            if not month:
                continue  # annual avg (M13) / quarterly / non-monthly → skip
            try:
                obs.append({"date": f"{int(row['year']):04d}-{month:02d}", "value": float(row["value"])})
            except (TypeError, ValueError, KeyError):
                continue  # footnoted / missing value → dropped, never faked
        obs.sort(key=lambda o: o["date"])  # BLS returns newest-first → make ascending (latest last)
        if sid:
            out[sid] = obs
    return out
