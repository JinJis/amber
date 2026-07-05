"""PH-DATA-4: economic-indicators DB via DBnomics (keyless, cloud-safe).

Provenance workflow for this source:
  · FETCH   — DBnomics series API (no key, no datacenter bot-wall, unlike FRED).
  · PROCESS — typed observations {date, value} per indicator.
  · STORE   — on-demand (time series; no store).
  · SHOW    — data card: the values + source "DBnomics" + `as_of` + a canonical
              `db.nomics.world/{provider}/{dataset}/{series}` page link (show the real source).

Each series id below was verified live against the DBnomics API (returns data).
"""

from __future__ import annotations

import asyncio
import datetime

from app.http import fetch_json
from app.providers.us import bls as bls_api

# An observation older than this is clearly abnormal for these (monthly/quarterly) series — a
# frozen upstream, not a real release lag. We FLAG it (honesty: never present a year-old value as
# current) so the card/agent can show staleness. The DBnomics BLS mirror froze ~17mo at 2025-01.
_STALE_AFTER_DAYS = 270

# slug → {name, series (DBnomics provider/dataset/series), unit, region, group}
# `group` (하위요인) buckets the catalog so it browses by theme: 물가/고용/성장/금리.
INDICATORS: dict[str, dict] = {
    # 물가 (inflation)
    "cpi": {"name": "US CPI (all items, SA)", "series": "BLS/cu/CUSR0000SA0", "unit": "index", "region": "US", "group": "물가"},
    "core_cpi": {"name": "US Core CPI (ex food & energy, SA)", "series": "BLS/cu/CUSR0000SA0L1E",
                 "unit": "index", "region": "US", "group": "물가"},
    "pce_price": {"name": "US PCE Price Index", "series": "BEA/NIPA-T20804/DPCERG-M", "unit": "index", "region": "US", "group": "물가"},
    # 반도체 생산자물가 (PPI) — DRAM 현물가의 무료 대용(프록시). 월간 생산자물가, 현물 스팟 아님.
    "semiconductor_ppi": {"name": "US 반도체 생산자물가(PPI) — DRAM 현물가 아님(월간 프록시)",
                          "series": "BLS/pc/PCU334413334413", "unit": "index", "region": "US", "group": "물가"},
    "euro_cpi": {"name": "Euro Area HICP (YoY)", "series": "Eurostat/prc_hicp_manr/M.RCH_A.CP00.EA",
                 "unit": "%", "region": "EA", "group": "물가"},
    # 고용 (labor)
    "unemployment": {"name": "US Unemployment Rate", "series": "BLS/ln/LNS14000000", "unit": "%", "region": "US", "group": "고용"},
    "nonfarm_payrolls": {"name": "US Nonfarm Payrolls", "series": "BLS/ce/CES0000000001",
                         "unit": "thousands", "region": "US", "group": "고용"},
    "labor_participation": {"name": "US Labor Force Participation Rate", "series": "BLS/ln/LNS11300000",
                            "unit": "%", "region": "US", "group": "고용"},
    # 성장 (growth)
    "gdp_growth": {"name": "US Real GDP Growth (QoQ, annualized)", "series": "BEA/NIPA-T10101/A191RL-Q",
                   "unit": "%", "region": "US", "group": "성장"},
    "industrial_production": {"name": "US Industrial Production Index", "series": "FED/G17/IP_B50001_S",
                             "unit": "index", "region": "US", "group": "성장"},
    # 금리 (rates) — Fed H.15 constant-maturity Treasuries via DBnomics (fresh, monthly). The
    # prior OECD/KEI series froze at 2024-01 (surfaced by the staleness flag).
    "treasury_10y": {"name": "US 10Y Treasury Yield", "series": "FED/H15/RIFLGFCY10_N.M",
                     "unit": "%", "region": "US", "group": "금리"},
    "treasury_3m": {"name": "US 3M Treasury Yield", "series": "FED/H15/RIFLGFCM03_N.M",
                    "unit": "%", "region": "US", "group": "금리"},
    # 한국 (DATA-KR-1) — native Bank of Korea ECOS ("ecos:{stat}/{cycle}/{item}"). Authoritative
    # KR macro so the KR fact-check / macro panel isn't limited to the base rate. Verified live.
    "kr_cpi": {"name": "한국 소비자물가지수 (CPI, 2020=100)", "series": "ecos:901Y009/M/0",
               "unit": "index", "region": "KR", "group": "물가"},
    "kr_ppi": {"name": "한국 생산자물가지수 (PPI, 2020=100)", "series": "ecos:404Y014/M/*AA",
               "unit": "index", "region": "KR", "group": "물가"},
    "kr_unemployment": {"name": "한국 실업률 (원계열)", "series": "ecos:901Y027/M/I61BC",
                        "unit": "%", "region": "KR", "group": "고용"},
    "kr_gdp": {"name": "한국 실질 국내총생산 (계절조정, 십억원)", "series": "ecos:200Y108/Q/10601",
               "unit": "십억원", "region": "KR", "group": "성장"},
    "kr_usdkrw": {"name": "원/미국달러 환율 (매매기준율)", "series": "ecos:731Y001/D/0000001",
                  "unit": "원", "region": "KR", "group": "금리"},
}


def _is_ecos(series: str) -> bool:
    return series.startswith("ecos:")


def _ecos_spec(series: str) -> tuple[str, str, str]:
    """"ecos:901Y009/M/0" → (stat, cycle, item)."""
    stat, cycle, item = series[len("ecos:"):].split("/", 2)
    return stat, cycle, item


def _ecos_page(series: str) -> str:
    stat, _c, _i = _ecos_spec(series)
    return f"https://ecos.bok.or.kr/#/SearchStat?statCode={stat}"


def _source_url(series: str) -> str:
    return f"https://db.nomics.world/{series}"


def _is_bls(series: str) -> bool:
    return (series or "").startswith("BLS/")


def _bls_id(series: str) -> str:
    """`BLS/ln/LNS14000000` → `LNS14000000` (the BLS public-API series id)."""
    return series.split("/")[-1]


def _days_old(date_str: str) -> int | None:
    """Approximate age in days of a 'YYYY-MM[-DD]' / 'YYYY-Qx' / 'YYYY' observation date."""
    if not date_str:
        return None
    try:
        parts = str(date_str).split("-")
        y = int(parts[0])
        if len(parts) >= 2 and parts[1].upper().startswith("Q"):
            m = (int(parts[1][1]) - 1) * 3 + 1
        elif len(parts) >= 2:
            m = int(parts[1])
        else:
            m = 12  # annual → treat as year-end
        d = int(parts[2]) if len(parts) >= 3 else 1
        return (datetime.date.today() - datetime.date(y, m, min(d, 28))).days
    except Exception:  # noqa: BLE001 — unparseable date → unknown age
        return None


def _row(slug: str, obs: list[dict]) -> dict | None:
    """Assemble a panel row (latest + prior + change + freshness) from ascending observations."""
    if not obs:
        return None
    meta = INDICATORS[slug]
    latest = obs[-1]
    prior = obs[-2] if len(obs) > 1 else None
    days_old = _days_old(latest["date"])
    return {
        "slug": slug, "name": meta["name"], "unit": meta.get("unit"), "group": meta.get("group"),
        "latest": latest["value"], "as_of": latest["date"],
        "prior": prior["value"] if prior else None,
        "change": (latest["value"] - prior["value"]) if prior else None,
        "days_old": days_old,
        "stale": days_old is not None and days_old > _STALE_AFTER_DAYS,
        "source": ("BLS" if _is_bls(meta["series"]) else
                   "Bank of Korea ECOS" if _is_ecos(meta["series"]) else "DBnomics"),
        "source_url": (bls_api.series_page(_bls_id(meta["series"])) if _is_bls(meta["series"]) else
                       _ecos_page(meta["series"]) if _is_ecos(meta["series"]) else
                       _source_url(meta["series"])),
    }


def list_indicators(region: str | None = None, group: str | None = None) -> list[dict]:
    """Browse the indicator catalog, optionally filtered by region (국가) or group (하위요인)."""
    out = []
    for k, v in INDICATORS.items():
        if region and v["region"] != region.upper():
            continue
        if group and v.get("group") != group:
            continue
        out.append({"slug": k, "name": v["name"], "unit": v["unit"], "region": v["region"], "group": v.get("group")})
    return out


def list_regions() -> list[str]:
    return sorted({v["region"] for v in INDICATORS.values()})


async def region_panel(region: str = "US", limit: int = 2) -> dict:
    """국가경제 panel: latest + prior + change + freshness for each indicator in a region.

    BLS series (frozen on DBnomics) are fetched fresh from the BLS API in ONE batched request
    (rate-limit friendly); the rest come from DBnomics concurrently. Best-effort — an indicator
    whose series fails upstream is dropped, never faked; a stale-but-present value is flagged."""
    region = (region or "US").upper()
    slugs = [k for k, v in INDICATORS.items() if v["region"] == region]
    bls_slugs = [s for s in slugs if _is_bls(INDICATORS[s]["series"])]
    ecos_slugs = [s for s in slugs if _is_ecos(INDICATORS[s]["series"])]
    dbn_slugs = [s for s in slugs if not _is_bls(INDICATORS[s]["series"]) and not _is_ecos(INDICATORS[s]["series"])]

    async def _bls_obs() -> dict[str, list[dict]]:
        if not bls_slugs:
            return {}
        try:
            raw = await bls_api.fetch_bls([_bls_id(INDICATORS[s]["series"]) for s in bls_slugs])
        except Exception:  # noqa: BLE001 — whole BLS batch unavailable → those drop, never faked
            return {}
        return {s: raw.get(_bls_id(INDICATORS[s]["series"]), []) for s in bls_slugs}

    bls_map, dbn_fetched, ecos_fetched = await asyncio.gather(
        _bls_obs(),
        asyncio.gather(*[_dbnomics_obs(s, limit=max(2, limit)) for s in dbn_slugs]),
        asyncio.gather(*[_ecos_obs(s, limit=max(2, limit)) for s in ecos_slugs]),
    )
    obs_by_slug = {**bls_map, **dict(zip(dbn_slugs, dbn_fetched)), **dict(zip(ecos_slugs, ecos_fetched))}
    rows = [r for s in slugs if (r := _row(s, obs_by_slug.get(s) or []))]
    src = "BLS · DBnomics" if region == "US" else ("Bank of Korea ECOS" if region == "KR" else "DBnomics")
    return {"region": region, "source": src, "indicators": rows}


async def _ecos_obs(slug: str, limit: int = 24) -> list[dict]:
    """Ascending observations for an ECOS-backed KR slug ([] on failure/no key)."""
    from datetime import date

    from app.config import settings
    if not settings.ecos_api_key:
        return []
    stat, cycle, item = _ecos_spec(INDICATORS[slug]["series"])
    end = date.today()
    span = {"D": 400, "M": 3600, "Q": 3600, "A": 12000}.get(cycle, 3600)
    start = date(end.year - (2 if cycle == "D" else 12), 1, 1)

    def _fmt(d):
        if cycle == "D":
            return d.strftime("%Y%m%d")
        if cycle == "M":
            return d.strftime("%Y%m")
        if cycle == "Q":
            return f"{d.year}Q{(d.month - 1) // 3 + 1}"
        return d.strftime("%Y")

    def _iso(t):
        if cycle == "D" and len(t) == 8:
            return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
        if cycle == "M" and len(t) == 6:
            return f"{t[:4]}-{t[4:6]}-01"
        if cycle == "Q" and "Q" in t:
            y, q = t.split("Q")
            return f"{y}-{(int(q) - 1) * 3 + 1:02d}-01"
        return f"{t[:4]}-01-01"

    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{settings.ecos_api_key}/json/kr/1/{span}/"
           f"{stat}/{cycle}/{_fmt(start)}/{_fmt(end)}/{item}")
    try:
        data = await fetch_json("ecos", url)
    except Exception:  # noqa: BLE001 — graceful
        return []
    if not isinstance(data, dict) or "RESULT" in data:
        return []
    rows = (data.get("StatisticSearch") or {}).get("row") or []
    obs: list[dict] = []
    for r in rows:
        v = r.get("DATA_VALUE")
        if v in (None, "", "-"):
            continue
        try:
            obs.append({"date": _iso(r.get("TIME", "")), "value": float(v)})
        except (TypeError, ValueError):
            continue
    obs.sort(key=lambda o: o["date"])
    return obs[-limit:]


async def _dbnomics_obs(slug: str, limit: int = 24) -> list[dict]:
    """Ascending observations for a DBnomics-backed slug ([] on failure)."""
    series = INDICATORS[slug]["series"]
    try:
        data = await fetch_json("dbnomics", f"https://api.db.nomics.world/v22/series/{series}",
                                params={"observations": "1"})
    except Exception:  # noqa: BLE001 — upstream/network → graceful ([])
        return []
    docs = (data.get("series") or {}).get("docs") or [] if isinstance(data, dict) else []
    if not docs:
        return []
    doc = docs[0]
    obs: list[dict] = []
    for period, value in zip(doc.get("period") or [], doc.get("value") or []):
        try:
            obs.append({"date": period, "value": float(value)})
        except (TypeError, ValueError):
            continue  # "NA" / missing → dropped, never faked
    return obs[-limit:]


async def fetch_indicator(slug: str, limit: int = 24) -> dict | None:
    """One indicator's recent observations + source link + freshness. None if unknown/failed.

    BLS series read from the BLS public API (DBnomics' BLS mirror froze at 2025-01); everything
    else from keyless DBnomics. The latest value carries `as_of` + `stale` so a frozen upstream is
    surfaced, never shown as if current."""
    slug = (slug or "").strip().lower()
    meta = INDICATORS.get(slug)
    if not meta:
        return None
    series = meta["series"]
    if _is_bls(series):
        try:
            raw = await bls_api.fetch_bls([_bls_id(series)])
        except Exception:  # noqa: BLE001 — graceful (None)
            return None
        obs = (raw.get(_bls_id(series)) or [])[-limit:]
        source, source_url = "BLS", bls_api.series_page(_bls_id(series))
    elif _is_ecos(series):
        obs = await _ecos_obs(slug, limit=limit)
        source, source_url = "Bank of Korea ECOS", _ecos_page(series)
    else:
        obs = await _dbnomics_obs(slug, limit=limit)
        source, source_url = "DBnomics", _source_url(series)
    if not obs:
        return None
    latest = obs[-1]
    days_old = _days_old(latest["date"])
    return {"slug": slug, "name": meta["name"], "unit": meta.get("unit"),
            "region": meta.get("region"), "source": source, "source_url": source_url,
            "as_of": latest["date"], "days_old": days_old,
            "stale": days_old is not None and days_old > _STALE_AFTER_DAYS,
            "observations": obs}
