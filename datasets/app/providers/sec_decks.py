"""Resolve a US ticker's recent earnings/investor PRESENTATION decks (PDF) from SEC EDGAR.

Companies file the deck as an 8-K Exhibit 99 (free, public). We find recent 8-K filings, read each
filing's index, and pick the EX-99 PDF that looks like a presentation (vs the press release). The
PDF is then parsed by Document AI (text → RAG) and shown in the in-app pdf.js viewer.
"""

from __future__ import annotations

import logging
import re

import httpx

from app.config import settings
from app.providers.us.sec_edgar import _UA, _resolve_cik, _submissions
from app.symbols import Market, build_ref

log = logging.getLogger(__name__)

_PRES = re.compile(r"present|slides?|deck|investor|earnings", re.I)


def _archive(cik: int, accession: str, name: str) -> str:
    nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/{name}"


async def _filing_index(cik: int, accession: str) -> list[dict]:
    nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nodash}/index.json"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(url, headers=_UA)
        if r.status_code != 200:
            return []
        return (r.json().get("directory") or {}).get("item") or []
    except (httpx.HTTPError, ValueError):
        return []


def _pick_deck(items: list[dict]) -> dict | None:
    """The most deck-like PDF in a filing's file list (prefer EX-99.2 / presentation-named)."""
    pdfs = [it for it in items if str(it.get("name", "")).lower().endswith(".pdf")]
    if not pdfs:
        return None
    def score(it: dict) -> tuple:
        name, typ = str(it.get("name", "")), str(it.get("type", ""))
        return (
            bool(_PRES.search(name) or _PRES.search(typ)),      # presentation-named wins
            typ.upper().startswith("EX-99"),                    # an EX-99 exhibit
            "99.2" in typ or "ex992" in name.lower(),           # EX-99.2 is the deck (99.1 = release)
            int(it.get("size") or 0),                           # decks are large
        )
    return max(pdfs, key=score)


async def recent_decks(ticker: str, limit: int = 4) -> list[dict]:
    """Up to ``limit`` recent 8-K presentation decks for a US ticker:
    ``[{accession, ticker, filed, title, pdf_url}]`` (newest first; best-effort)."""
    try:
        cik10 = await _resolve_cik(build_ref(Market.US, ticker))
        cik = int(cik10)
        sub = await _submissions(cik10)
    except Exception as exc:  # noqa: BLE001
        log.info("decks: cannot resolve %s: %s", ticker, exc)
        return []
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accns = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    descs = recent.get("primaryDocDescription") or []
    out: list[dict] = []
    for i, form in enumerate(forms):
        if len(out) >= limit:
            break
        if form not in ("8-K", "8-K/A"):
            continue
        accn = accns[i] if i < len(accns) else None
        if not accn:
            continue
        deck = _pick_deck(await _filing_index(cik, accn))
        if not deck:
            continue
        out.append({
            "accession": accn, "ticker": ticker.upper(),
            "filed": dates[i] if i < len(dates) else None,
            "title": (descs[i] if i < len(descs) else None) or "Investor presentation",
            "pdf_url": _archive(cik, accn, str(deck["name"])),
        })
    return out
