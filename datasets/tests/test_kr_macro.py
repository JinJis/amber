"""DATA-KR-1 — KR macro via ECOS: slug catalog, series-scheme parsing, panel/fetch routing."""

from __future__ import annotations

import httpx
import respx

from app.providers import macro_indicators as M


def test_kr_indicators_registered_and_ecos_scheme():
    kr = {k: v for k, v in M.INDICATORS.items() if v["region"] == "KR"}
    assert {"kr_cpi", "kr_ppi", "kr_unemployment", "kr_gdp", "kr_usdkrw"} <= set(kr)
    assert all(v["series"].startswith("ecos:") for v in kr.values())
    # scheme parse: "ecos:901Y009/M/0" → (stat, cycle, item)
    assert M._ecos_spec("ecos:901Y009/M/0") == ("901Y009", "M", "0")
    assert M._is_ecos("ecos:x/M/0") and not M._is_ecos("BLS/cu/x")
    assert "statCode=901Y009" in M._ecos_page("ecos:901Y009/M/0")
    assert "KR" in M.list_regions()


@respx.mock
async def test_ecos_obs_parses_and_sorts_ascending(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ecos_api_key", "k")
    respx.get(url__regex=r"https://ecos\.bok\.or\.kr/api/StatisticSearch/.*").mock(
        return_value=httpx.Response(200, json={"StatisticSearch": {"row": [
            {"TIME": "202503", "DATA_VALUE": "116.10", "UNIT_NAME": "2020=100"},
            {"TIME": "202504", "DATA_VALUE": "116.38", "UNIT_NAME": "2020=100"},
            {"TIME": "202502", "DATA_VALUE": "115.90", "UNIT_NAME": "2020=100"},
            {"TIME": "202505", "DATA_VALUE": "-", "UNIT_NAME": "2020=100"},   # missing → dropped
        ]}}))
    obs = await M._ecos_obs("kr_cpi", limit=24)
    assert [o["date"] for o in obs] == ["2025-02-01", "2025-03-01", "2025-04-01"]  # sorted, '-' dropped
    assert obs[-1]["value"] == 116.38


@respx.mock
async def test_ecos_quarter_iso_and_no_key_degrades(monkeypatch):
    from app.config import settings
    # no key → empty (never fabricate), and the panel still returns for the region
    monkeypatch.setattr(settings, "ecos_api_key", "")
    assert await M._ecos_obs("kr_gdp", limit=8) == []

    monkeypatch.setattr(settings, "ecos_api_key", "k")
    respx.get(url__regex=r".*StatisticSearch.*").mock(return_value=httpx.Response(200, json={
        "StatisticSearch": {"row": [{"TIME": "2024Q4", "DATA_VALUE": "575977"}]}}))
    obs = await M._ecos_obs("kr_gdp", limit=4)
    assert obs[0]["date"] == "2024-10-01"    # 2024Q4 → 2024-10-01


@respx.mock
async def test_kr_fetch_indicator_labels_ecos_source(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "ecos_api_key", "k")
    respx.get(url__regex=r".*StatisticSearch.*").mock(return_value=httpx.Response(200, json={
        "StatisticSearch": {"row": [{"TIME": "202504", "DATA_VALUE": "3.1"}]}}))
    res = await M.fetch_indicator("kr_unemployment")
    assert res["source"] == "Bank of Korea ECOS" and res["region"] == "KR"
    assert res["as_of"] == "2025-04-01" and res["observations"][-1]["value"] == 3.1
    assert "ecos.bok.or.kr" in res["source_url"]
