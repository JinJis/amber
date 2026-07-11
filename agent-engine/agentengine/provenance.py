"""Canonical provenance links + extraction from tool results.

SEC index-page / DART rcpNo-viewer link builders, and the walk that pulls the
canonical (url, accession, cik) out of a datasets response. Mirror of datasets
`app.store.provenance` — agent-engine is a separate deployable, so these tiny
builders are duplicated here on purpose (can't import across services).
"""

from __future__ import annotations


def _sec_index_url(cik, accession) -> str | None:
    if not accession or not cik:
        return None
    nodash = str(accession).replace("-", "")
    if len(nodash) != 18:
        return None
    dashed = accession if "-" in str(accession) else f"{nodash[:10]}-{nodash[10:12]}-{nodash[12:]}"
    try:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{nodash}/{dashed}-index.htm"
    except (TypeError, ValueError):
        return None


def _filing_link(market, accession, cik=None) -> str | None:
    """Canonical link to the *specific filing* — SEC index page / DART rcpNo viewer."""
    if not accession:
        return None
    m = (market or "").upper()
    if m == "KR":
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={accession}"
    if m == "US":
        return _sec_index_url(cik, accession)
    return None


def _market_hint(tool: dict, data) -> str | None:
    """KR vs US for link-building: the data's own market field, else the connector."""
    if isinstance(data, dict) and isinstance(data.get("market"), str) and data["market"]:
        return data["market"].upper()
    conn = (tool.get("connector") or tool.get("name") or "").lower()
    if "dart" in conn or "opendart" in conn or "kis" in conn:
        return "KR"
    if "sec" in conn or "edgar" in conn or "yahoo" in conn or "fred" in conn:
        return "US"
    return None


def _market_from_link(url: str | None, accession: str | None = None) -> str | None:
    """Deterministic market recovery from the row's own provenance when the tool/data carry no
    market hint (e.g. the Ingestion-Store listing tools): the canonical link's host, else the
    accession format (SEC: 18 digits/dashed; DART rcept_no: 14 digits)."""
    u = (url or "").lower()
    if "sec.gov" in u:
        return "US"
    if "dart.fss.or.kr" in u:
        return "KR"
    a = str(accession or "")
    digits = a.replace("-", "")
    if digits.isdigit():
        if len(digits) == 18:
            return "US"
        if len(digits) == 14:
            return "KR"
    return None


_CANON_URL_KEYS = ("filing_url", "source_url")
_PROV_KEYS = {"filing_url", "source_url", "accession_number", "cik", "url", "ticker",
              "market", "currency", "period", "fiscal_period", "source"}


def _canonical_provenance(data) -> tuple[str | None, str | None, str | None]:
    """(url, accession, cik) from the first row carrying a *canonical* filing link or
    accession — never an incidental url (a directory listing is not the filing)."""
    found = {"url": None, "accn": None, "cik": None}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kl = k.lower()
                if found["url"] is None and isinstance(v, str) and v.startswith("http") and kl in _CANON_URL_KEYS:
                    found["url"] = v
                elif found["accn"] is None and kl == "accession_number" and v:
                    found["accn"] = str(v)
                elif found["cik"] is None and kl == "cik" and v:
                    found["cik"] = str(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o[:30]:
                walk(x)

    walk(data)
    return found["url"], found["accn"], found["cik"]


def _rag_link(prov: dict) -> str | None:
    """A RAG chunk's canonical link: its own url, else built from its accession."""
    return prov.get("url") or _filing_link(prov.get("market"), prov.get("accession"))
