"""HL-5 — GDELT era-news provider: coverage clamp (2017 floor, gaps drawn), timeline/artlist parse."""

from __future__ import annotations

from datetime import date

import httpx
import respx

from app.providers.us.gdelt import GdeltProvider, _clamp_note, _GDELT_START

GD = "https://api.gdeltproject.org/api/v2/doc/doc"


def test_clamp_note_draws_the_pre_2017_gap():
    # a window entirely before GDELT's index → note only, no fabricated series
    assert "2017" in (_clamp_note(date(2008, 9, 1), date(2009, 3, 1)) or "")
    # a window straddling the floor → a partial-coverage note
    assert _clamp_note(date(2016, 6, 1), date(2018, 1, 1)) is not None
    # a fully-covered window → no note
    assert _clamp_note(date(2023, 1, 1), date(2023, 6, 1)) is None
    assert _GDELT_START == date(2017, 1, 1)


@respx.mock
async def test_timeline_skips_upstream_for_pre_2017_and_returns_empty_series():
    # end < 2017 → we must NOT call GDELT at all (guaranteed miss); empty series + note
    route = respx.get(GD).mock(return_value=httpx.Response(200, json={}))
    out = await GdeltProvider().news_timeline("crash", date(2008, 1, 1), date(2008, 12, 31))
    assert out["series"] == [] and out["coverage_note"] and route.call_count == 0
    assert out["method"] == "gdelt-timeline-v1" and out["source"] == "GDELT DOC 2.0"


@respx.mock
async def test_timeline_parses_volume_and_tone():
    payload = {"timeline": [
        {"series": "Volume Intensity", "data": [
            {"date": "20230102", "value": 3.1}, {"date": "20230103", "value": 4.2}]},
        {"series": "Average Tone", "data": [
            {"date": "20230102", "value": -1.5}, {"date": "20230103", "value": -2.0}]},
    ]}
    respx.get(GD).mock(return_value=httpx.Response(200, json=payload))
    out = await GdeltProvider().news_timeline("bank run", date(2023, 1, 1), date(2023, 1, 5))
    assert [p["date"] for p in out["series"]] == ["2023-01-02", "2023-01-03"]
    assert out["series"][0]["volume"] == 3.1 and out["series"][0]["tone"] == -1.5
    assert out["coverage_note"] is None


@respx.mock
async def test_search_parses_articles_and_iso_dates():
    payload = {"articles": [
        {"title": "SVB collapses", "url": "https://x/1", "domain": "nyt.com",
         "seendate": "20230310T120000Z", "language": "English", "tone": -3.2}]}
    respx.get(GD).mock(return_value=httpx.Response(200, json=payload))
    out = await GdeltProvider().news_search("Silicon Valley Bank", date(2023, 3, 1), date(2023, 3, 31), 10)
    a = out["articles"][0]
    assert a["title"] == "SVB collapses" and a["date"] == "2023-03-10" and a["domain"] == "nyt.com"
    assert out["method"] == "gdelt-artlist-v1"


def test_gdelt_connector_in_catalog_and_categorized():
    from app.connectors.catalog import get_connector

    c = get_connector("gdelt")
    assert c is not None and c.upstream.requires_key is False
    names = {r.name for r in c.resources}
    assert names == {"news_timeline", "news_search"}
    assert all(r.category == "history" for r in c.resources)  # load fails if uncategorized
    assert all(r.path.startswith("/era-news/") for r in c.resources)


# --- NYT Archive: key gate + rate limiter + window filter ------------------------------
def test_nyt_rate_limiter_spaces_calls():
    import asyncio

    from app.providers.us.nyt_archive import _RateLimiter

    lim = _RateLimiter(rate=5, per=60.0)  # → 12s min interval
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    async def run():
        await lim.acquire(now=100.0, sleep=fake_sleep)   # first call: no wait
        await lim.acquire(now=100.0, sleep=fake_sleep)   # immediately after: must wait ~12s
        await lim.acquire(now=200.0, sleep=fake_sleep)   # 100s later: no wait

    asyncio.run(run())
    assert slept and abs(slept[0] - 12.0) < 0.01 and len(slept) == 1


def test_nyt_months_span():
    from app.providers.us.nyt_archive import _months

    assert _months(date(2008, 11, 1), date(2009, 2, 15)) == [(2008, 11), (2008, 12), (2009, 1), (2009, 2)]
    assert _months(date(2020, 3, 1), date(2020, 3, 31)) == [(2020, 3)]


async def test_nyt_era_news_501_without_key(monkeypatch):
    from app.errors import APIError
    from app.config import settings
    from app.providers.us.nyt_archive import NytArchiveProvider

    monkeypatch.setattr(settings, "nyt_api_key", "")
    try:
        await NytArchiveProvider().era_news("crash", date(2008, 9, 1), date(2008, 10, 1))
        assert False, "expected 501"
    except APIError as e:
        assert e.status_code == 501


@respx.mock
async def test_nyt_era_news_filters_to_window_and_query(monkeypatch):
    from app.config import settings
    from app.providers.us.nyt_archive import NytArchiveProvider, _RateLimiter

    monkeypatch.setattr(settings, "nyt_api_key", "k")
    docs = {"response": {"docs": [
        {"headline": {"main": "Lehman collapses"}, "abstract": "bankruptcy filing",
         "pub_date": "2008-09-15T00:00:00Z", "web_url": "https://nyt/1", "section_name": "Business"},
        {"headline": {"main": "Unrelated sports piece"}, "abstract": "a game",
         "pub_date": "2008-09-16T00:00:00Z", "web_url": "https://nyt/2"},
        {"headline": {"main": "Out of window"}, "abstract": "crash later",
         "pub_date": "2008-10-20T00:00:00Z", "web_url": "https://nyt/3"},
    ]}}
    respx.get(url__regex=r"https://api\.nytimes\.com/svc/archive/v1/2008/9\.json").mock(
        return_value=httpx.Response(200, json=docs))
    # no real sleeping in the test limiter
    fast = _RateLimiter(rate=1000, per=0.0)
    out = await NytArchiveProvider().era_news("lehman bankruptcy", date(2008, 9, 1), date(2008, 9, 30),
                                              limiter=fast)
    assert [a["url"] for a in out["articles"]] == ["https://nyt/1"]  # query + window filter
    assert out["method"] == "nyt-archive-v1"


def test_nyt_connector_key_gated_in_catalog():
    from app.connectors.catalog import get_connector

    c = get_connector("nyt_archive")
    assert c is not None and c.upstream.requires_key is True and c.upstream.key_env == "NYT_API_KEY"
    assert {r.name for r in c.resources} == {"era_news"}
    assert all(r.category == "history" for r in c.resources)


# --- 8-K fix: filings listing carries item summary + events reach RAG refs ------------
def test_filing_summary_labels_8k_items():
    from app.providers.us.sec_edgar import _filing_summary

    assert _filing_summary("8-K", "5.02,9.01", "8-K") == "항목 5.02 임원·이사 변동 · 항목 9.01 재무제표·첨부자료"
    # unknown code → keeps the bare item, never dropped
    assert "항목 3.99" in _filing_summary("8-K", "3.99", "")
    # non-8-K → the primary-document description when it's not just the form
    assert _filing_summary("10-K", "", "Annual report") == "Annual report"
    # nothing descriptive → None (caller falls back to the form)
    assert _filing_summary("10-K", "", "10-K") is None


async def test_filing_refs_includes_recent_8k_events(monkeypatch):
    import app.store.filing_refs as FR

    async def fake_cik(_ref):
        return "0000320193"

    async def fake_docmap(_cik):
        return {"0000320193-24-000100": "https://sec/aapl-8k.htm",
                "0000320193-24-000055": "https://sec/aapl-10k.htm"}

    submissions = {"filings": {"recent": {
        "form": ["8-K", "10-K"],
        "accessionNumber": ["0000320193-24-000100", "0000320193-24-000055"]}}}

    async def fake_sub(_cik):
        return submissions

    # statements provider returns no accessions → without the event path, out would be empty
    class _Prov:
        async def income_statements(self, *a):
            return []
        balance_sheets = cash_flow_statements = income_statements

    monkeypatch.setattr(FR, "_resolve_cik", fake_cik)
    monkeypatch.setattr(FR, "_primary_doc_map", fake_docmap)
    monkeypatch.setattr(FR, "_submissions", fake_sub)
    monkeypatch.setattr(FR, "get_financials_provider", lambda _m: _Prov())

    refs = await FR.filing_refs("US", "AAPL", 4)
    assert "0000320193-24-000100" in refs  # the 8-K is now indexable
    assert refs["0000320193-24-000100"]["fetch_url"] == "https://sec/aapl-8k.htm"
    # include_events=False keeps the old statements-only behavior
    refs_off = await FR.filing_refs("US", "AAPL", 4, include_events=False)
    assert "0000320193-24-000100" not in refs_off
