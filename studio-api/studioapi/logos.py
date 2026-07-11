"""Company-logo proxy — streams a ticker's cached brand image from the data plane through the
**gateway** with the tenant key (mirrors ``evidence.py``; the browser can't call the gateway).
Returns the PNG, or 204 when there's no logo so the UI draws a monogram instead. A logo is a
decorative asset, so it's an ungoverned passthrough (no entitlement)."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query, Response

from studioapi.config import settings
from studioapi.deps import current_user, require_service
from studioapi.models import User

router = APIRouter(tags=["Logos"], dependencies=[Depends(require_service)])


@router.get("/logos", summary="A ticker's company logo (via the gateway) — 204 when none")
async def logo(
    market: str = Query("US"),
    ticker: str = Query(..., min_length=1),
    user: User = Depends(current_user),
) -> Response:
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds + 20) as client:
            resp = await client.get(
                f"{settings.control_plane_url}/logos",
                params={"market": market, "ticker": ticker},
                headers={"X-API-KEY": user.api_key},
            )
        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and ct.startswith("image/"):
            return Response(content=resp.content, media_type=ct,
                            headers={"cache-control": "public, max-age=604800"})
    except httpx.HTTPError:
        pass
    return Response(status_code=204, headers={"cache-control": "public, max-age=86400"})
