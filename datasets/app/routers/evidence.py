"""Evidence endpoint — the original filing, in-app, for a cited figure or passage.

A utility route (not a catalog resource → gateway-proxied, no entitlement, like `/company/search`).
`/evidence/html` serves the filing as sanitized HTML (US iXBRL primary doc · KR OpenDART
document.xml) so the web viewer renders the *real* document and highlights the cited element in the
DOM. A gap → `204`, and the UI degrades to the external "원문 보기" link; never fabricated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from app.deps import ApiKeyDep
from app.store.filing_html import get_filing_html
from app.store.source_html import get_source_html
from app.store.transcript_html import get_transcript_html, parse_accession

router = APIRouter(tags=["Evidence"])
log = logging.getLogger(__name__)

_HTML_CACHE = {"cache-control": "public, max-age=86400"}


@router.get("/evidence/html", dependencies=[ApiKeyDep],
            summary="The original filing as sanitized HTML for the in-app viewer",
            description="US iXBRL primary doc / KR OpenDART document.xml, sanitized (scripts stripped, "
                        "strict CSP → no egress) and cached. 204 when no source markup is available "
                        "(the UI then offers the external '원문 보기' link).")
async def evidence_html(market: str, accession: str, cik: str | None = None):
    # a synthetic `TR:{ticker}:{quarter}` accession → an earnings-call transcript (Phase 1), served
    # through this same route so the viewer/highlight/BFF/gateway chain is reused unchanged.
    tr = parse_accession(accession)
    if tr is not None:
        html = await get_transcript_html(*tr)
    else:
        html = await get_filing_html(market, accession, cik)
    if not html:
        return Response(status_code=204)
    return Response(content=html, media_type="text/html; charset=utf-8", headers=_HTML_CACHE)


@router.get("/evidence/deck", dependencies=[ApiKeyDep],
            summary="An 8-K presentation deck (PDF) for the in-app pdf.js viewer")
async def evidence_deck(accession: str):
    """The cached deck PDF for a `DECK:{ticker}:{accession}` accession, served same-origin so the
    pdf.js viewer renders the slides + highlights the cited chunk. 204 when not available."""
    from app.store.deck_ingest import get_deck_pdf
    pdf = await get_deck_pdf(accession)
    if not pdf:
        return Response(status_code=204)
    return Response(content=pdf, media_type="application/pdf", headers=_HTML_CACHE)


@router.get("/evidence/url", dependencies=[ApiKeyDep],
            summary="Any public data-source page as sanitized HTML for the in-app viewer",
            description="Fetches a source URL (BLS/DBnomics/FRED series page, news article, …) "
                        "SSRF-safe (public host only, redirects re-validated, HTML + size cap), "
                        "sanitizes it (scripts stripped, strict CSP → no egress) and serves it "
                        "same-origin so the viewer can highlight the cited value/passage. 204 when "
                        "it can't be shown — the UI then degrades to the external link.")
async def evidence_url(u: str):
    html = await get_source_html(u)
    if not html:
        return Response(status_code=204)
    return Response(content=html, media_type="text/html; charset=utf-8", headers=_HTML_CACHE)
