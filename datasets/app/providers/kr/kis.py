"""CE-12: Korea Investment & Securities (KIS) — KR realtime rankings + investor flows.

The KR half competitors can't easily match: live volume rankings (활발 종목) and per-stock
investor flows (개인/외국인/기관 순매수 = 수급). OAuth token (app key/secret → 24h access token,
cached + rate-limit-aware; issuance is ~1/min). Keys stay server-side (KIS_APP_KEY/SECRET).
Descriptive market data — no advice/forecast.
"""

import httpx

from app.cache import TTLCache
from app.config import settings
from app.errors import bad_request, upstream_error
from app.providers._parse_utils import parse_float as _f, parse_int as _i  # shared parsers (RF-02)
from app.http import fetch_json

# KIS tokens last ~24h; cache for 23h (less a 60s margin). The shared TTLCache is single-flight per
# key, so concurrent callers issue exactly one token request — KIS limits issuance to ~1/min (RF-06).
_token_cache = TTLCache(23 * 3600 - 60)


def _creds() -> tuple[str, str]:
    if not (settings.kis_app_key and settings.kis_app_secret):
        raise bad_request("KIS_APP_KEY / KIS_APP_SECRET are not configured.")
    return settings.kis_app_key, settings.kis_app_secret


async def _token() -> str:
    async def _issue() -> str:
        app_key, app_secret = _creds()
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                resp = await client.post(
                    f"{settings.kis_domain}/oauth2/tokenP",
                    json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret})
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise upstream_error("kis", f"token request failed: {exc}")
        token = data.get("access_token")
        if not token:
            raise upstream_error("kis", f"no access_token ({data.get('msg1') or data})")
        return token

    return await _token_cache.get_or_set("kis:token", _issue)


async def _get(path: str, tr_id: str, params: dict, output_key: str = "output") -> list:
    app_key, app_secret = _creds()
    headers = {"authorization": f"Bearer {await _token()}", "appkey": app_key,
               "appsecret": app_secret, "tr_id": tr_id, "custtype": "P"}
    data = await fetch_json("kis", f"{settings.kis_domain}{path}", params=params, headers=headers)
    if isinstance(data, dict) and data.get("rt_cd") is not None and str(data.get("rt_cd")) != "0":
        raise upstream_error("kis", str(data.get("msg1") or "KIS error")[:160])
    out = (data.get(output_key) if isinstance(data, dict) else None) or []
    return out if isinstance(out, list) else [out]


async def volume_rank(limit: int = 30) -> dict:
    """거래량 순위 (most-active KR stocks) — the KR 'movers' view (FMP movers are premium-gated)."""
    rows = await _get(
        "/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
         "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
         "FID_TRGT_EXLS_CLS_CODE": "0000000000", "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
         "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})
    out = []
    for r in rows[:limit]:
        out.append({"rank": _i(r.get("data_rank")), "ticker": r.get("mksc_shrn_iscd"),
                    "name": r.get("hts_kor_isnm"), "price": _i(r.get("stck_prpr")),
                    "change_percent": _f(r.get("prdy_ctrt")), "volume": _i(r.get("acml_vol")),
                    "value": _i(r.get("acml_tr_pbmn"))})
    return {"market": "KR", "source": "한국투자증권 (KIS)", "ranking": "volume", "results": out}


async def fluctuation_rank(direction: str = "up", limit: int = 30) -> dict:
    """등락률 순위 — 상승률(up=gainers) / 하락률(down=losers) top KR stocks."""
    sort = "1" if str(direction).lower() in ("down", "losers", "하락") else "0"
    rows = await _get(
        "/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20170", "FID_INPUT_ISCD": "0000",
         "FID_RANK_SORT_CLS_CODE": sort, "FID_INPUT_CNT_1": "0", "FID_PRC_CLS_CODE": "0",
         "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_TRGT_CLS_CODE": "0",
         "FID_TRGT_EXLS_CLS_CODE": "0", "FID_DIV_CLS_CODE": "0", "FID_RSFL_RATE1": "", "FID_RSFL_RATE2": ""})
    out = []
    for r in rows[:limit]:
        out.append({"rank": _i(r.get("data_rank")), "ticker": r.get("stck_shrn_iscd"),
                    "name": r.get("hts_kor_isnm"), "price": _i(r.get("stck_prpr")),
                    "change_percent": _f(r.get("prdy_ctrt")), "volume": _i(r.get("acml_vol"))})
    return {"market": "KR", "source": "한국투자증권 (KIS)", "ranking": "fluctuation",
            "direction": "down" if sort == "1" else "up", "results": out}


async def market_cap_rank(limit: int = 30) -> dict:
    """시가총액 순위 — largest KR companies by market cap (억원) + 시장 비중."""
    rows = await _get(
        "/uapi/domestic-stock/v1/ranking/market-cap", "FHPST01740000",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20174", "FID_INPUT_ISCD": "0000",
         "FID_DIV_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
         "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": ""})
    out = []
    for r in rows[:limit]:
        out.append({"rank": _i(r.get("data_rank")), "ticker": r.get("mksc_shrn_iscd"),
                    "name": r.get("hts_kor_isnm"), "price": _i(r.get("stck_prpr")),
                    "change_percent": _f(r.get("prdy_ctrt")),
                    "market_cap_eok": _i(r.get("stck_avls")),  # 시가총액 (억원)
                    "market_weight_pct": _f(r.get("mrkt_whol_avls_rlim"))})
    return {"market": "KR", "source": "한국투자증권 (KIS)", "ranking": "market_cap", "results": out}


async def etf_nav(ticker: str) -> dict:
    """ETF 현재가 vs NAV + 괴리율(premium/discount) — is the ETF trading rich/cheap to its NAV?"""
    rows = await _get("/uapi/etfetn/v1/quotations/inquire-price", "FHPST02400000",
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker})
    r = rows[0] if rows else {}
    return {"market": "KR", "ticker": ticker, "source": "한국투자증권 (KIS)",
            "name": r.get("hts_kor_isnm"), "price": _i(r.get("stck_prpr")), "nav": _f(r.get("nav")),
            "premium_discount_pct": _f(r.get("dprt")), "price_change_percent": _f(r.get("prdy_ctrt")),
            "nav_change_percent": _f(r.get("nav_prdy_ctrt"))}


# --- KIS-PRICES: realtime KR prices provider (drop-in for the Yahoo KR provider) ----------
from datetime import date, datetime, timedelta, timezone  # noqa: E402

from app.models.generated import Price, PriceSnapshot  # noqa: E402
from app.symbols import SecurityRef  # noqa: E402

_PERIOD = {"day": "D", "week": "W", "month": "M", "year": "Y"}


class KisPricesProvider:
    """Realtime/intraday KR prices via KIS — a drop-in PricesProvider (snapshot + OHLCV) so the
    whole app (charts, snapshots, backtest, portfolio) uses live broker data when
    ``PRICES_PROVIDER_KR=kis``. Daily-chart calls cap ~100 bars, so history is paginated back."""

    async def snapshot(self, ref: SecurityRef) -> PriceSnapshot:
        rows = await _get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                          {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ref.ticker})
        r = rows[0] if rows else {}
        return PriceSnapshot(ticker=ref.ticker, price=_f(r.get("stck_prpr")),
                             day_change=_f(r.get("prdy_vrss")), day_change_percent=_f(r.get("prdy_ctrt")),
                             time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))

    async def prices(self, ref: SecurityRef, interval: str, start: date, end: date) -> list[Price]:
        period = _PERIOD.get(interval, "D")
        bars: dict[str, Price] = {}
        cur_end = end
        for _ in range(40):  # paginate ~100-bar windows back to `start` (bounded)
            rows = await _get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ref.ticker,
                 "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": cur_end.strftime("%Y%m%d"),
                 "FID_PERIOD_DIV_CODE": period, "FID_ORG_ADJ_PRC": "0"}, output_key="output2")
            rows = [r for r in rows if r.get("stck_bsop_date")]
            if not rows:
                break
            oldest = min(r["stck_bsop_date"] for r in rows)
            for r in rows:
                d = r["stck_bsop_date"]
                bars[d] = Price(time=f"{d[:4]}-{d[4:6]}-{d[6:8]}", open=_f(r.get("stck_oprc")),
                                high=_f(r.get("stck_hgpr")), low=_f(r.get("stck_lwpr")),
                                close=_f(r.get("stck_clpr")), volume=_i(r.get("acml_vol")))
            if oldest <= start.strftime("%Y%m%d") or len(rows) < 2:
                break
            cur_end = datetime.strptime(oldest, "%Y%m%d").date() - timedelta(days=1)
            if cur_end < start:
                break
        return [bars[k] for k in sorted(bars)]


async def investor_flow(ticker: str, limit: int = 10) -> dict:
    """투자자별 순매수 (개인/외국인/기관) for a KR stock — recent days; KR 수급 differentiator."""
    rows = await _get(
        "/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker})
    out = []
    for r in rows[:limit]:
        out.append({"date": r.get("stck_bsop_date"), "close": _i(r.get("stck_clpr")),
                    "individual_net": _i(r.get("prsn_ntby_qty")), "foreign_net": _i(r.get("frgn_ntby_qty")),
                    "institution_net": _i(r.get("orgn_ntby_qty"))})
    return {"market": "KR", "ticker": ticker, "source": "한국투자증권 (KIS)", "flows": out}
