"""Resolve which filings to show/index for a ticker — accession → fetch + canonical URLs.

Shared by the in-app filing viewer (`filing_html`) and the filing-text RAG ingest (`filing_ingest`):
both work from the *original markup* (US SEC iXBRL primary doc · KR OpenDART document.xml), so this
just answers "for this ticker, which recent filings, and where do I fetch each one's markup".
"""

from __future__ import annotations

from app.providers.registry import get_financials_provider
from app.providers.us.sec_edgar import _resolve_cik, _submissions
from app.store.provenance import dart_url, sec_index_url
from app.symbols import Market, build_ref

_STMT_METHODS = ("income_statements", "balance_sheets", "cash_flow_statements")


async def _primary_doc_map(cik10: str) -> dict[str, str]:
    """accession_number → primary-document URL, from the SEC submissions index (same URL
    shape `SecEdgarProvider.filings` builds)."""
    sub = await _submissions(cik10)
    recent = (sub.get("filings") or {}).get("recent") or {}
    accns = recent.get("accessionNumber") or []
    prim = recent.get("primaryDocument") or []
    out: dict[str, str] = {}
    for i, accn in enumerate(accns):
        doc = prim[i] if i < len(prim) and prim[i] else ""
        if accn and doc:
            out[accn] = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accn.replace('-', '')}/{doc}"
    return out


# US event forms whose BODY TEXT is worth indexing for RAG (not just financial statements) so
# the agent can quote a real passage — 8-K (current events) is the big gap the listing citations hit.
_US_EVENT_FORMS = ("8-K",)


async def _event_filing_refs(cik10: str, docmap: dict, limit: int) -> dict[str, dict]:
    """Recent 8-K (event) filings for a US ticker → same {fetch_url, canonical, cik} shape, so
    their body text gets RAG-indexed alongside the statements (fixes 8-K quotes/highlights)."""
    from app.store.provenance import sec_index_url
    sub = await _submissions(cik10)
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    out: dict[str, dict] = {}
    for i, form in enumerate(forms):
        if str(form).upper() not in _US_EVENT_FORMS:
            continue
        accn = accns[i] if i < len(accns) else None
        if not accn or accn not in docmap:
            continue
        out[accn] = {"fetch_url": docmap.get(accn), "canonical": sec_index_url(cik10, accn), "cik": cik10}
        if len(out) >= limit:
            break
    return out


async def filing_refs(market: str, ticker: str, limit: int, include_events: bool = True) -> dict[str, dict]:
    """Recent filing accessions (with their markup-fetch + canonical URLs, and US CIK) for a
    ticker — the financial statements (users cite figures from these) PLUS recent US event forms
    (8-K) when ``include_events`` so their body text is searchable and quotable."""
    market = market.upper()
    ref = build_ref(Market[market], ticker)
    prov = get_financials_provider(Market[market])
    out: dict[str, dict] = {}
    cik, docmap = None, {}
    if market == "US":
        cik = await _resolve_cik(ref)
        docmap = await _primary_doc_map(cik)
    for method_name in _STMT_METHODS:
        method = getattr(prov, method_name, None)
        if method is None:
            continue
        for period in ("annual", "quarterly"):
            try:
                stmts = await method(ref, period, limit)
            except Exception:  # noqa: BLE001 — a missing statement/period never blocks the rest
                continue
            for st in stmts:
                accn = getattr(st, "accession_number", None)
                if not accn or accn in out:
                    continue
                if market == "US":
                    out[accn] = {"fetch_url": docmap.get(accn), "canonical": sec_index_url(cik, accn), "cik": cik}
                else:
                    fu = getattr(st, "filing_url", None)
                    out[accn] = {"fetch_url": None, "canonical": str(fu) if fu else dart_url(accn), "cik": None}
    if market == "US" and include_events and cik:
        for accn, info in (await _event_filing_refs(cik, docmap, limit)).items():
            out.setdefault(accn, info)
    return out
