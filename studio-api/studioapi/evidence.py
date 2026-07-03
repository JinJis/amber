"""Evidence proxy — the original filing as sanitized HTML for the in-app viewer.

The browser can't call the gateway directly (no tenant key client-side), so this streams the
filing HTML from the data plane through the **gateway** with the user's server-side tenant key.
Returns the HTML, or 204 when no source markup is available (the UI then offers the external
"원문 보기" link). The viewer renders it in a sandboxed iframe and highlights the cited element."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query, Response

from studioapi.config import settings
from studioapi.deps import current_user, require_service
from studioapi.models import User

router = APIRouter(tags=["Evidence"], dependencies=[Depends(require_service)])


@router.get("/evidence/deck", summary="An 8-K presentation deck (PDF) for the in-app pdf.js viewer (via the gateway)")
async def evidence_deck(accession: str = Query(...), user: User = Depends(current_user)):
    """Stream the cached deck PDF (→ gateway → datasets) with the tenant key, for the pdf.js viewer
    that renders the slides + highlights the cited chunk. 204 → the UI degrades to the link."""
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds + 40) as client:
            resp = await client.get(f"{settings.control_plane_url}/evidence/deck",
                                    params={"accession": accession}, headers={"X-API-KEY": user.api_key})
        if resp.status_code == 200 and "pdf" in resp.headers.get("content-type", "").lower():
            return Response(content=resp.content, media_type="application/pdf",
                            headers={"cache-control": "private, max-age=86400"})
    except httpx.HTTPError:
        pass
    return Response(status_code=204)


@router.get("/evidence/html", summary="The original filing as sanitized HTML for the in-app viewer (via the gateway)")
async def evidence_html(
    market: str = Query("US"),
    accession: str = Query(...),
    cik: str | None = Query(None),
    user: User = Depends(current_user),
):
    params: dict = {"market": market, "accession": accession}
    if cik is not None:
        params["cik"] = cik
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds + 40) as client:
            resp = await client.get(
                f"{settings.control_plane_url}/evidence/html", params=params,
                headers={"X-API-KEY": user.api_key},
            )
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("text/html"):
            return Response(content=resp.content, media_type="text/html; charset=utf-8",
                            headers={"cache-control": "private, max-age=86400"})
    except httpx.HTTPError:
        pass
    return Response(status_code=204)  # graceful fallback → UI shows the external source link


@router.get("/evidence/url", summary="Any public data-source page as sanitized HTML for the in-app viewer (via the gateway)")
async def evidence_url(
    u: str = Query(..., description="The external source URL to render (BLS/DBnomics/FRED/news/…)"),
    user: User = Depends(current_user),
):
    """Same as `/evidence/html` but for a non-filing source: the data plane fetches `u` SSRF-safe,
    sanitizes it (scripts stripped, strict CSP → no egress) and returns it so the viewer renders the
    real page and highlights the cited value/passage. 204 → the UI degrades to the external link."""
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds + 40) as client:
            resp = await client.get(
                f"{settings.control_plane_url}/evidence/url", params={"u": u},
                headers={"X-API-KEY": user.api_key},
            )
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("text/html"):
            return Response(content=resp.content, media_type="text/html; charset=utf-8",
                            headers={"cache-control": "private, max-age=86400"})
    except httpx.HTTPError:
        pass
    return Response(status_code=204)  # graceful fallback → UI shows the external source link
