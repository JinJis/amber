"""Shared router helpers (RF-04).

Small, behavior-preserving utilities that several routers re-implemented: choice validation
(interval/period), best-effort concurrent fan-out (skip per-item failures, never fabricate), and the
"list this resource's tickers" response. Keeping them here stops the copy-paste from drifting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from typing import TypeVar

from app.errors import bad_request
from app.models.generated import TickersResponse
from app.providers.registry import get_company_provider
from app.symbols import Market

# Allowed values, shared so the validation message + the set stay in one place.
INTERVALS = ["day", "week", "month", "year"]
PERIODS = ["annual", "quarterly", "ttm"]


def check_choice(value: str, allowed: list[str], name: str) -> None:
    """Raise the standard 400 if `value` isn't in `allowed` (message kept identical to the old inline checks)."""
    if value not in allowed:
        raise bad_request(f"{name} must be one of {allowed}.")


def validate_interval(interval: str) -> None:
    check_choice(interval, INTERVALS, "interval")


def validate_period(period: str) -> None:
    check_choice(period, PERIODS, "period")


_T = TypeVar("_T")


async def gather_best_effort(items: Iterable, fn: Callable[[object], Awaitable[_T]]) -> list[_T]:
    """Run `fn(item)` for every item concurrently; drop any that raise or return None.

    The platform's fan-out contract: a per-item failure (e.g. one un-priceable ticker) is skipped,
    never fabricated, and never sinks the rest. Replaces the repeated inner `_one`+gather+filter pattern.
    """
    async def _safe(item):
        try:
            return await fn(item)
        except Exception:  # noqa: BLE001 — best-effort: skip a failing item, don't fabricate
            return None

    results = await asyncio.gather(*[_safe(i) for i in items])
    return [r for r in results if r is not None]


async def tickers_response(market: Market, resource: str) -> TickersResponse:
    """The shared `/<resource>/tickers` response: the tickers we track for this market + resource."""
    tickers = await get_company_provider(market).list_tickers()
    return TickersResponse(resource=resource, tickers=tickers)
