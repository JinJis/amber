"""IMP-15 — resilient price-provider chain: fallback, honest source, eligibility."""

from __future__ import annotations

from datetime import date

import pytest

from app.errors import APIError, upstream_error
from app.models.generated import Price, PriceSnapshot
from app.providers.chain import ChainMember, ChainPricesProvider, build_chain
from app.symbols import Market, SecurityRef, build_ref

_REF_US = build_ref(Market.US, "AAPL")
_REF_KR = build_ref(Market.KR, "005930")
_RANGE = ("day", date(2026, 1, 1), date(2026, 2, 1))
_BAR = Price(time="2026-01-02", close=100.0)


class _Ok:
    def __init__(self, label="ok"):
        self.label = label

    async def prices(self, ref, interval, start, end):
        return [_BAR]

    async def snapshot(self, ref):
        return PriceSnapshot(ticker=ref.ticker, price=100.0)


class _Boom:
    async def prices(self, ref, interval, start, end):
        raise upstream_error("yahoo", "503 from upstream")

    async def snapshot(self, ref):
        raise upstream_error("yahoo", "503 from upstream")


class _Empty:
    async def prices(self, ref, interval, start, end):
        return []

    async def snapshot(self, ref):
        return PriceSnapshot(ticker=ref.ticker, price=None)


def _m(key, provider, eligible=lambda _r: True):
    return ChainMember(key, key, provider, eligible)


async def test_falls_through_on_upstream_error_and_labels_source():
    chain = ChainPricesProvider([_m("yahoo", _Boom()), _m("kis", _Ok())])
    member, bars = await chain.prices_labeled(_REF_KR, *_RANGE)
    assert member.key == "kis" and bars == [_BAR]
    snap = await chain.snapshot(_REF_KR)
    assert snap.price == 100.0 and snap.source == "kis"


async def test_empty_result_falls_through():
    chain = ChainPricesProvider([_m("yahoo", _Empty()), _m("stooq", _Ok())])
    member, bars = await chain.prices_labeled(_REF_US, *_RANGE)
    assert member.key == "stooq" and bars == [_BAR]


async def test_all_fail_raises_primary_error():
    chain = ChainPricesProvider([_m("yahoo", _Boom()), _m("stooq", _Empty())])
    with pytest.raises(APIError) as exc:
        await chain.prices(_REF_US, *_RANGE)
    assert "yahoo" in exc.value.message


async def test_ineligible_member_is_skipped():
    # a fallback that can't address the symbol must not be called at all
    class _Never:
        async def prices(self, ref, interval, start, end):
            raise AssertionError("ineligible member was called")

    chain = ChainPricesProvider(
        [_m("yahoo", _Ok()), ChainMember("kis", "kis", _Never(), lambda r: r.ticker.isdigit())]
    )
    member, _ = await chain.prices_labeled(_REF_US, *_RANGE)
    assert member.key == "yahoo"


async def test_corporate_actions_delegates_to_member_that_has_it():
    class _Actions:
        async def corporate_actions(self, ref, start, end):
            return {"currency": "USD", "dividends": [], "splits": []}

    chain = ChainPricesProvider([_m("stooq", _Ok()), _m("yahoo", _Actions())])
    out = await chain.corporate_actions(_REF_US, date(2020, 1, 1), date(2026, 1, 1))
    assert out["currency"] == "USD"


def test_build_chain_shape(monkeypatch):
    us = build_chain(Market.US)
    assert [m.key for m in us.members] == ["yahoo", "stooq"]
    # stooq never sees index symbols
    assert not us.members[1].eligible(build_ref(Market.US, "^GSPC"))

    from app.config import settings

    monkeypatch.setattr(settings, "kis_app_key", "", raising=False)
    monkeypatch.setattr(settings, "kis_app_secret", "", raising=False)
    kr = build_chain(Market.KR)
    assert [m.key for m in kr.members] == ["yahoo"]  # no keys → no KIS member

    monkeypatch.setattr(settings, "kis_app_key", "k", raising=False)
    monkeypatch.setattr(settings, "kis_app_secret", "s", raising=False)
    kr = build_chain(Market.KR)
    assert [m.key for m in kr.members] == ["yahoo", "kis"]
    # KIS only sees 6-digit KRX codes (index aliases like ^KS11 skip it)
    assert kr.members[1].eligible(_REF_KR)
    assert not kr.members[1].eligible(SecurityRef(market=Market.KR, ticker="^KS11"))
