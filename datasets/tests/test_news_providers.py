"""Unit tests for the real-time news providers + the market-routed AutoNewsProvider.

Covers:
  * FinnhubNewsProvider (US, keyed) — company-news → News, newest-first, guard/empty paths.
  * NaverNewsProvider (KR, keyed) — search → News, <b>/entity cleaning, url + publisher, guards.
  * AutoNewsProvider — keyed-source routing (US=Finnhub, KR=Naver), Google-News fallback, the
    2-min dedup cache (one upstream call per key within the window), never-raises contract.
  * configured() gates for both keyed providers (every missing-credential combination).

All upstream HTTP is mocked (respx); the OpenDART corp map is monkeypatched away. No network, no key.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.models.generated import News
from app.providers import news as AUTO
from app.providers.kr import naver_news as NV
from app.providers.us import finnhub_news as FN
from app.symbols import Market


@pytest.fixture(autouse=True)
def _clear_news_cache():
    """AutoNewsProvider dedups through the module-global cache — clear it so the ~2-min TTL
    doesn't leak a prior test's result into the next (identical market:ticker:limit key)."""
    from app.cache import cache

    cache.clear()
    yield
    cache.clear()


# --- Finnhub (US) --------------------------------------------------------------
_FINNHUB_SAMPLE = [
    # deliberately NOT sorted (older first) so we prove the provider sorts newest-first
    {"headline": "Apple older story", "url": "https://ex.com/old", "source": "Reuters",
     "datetime": 1749300000},
    {"headline": "Apple newer story", "url": "https://ex.com/new", "source": "Bloomberg",
     "datetime": 1749500000},
    # missing url → dropped even though it has the largest datetime (would sort first)
    {"headline": "no url story", "source": "X", "datetime": 1749600000},
]


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_maps_articles_newest_first(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    route = respx.get(FN._URL).mock(return_value=httpx.Response(200, json=_FINNHUB_SAMPLE))

    out = await FN.FinnhubNewsProvider().news(Market.US, "aapl", 5)

    assert route.called
    assert route.calls.last.request.url.params["symbol"] == "AAPL"      # upper-cased + windowed
    assert route.calls.last.request.url.params["token"] == "k"
    # the url-less article is filtered; remaining two are newest-first
    assert [n.title for n in out] == ["Apple newer story", "Apple older story"]
    top = out[0]
    assert top.ticker == "AAPL" and top.source == "Bloomberg"
    assert str(top.url) == "https://ex.com/new"
    assert str(top.date) == FN._epoch_to_date(1749500000)              # epoch → ISO date


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_respects_limit(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    respx.get(FN._URL).mock(return_value=httpx.Response(200, json=_FINNHUB_SAMPLE))
    out = await FN.FinnhubNewsProvider().news(Market.US, "AAPL", 1)
    assert [n.title for n in out] == ["Apple newer story"]             # only the newest


@pytest.mark.asyncio
async def test_finnhub_empty_for_kr_no_ticker_no_key(monkeypatch):
    # KR market → not this endpoint's job (no network attempted)
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    assert await FN.FinnhubNewsProvider().news(Market.KR, "005930", 5) == []
    # no ticker → nothing to query
    assert await FN.FinnhubNewsProvider().news(Market.US, None, 5) == []
    # no key → let the caller fall back
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "")
    assert await FN.FinnhubNewsProvider().news(Market.US, "AAPL", 5) == []


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_empty_on_error_and_non_list(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    # non-200 (403 is not retried) → upstream error swallowed → []
    respx.get(FN._URL).mock(return_value=httpx.Response(403, json={"error": "forbidden"}))
    assert await FN.FinnhubNewsProvider().news(Market.US, "AAPL", 5) == []


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_empty_on_non_list_payload(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    respx.get(FN._URL).mock(return_value=httpx.Response(200, json={"unexpected": "object"}))
    assert await FN.FinnhubNewsProvider().news(Market.US, "AAPL", 5) == []


def test_finnhub_configured_gate(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")
    assert FN.configured() is True
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "")
    assert FN.configured() is False


def test_finnhub_epoch_to_date_helper():
    assert FN._epoch_to_date(0) is None            # falsy epoch → no date
    assert FN._epoch_to_date(None) is None
    assert FN._epoch_to_date("garbage") is None
    assert FN._epoch_to_date(1749500000) is not None


# --- Naver (KR) ----------------------------------------------------------------
_NAVER_SAMPLE = {"items": [
    {"title": "삼성<b>전자</b> &quot;신기록&quot; &amp; 성장",
     "originallink": "https://www.chosun.com/article/1",
     "link": "https://n.news.naver.com/article/001",
     "pubDate": "Mon, 09 Jun 2026 12:00:00 +0900",
     "description": "본문"},
    {"title": "두 번째 <b>기사</b>",
     # no originallink → url falls back to the naver link
     "link": "https://n.news.naver.com/article/002",
     "pubDate": "Tue, 10 Jun 2026 09:00:00 +0900",
     "description": "본문2"},
    {"title": "  <b></b>  ", "link": "https://x.test/3"},   # empty after cleaning → skipped
]}


@pytest.mark.asyncio
@respx.mock
async def test_naver_cleans_title_and_resolves_url(monkeypatch):
    monkeypatch.setattr(NV.settings, "naver_client_id", "id")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")

    async def _fake_query(ticker):    # no OpenDART corp-map network
        return "삼성전자"

    monkeypatch.setattr(NV, "_query_for", _fake_query)
    route = respx.get(NV._URL).mock(return_value=httpx.Response(200, json=_NAVER_SAMPLE))

    out = await NV.NaverNewsProvider().news(Market.KR, "005930", 5)

    assert route.called
    assert route.calls.last.request.headers["X-Naver-Client-Id"] == "id"
    assert route.calls.last.request.headers["X-Naver-Client-Secret"] == "sec"
    assert route.calls.last.request.url.params["query"] == "삼성전자"
    assert route.calls.last.request.url.params["sort"] == "date"

    # the empty-after-cleaning item is dropped → 2 articles
    assert len(out) == 2
    first = out[0]
    # <b> tags stripped + HTML entities unescaped
    assert "<b>" not in first.title and "</b>" not in first.title
    assert first.title == '삼성전자 "신기록" & 성장'
    assert "&amp;" not in first.title and "&quot;" not in first.title
    assert first.ticker == "005930"
    assert str(first.url) == "https://www.chosun.com/article/1"       # originallink preferred
    assert first.source == "chosun.com"                              # host, www. stripped
    assert str(first.date) == "2026-06-09"                           # RFC-822 pubDate → ISO

    # second item: no originallink → naver link fallback, host = publisher label
    second = out[1]
    assert str(second.url) == "https://n.news.naver.com/article/002"
    assert second.source == "n.news.naver.com"


@pytest.mark.asyncio
async def test_naver_empty_for_us_market(monkeypatch):
    monkeypatch.setattr(NV.settings, "naver_client_id", "id")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")
    assert await NV.NaverNewsProvider().news(Market.US, "AAPL", 5) == []   # KR-only


@pytest.mark.asyncio
async def test_naver_empty_when_not_configured(monkeypatch):
    # either credential missing → provider stays dark (no network)
    monkeypatch.setattr(NV.settings, "naver_client_id", "id")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "")
    assert await NV.NaverNewsProvider().news(Market.KR, "005930", 5) == []
    monkeypatch.setattr(NV.settings, "naver_client_id", "")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")
    assert await NV.NaverNewsProvider().news(Market.KR, "005930", 5) == []


def test_naver_configured_gate(monkeypatch):
    monkeypatch.setattr(NV.settings, "naver_client_id", "id")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")
    assert NV.configured() is True
    monkeypatch.setattr(NV.settings, "naver_client_secret", "")
    assert NV.configured() is False
    monkeypatch.setattr(NV.settings, "naver_client_id", "")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")
    assert NV.configured() is False
    monkeypatch.setattr(NV.settings, "naver_client_secret", "")
    assert NV.configured() is False


def test_naver_clean_and_publisher_helpers():
    assert NV._clean("<b>삼성</b>전자 &amp; SK") == "삼성전자 & SK"
    assert NV._clean("<b></b>") is None
    assert NV._clean(None) is None
    assert NV._publisher("https://www.hankyung.com/x") == "hankyung.com"
    assert NV._publisher(None) is None


# --- AutoNewsProvider routing + fallback + cache -------------------------------
def _sentinel(tag: str) -> list[News]:
    return [News(ticker=tag, title=tag, source=tag)]


@pytest.mark.asyncio
async def test_auto_us_uses_finnhub_when_configured(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")     # → _primary_provider = Finnhub

    google_called = []

    async def _finnhub_news(self, market, ticker, limit):
        return _sentinel("FINNHUB")

    async def _google_news(self, market, ticker, limit):
        google_called.append(True)
        return _sentinel("GOOGLE")

    monkeypatch.setattr(FN.FinnhubNewsProvider, "news", _finnhub_news)
    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    out = await AUTO.AutoNewsProvider().news(Market.US, "AAPL", 5)
    assert out[0].ticker == "FINNHUB"
    assert google_called == []                                  # keyed source served it


@pytest.mark.asyncio
async def test_auto_us_falls_back_to_google_when_finnhub_unconfigured(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "")     # → _primary_provider = None

    async def _google_news(self, market, ticker, limit):
        return _sentinel("GOOGLE")

    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    out = await AUTO.AutoNewsProvider().news(Market.US, "AAPL", 5)
    assert out[0].ticker == "GOOGLE"


@pytest.mark.asyncio
async def test_auto_us_falls_back_when_finnhub_returns_empty(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")

    async def _finnhub_empty(self, market, ticker, limit):
        return []

    async def _google_news(self, market, ticker, limit):
        return _sentinel("GOOGLE")

    monkeypatch.setattr(FN.FinnhubNewsProvider, "news", _finnhub_empty)
    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    out = await AUTO.AutoNewsProvider().news(Market.US, "AAPL", 5)
    assert out[0].ticker == "GOOGLE"


@pytest.mark.asyncio
async def test_auto_kr_uses_naver_when_configured(monkeypatch):
    monkeypatch.setattr(NV.settings, "naver_client_id", "id")
    monkeypatch.setattr(NV.settings, "naver_client_secret", "sec")

    google_called = []

    async def _naver_news(self, market, ticker, limit):
        return _sentinel("NAVER")

    async def _google_news(self, market, ticker, limit):
        google_called.append(True)
        return _sentinel("GOOGLE")

    monkeypatch.setattr(NV.NaverNewsProvider, "news", _naver_news)
    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    out = await AUTO.AutoNewsProvider().news(Market.KR, "005930", 5)
    assert out[0].ticker == "NAVER"
    assert google_called == []


@pytest.mark.asyncio
async def test_auto_falls_back_when_primary_raises(monkeypatch):
    monkeypatch.setattr(FN.settings, "finnhub_api_key", "k")

    async def _boom(self, market, ticker, limit):
        raise RuntimeError("finnhub exploded")

    async def _google_news(self, market, ticker, limit):
        return _sentinel("GOOGLE")

    monkeypatch.setattr(FN.FinnhubNewsProvider, "news", _boom)
    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    out = await AUTO.AutoNewsProvider().news(Market.US, "AAPL", 5)   # must not raise
    assert out[0].ticker == "GOOGLE"


@pytest.mark.asyncio
async def test_auto_cache_dedups_identical_calls(monkeypatch):
    calls = {"n": 0}

    class _CountingProvider:
        async def news(self, market, ticker, limit):
            calls["n"] += 1
            return _sentinel("PRIMARY")

    monkeypatch.setattr(AUTO, "_primary_provider", lambda market: _CountingProvider())

    google_called = []

    async def _google_news(self, market, ticker, limit):
        google_called.append(True)
        return _sentinel("GOOGLE")

    monkeypatch.setattr(AUTO.GoogleNewsProvider, "news", _google_news)

    provider = AUTO.AutoNewsProvider()
    first = await provider.news(Market.US, "AAPL", 5)
    second = await provider.news(Market.US, "AAPL", 5)     # identical key → served from cache

    assert first[0].ticker == "PRIMARY" and second[0].ticker == "PRIMARY"
    assert calls["n"] == 1                                  # underlying fetch ran exactly once
    assert google_called == []

    # a different key (limit) re-runs the fetch
    await provider.news(Market.US, "AAPL", 10)
    assert calls["n"] == 2
