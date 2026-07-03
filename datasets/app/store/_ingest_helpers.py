"""Shared helpers for the price/corp-action ingestion runners (RF-05).

Small, provider-agnostic utilities used by both ``prices_ingest`` and ``corp_actions_ingest``:
lenient date/number coercion (Yahoo already returns numbers), an incremental start-date rule, and a
bounded retry. Kept here so the two runners stay in sync.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta


def _to_date(v) -> date | None:
    try:
        return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _incremental_start(last: date | None, full_start: date, overlap_days: int) -> date:
    """Start date for an incremental pull: last-stored − overlap, never before `full_start`.
    `last is None` (no prior data) → full backfill from `full_start`."""
    if last is None:
        return full_start
    return max(full_start, last - timedelta(days=overlap_days))


async def _retry(fn, retries: int):
    """Await ``fn`` up to ``retries``+1 times; return its result or raise the last error."""
    last = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
    raise last  # type: ignore[misc]
