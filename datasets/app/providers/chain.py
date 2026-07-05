"""Resilient price-provider chain (IMP-15).

Yahoo stays the primary EOD source for both markets, but a transient upstream
failure (e.g. the intermittent 503s Yahoo returns for ``.KS`` symbols) no longer
takes the endpoint down: the chain falls through to another API we already run —
Stooq (US, keyless) or KIS (KR, when the broker keys are configured). The member
that actually served is reported back so responses stay honestly sourced —
a KIS-served bar is never attributed to Yahoo.

Fall-through rules:
- a member is skipped when it can't address the symbol at all (KIS needs a
  6-digit KRX code; Stooq can't resolve ``^GSPC``-style index symbols), so
  fallbacks never burn rate limit on guaranteed misses;
- any exception or an *empty* result moves to the next member;
- if every member fails, the PRIMARY member's error is raised (it names the
  source the user expects and is the most diagnostic).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.errors import not_found
from app.models.generated import Price, PriceSnapshot
from app.symbols import Market, SecurityRef


@dataclass(frozen=True)
class ChainMember:
    key: str            # machine id, stored in PriceBar.source ("yahoo" | "stooq" | "kis")
    label: str          # human label for response `source` fields
    provider: object
    eligible: Callable[[SecurityRef], bool]


class ChainPricesProvider:
    """PricesProvider that tries members in order; drop-in for the registry."""

    def __init__(self, members: list[ChainMember]) -> None:
        if not members:
            raise ValueError("ChainPricesProvider needs at least one member")
        self.members = members

    async def _run(self, method: str, ref: SecurityRef, *args):
        first_err: Exception | None = None
        for m in self.members:
            fn = getattr(m.provider, method, None)
            if fn is None or not m.eligible(ref):
                continue
            try:
                out = await fn(ref, *args)
            except Exception as e:  # noqa: BLE001 — any member failure falls through
                if first_err is None:
                    first_err = e
                continue
            if self._empty(method, out):
                continue
            return m, out
        if first_err is not None:
            raise first_err
        raise not_found(f"No provider in the chain can serve '{ref.ticker}'.")

    @staticmethod
    def _empty(method: str, out) -> bool:
        if method == "prices":
            return not out
        if method == "snapshot":
            return out is None or out.price is None
        return False  # corporate_actions: an empty history is a valid answer

    # --- labeled variants (routers/ingest use these to attribute the real source) ---
    async def prices_labeled(
        self, ref: SecurityRef, interval: str, start: date, end: date
    ) -> tuple[ChainMember, list[Price]]:
        return await self._run("prices", ref, interval, start, end)

    # --- PricesProvider protocol ---
    async def prices(self, ref: SecurityRef, interval: str, start: date, end: date) -> list[Price]:
        _, out = await self._run("prices", ref, interval, start, end)
        return out

    async def snapshot(self, ref: SecurityRef) -> PriceSnapshot:
        m, snap = await self._run("snapshot", ref)
        snap.source = m.label
        return snap

    async def corporate_actions(self, ref: SecurityRef, start: date, end: date) -> dict:
        _, out = await self._run("corporate_actions", ref, start, end)
        return out


def _kr_code(ref: SecurityRef) -> bool:
    return ref.ticker.isdigit() and len(ref.ticker) == 6


def _plain_symbol(ref: SecurityRef) -> bool:
    return ref.ticker.replace(".", "").replace("-", "").isalnum() and not ref.ticker.startswith("^")


def build_chain(market: Market) -> ChainPricesProvider:
    from app.providers.us.yahoo import YahooProvider

    members = [ChainMember("yahoo", "Yahoo Finance", YahooProvider(), lambda _r: True)]
    if market is Market.US:
        from app.providers.us.stooq import StooqProvider

        members.append(ChainMember("stooq", "Stooq", StooqProvider(), _plain_symbol))
    if market is Market.KR:
        from app.config import settings

        if settings.kis_app_key and settings.kis_app_secret:
            from app.providers.kr.kis import KisPricesProvider

            members.append(ChainMember("kis", "한국투자증권 (KIS)", KisPricesProvider(), _kr_code))
    return ChainPricesProvider(members)
