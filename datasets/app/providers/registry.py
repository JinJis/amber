"""Resolve a concrete provider for a given (market, domain).

Providers are imported and instantiated lazily (and memoized) so that, e.g.,
importing pykrx only happens when a KR endpoint is actually hit. Price-provider
selection honors the ``*_PROVIDER_US`` / ``*_PROVIDER_KR`` settings; everything
else uses the free default for the market.
"""

from __future__ import annotations

from functools import cache

from app.config import settings
from app.errors import not_implemented
from app.providers.base import (
    CompanyProvider,
    EarningsProvider,
    FilingsProvider,
    FinancialsProvider,
    FundProvider,
    InsiderProvider,
    InstitutionalProvider,
    MacroProvider,
    MetricsProvider,
    NewsProvider,
    PricesProvider,
)
from app.symbols import Market


def _unbuilt(domain: str, market: Market):
    raise not_implemented(
        f"The '{domain}' endpoint is not yet implemented for market {market.value}."
    )


# --- Company -------------------------------------------------------------
@cache
def get_company_provider(market: Market) -> CompanyProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarProvider

        return SecEdgarProvider()
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartProvider

        return OpenDartProvider()
    _unbuilt("company", market)


# --- Prices --------------------------------------------------------------
@cache
def get_prices_provider(market: Market) -> PricesProvider:
    if market is Market.US:
        choice = settings.prices_provider_us
        if choice == "auto":  # IMP-15: Yahoo primary → Stooq fallback, honest source label
            from app.providers.chain import build_chain

            return build_chain(market)
        if choice == "yahoo":
            from app.providers.us.yahoo import YahooProvider

            return YahooProvider()
        if choice == "stooq":
            from app.providers.us.stooq import StooqProvider

            return StooqProvider()
        _unbuilt(f"prices(provider={choice})", market)
    if market is Market.KR:
        choice = settings.prices_provider_kr
        if choice == "auto":  # IMP-15: Yahoo primary → KIS fallback (when keys configured)
            from app.providers.chain import build_chain

            return build_chain(market)
        if choice == "kis":  # CE-12 KIS-PRICES: realtime KR prices via the broker API
            from app.providers.kr.kis import KisPricesProvider

            return KisPricesProvider()
        if choice == "pykrx":
            from app.providers.kr.krx import PyKrxProvider

            return PyKrxProvider()
        if choice == "yahoo":
            from app.providers.us.yahoo import YahooProvider

            return YahooProvider()
        _unbuilt(f"prices(provider={choice})", market)
    _unbuilt("prices", market)


# --- Financial statements -----------------------------------------------
@cache
def get_financials_provider(market: Market) -> FinancialsProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarProvider

        return SecEdgarProvider()
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartProvider

        return OpenDartProvider()
    _unbuilt("financials", market)


# --- Filings -------------------------------------------------------------
@cache
def get_filings_provider(market: Market) -> FilingsProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarProvider

        return SecEdgarProvider()
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartProvider

        return OpenDartProvider()
    _unbuilt("filings", market)


# --- Macro ---------------------------------------------------------------
@cache
def get_macro_provider(market: Market) -> MacroProvider:
    if market is Market.US:
        choice = settings.macro_provider_us
        if choice == "auto":
            from app.providers.us.macro_auto import AutoMacroProvider

            return AutoMacroProvider()
        if choice == "fred":
            from app.providers.us.fred import FredProvider

            return FredProvider()
        if choice == "dbnomics":
            from app.providers.us.dbnomics import DBnomicsProvider

            return DBnomicsProvider()
        _unbuilt(f"macro(provider={choice})", market)
    if market is Market.KR:
        from app.providers.kr.ecos import EcosProvider

        return EcosProvider()
    _unbuilt("macro", market)


# --- Metrics -------------------------------------------------------------
@cache
def get_metrics_provider(market: Market) -> MetricsProvider:
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartMetricsProvider

        return OpenDartMetricsProvider()
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarMetricsProvider

        return SecEdgarMetricsProvider()
    _unbuilt("financial-metrics", market)


# --- News ----------------------------------------------------------------
@cache
def get_news_provider(market: Market) -> NewsProvider:
    # Market-routed real-time news (KR=Naver · US=Finnhub) with a Google-News keyless fallback,
    # behind a short dedup cache. Google News stops being the production source (IP-rate-limited).
    from app.providers.news import AutoNewsProvider

    return AutoNewsProvider()


# --- Earnings ------------------------------------------------------------
@cache
def get_earnings_provider(market: Market) -> EarningsProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarEarningsProvider

        return SecEdgarEarningsProvider()
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartEarningsProvider

        return OpenDartEarningsProvider()
    _unbuilt("earnings", market)


# --- Insider trades ------------------------------------------------------
@cache
def get_insider_provider(market: Market) -> InsiderProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarInsiderProvider

        return SecEdgarInsiderProvider()
    if market is Market.KR:
        from app.providers.kr.opendart import OpenDartInsiderProvider

        return OpenDartInsiderProvider()
    _unbuilt("insider-trades", market)


# --- Institutional holdings (13F) ---------------------------------------
@cache
def get_institutional_provider(market: Market) -> InstitutionalProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgar13FProvider

        return SecEdgar13FProvider()
    _unbuilt("institutional-holdings", market)


# --- Index funds / ETF holdings (N-PORT) --------------------------------
@cache
def get_fund_provider(market: Market) -> FundProvider:
    if market is Market.US:
        from app.providers.us.sec_edgar import SecEdgarFundProvider

        return SecEdgarFundProvider()
    _unbuilt("index-funds", market)  # KR = KIS-ETF (deferred to the KIS connector)
