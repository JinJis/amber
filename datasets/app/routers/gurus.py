"""Superinvestor ("거장") 13F portfolios (PH-DATA-1).

`GET /gurus` lists the curated famous investors; `?slug=` returns that investor's latest
13F holdings (reusing the 13F provider) — every position carries its `accession_number`, so
the agent cites each holding to the real SEC filing (provenance, not an opaque feed).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.deps import ApiKeyDep
from app.errors import not_found
from app.providers.registry import get_institutional_provider
from app.providers.us.gurus import (
    common_holdings,
    compute_trades,
    get_guru,
    list_gurus,
)
from app.routers._common import gather_best_effort
from app.symbols import Market

router = APIRouter(tags=["Superinvestors"])


@router.get(
    "/gurus",
    dependencies=[ApiKeyDep],
    summary="Superinvestor (거장) 13F portfolios — Buffett, Burry, Ackman …",
    description=(
        "Omit `slug` to list the curated investors. With `?slug=buffett` (etc.) returns that "
        "investor's latest SEC 13F holdings — each position cited to the filing. US only."
    ),
)
async def gurus(
    slug: str | None = Query(None, description="Guru slug (e.g. buffett); omit to list all."),
    limit: int = Query(20, ge=1, le=200),
) -> dict:
    if not slug:
        return {"resource": "gurus", "gurus": list_gurus()}
    g = get_guru(slug)
    if not g:
        raise not_found(f"Unknown guru '{slug}'. See GET /gurus for the list.")
    holdings = await get_institutional_provider(Market.US).by_filer(g["cik"], limit)
    return {"guru": g, "filer_cik": g["cik"], "holdings": holdings}


@router.get(
    "/gurus/trades",
    dependencies=[ApiKeyDep],
    summary="거장 매매 — a superinvestor's latest quarter-over-quarter 13F moves",
    description=(
        "Diffs an investor's two most recent SEC 13F filings into discrete moves — "
        "**new / added / trimmed / exited** — with share & value deltas. Each move is cited to "
        "the actual 13F accession (provenance, not an inferred feed). `?slug=buffett`. US only."
    ),
)
async def guru_trades(
    slug: str = Query(..., description="Guru slug (e.g. buffett). See GET /gurus."),
    limit: int = Query(40, ge=1, le=200),
) -> dict:
    g = get_guru(slug)
    if not g:
        raise not_found(f"Unknown guru '{slug}'. See GET /gurus for the list.")
    quarters = await get_institutional_provider(Market.US).by_filer_quarters(g["cik"], 2)
    result = compute_trades(quarters, limit)
    return {"guru": g, "filer_cik": g["cik"], **result}


@router.get(
    "/gurus/common",
    dependencies=[ApiKeyDep],
    summary="공통 보유종목 — securities held by the most superinvestors right now",
    description=(
        "Intersects the latest 13F holdings across the curated investors (or `?slugs=buffett,ackman`) "
        "and ranks securities by how many gurus hold them. Each holder is cited to its 13F filing. "
        "US only."
    ),
)
async def guru_common(
    slugs: str | None = Query(None, description="Comma-separated guru slugs; omit for all curated gurus."),
    min_holders: int = Query(2, ge=1, le=20, description="Minimum number of gurus holding a security."),
    per_filer: int = Query(50, ge=1, le=200, description="Top positions to pull per guru."),
    limit: int = Query(40, ge=1, le=200),
) -> dict:
    if slugs:
        chosen = [g for s in slugs.split(",") if (g := get_guru(s.strip()))]
        if not chosen:
            raise not_found(f"No known gurus in '{slugs}'. See GET /gurus.")
    else:
        chosen = list_gurus()
    provider = get_institutional_provider(Market.US)

    async def _fetch(g: dict) -> dict:
        return {"guru": g, "holdings": await provider.by_filer(g["cik"], per_filer)}

    # best-effort: skip a guru whose 13F is unavailable, never fabricate
    per_guru = await gather_best_effort(chosen, _fetch)
    rows = common_holdings(per_guru, min_holders, limit)
    return {
        "resource": "gurus_common",
        "gurus_considered": [g["slug"] for g in chosen],
        "gurus_resolved": [x["guru"]["slug"] for x in per_guru],
        "min_holders": min_holders,
        "common": rows,
    }
