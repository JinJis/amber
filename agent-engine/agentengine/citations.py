"""Tool result → citations: provenance-bearing source cards + the extracted figures.

Pure data-shaping of known datasets/RAG/news result shapes (NOT reasoning — which
tools to call is the planner's job). Produces `Citation` objects (with the real
figures + a /evidence link where available), dedupes them, and marks which actually
backed the answer (evidence vs merely consulted).
"""

from __future__ import annotations

from urllib.parse import quote

from agentengine.evidence import _evidence_url, rag_evidence_url
from agentengine.freshness import compute_freshness
from agentengine.models import Citation
from agentengine.provenance import (
    _canonical_provenance,
    _filing_link,
    _market_hint,
    _rag_link,
)

# Figure extraction (formatters, statement column specs, table shaping, as-of date)
# and the inline-anchor / evidence-marking concern live in siblings; re-exported here
# so importers (agent.py) keep resolving them via ``agentengine.citations``.
from agentengine.figures import (  # noqa: F401
    _BALANCE_COLS,
    _CASHFLOW_COLS,
    _INCOME_COLS,
    _METRIC_COLS,
    _collect_dates,
    _evidence,
    _fmt_amt,
    _fmt_ratio,
    _latest_date,
    _shape_table,
)
from agentengine.anchors import (  # noqa: F401
    anchor_markers,
    has_anchors,
    mark_evidence,
)

# filing-ish doc_type hints (RAG provenance) → the "filing" preview-card variant.
_FILING_HINTS = ("10-k", "10-q", "8-k", "20-f", "6-k", "s-1", "filing", "annual", "quarterly")
# datasets tools whose citation renders as a "metric computation" card, not raw data.
_METRIC_HINTS = ("price", "metric", "snapshot", "financ", "ratio", "screener", "earnings")


def _rag_type(prov: dict) -> str:
    dt = (prov.get("doc_type") or "").lower()
    if dt == "news":
        return "news"
    if prov.get("accession") or any(h in dt for h in _FILING_HINTS):
        return "filing"
    return "data"


def _datasets_type(tool: dict) -> str:
    return "metric" if any(h in tool["name"].lower() for h in _METRIC_HINTS) else "data"


def text_fragment_url(url: str | None, phrase: str | None) -> str | None:
    """Universal web evidence: append a W3C text-fragment (`#:~:text=`) so opening the source
    scrolls to + highlights the cited phrase in the live article (browser-native, no screenshot).
    Best-effort — the browser ignores it if the text isn't found. Skipped if the url already has
    a fragment or there's nothing to highlight."""
    if not url or not phrase or "#" in url:
        return url
    frag = " ".join(phrase.strip().split()[:10])[:140]
    return f"{url}#:~:text={quote(frag)}" if frag else url


def _news_citations(tool: dict, data) -> list[Citation] | None:
    """Google News (/news) returns articles that each carry their OWN publisher +
    headline + date — cite those, not the connector's generic 'Google News' label."""
    items = data.get("news") if isinstance(data, dict) else None
    if not items:
        return None
    cites, seen = [], set()
    for a in items[:6]:
        if not isinstance(a, dict):
            continue
        url = a.get("url")
        src = a.get("source") or tool.get("source") or "Google News"  # publisher (Reuters/연합뉴스/…)
        key = (src, url)
        if key in seen:
            continue
        seen.add(key)
        as_of = a.get("date")
        title = a.get("title") or ""
        cites.append(Citation(
            tool=tool["name"], source=src, url=text_fragment_url(url, title), kind="news", doc_type="news",
            as_of=as_of, freshness=compute_freshness(as_of),
            snippet=title[:300] or None, ticker=a.get("ticker"),
        ))
    return cites or None


def _rag_citations(tool: dict, data) -> list[Citation] | None:
    """RAG returns passages that each carry their OWN provenance — cite those
    (the real document source/url), not the connector's generic label."""
    hits = data.get("hits") if isinstance(data, dict) else None
    if not hits:
        return None
    cites, seen = [], set()
    for h in hits[:5]:
        prov = (h or {}).get("provenance") or {}
        src, url = prov.get("source"), _rag_link(prov)
        key = (src, url)
        if not (src or url) or key in seen:
            continue
        seen.add(key)
        as_of = prov.get("as_of")
        text = (h or {}).get("text") or ""
        # news/web passages get a text-fragment deep link so opening the source highlights the
        # cited passage in the live page; filing passages keep a clean url (the in-app viewer highlights them).
        is_news = (prov.get("doc_type") or "").lower() == "news" and not prov.get("accession")
        link = text_fragment_url(url, text) if is_news else url
        cites.append(Citation(
            tool=tool["name"], source=src or tool.get("source"), url=link,
            kind=_rag_type(prov), doc_type=prov.get("doc_type"), as_of=as_of,
            freshness=compute_freshness(as_of),
            snippet=text[:300] or None,
            ticker=prov.get("ticker"), page=prov.get("section") or prov.get("accession"),
            # PH-PROV3e: a filing passage (has an accession) → highlight it in the cached PDF
            evidence_image_url=rag_evidence_url(prov.get("market"), prov.get("accession"), text),
        ))
    return cites or None


def _citations(tool: dict, result: dict) -> list[Citation]:
    """Build the tool's citations and stamp each with the source's periodicity + category (from
    the catalog tool dict) so the pin→alert flow can gate on it downstream."""
    cites = _build_citations(tool, result)
    cad, cat = tool.get("cadence"), tool.get("category")
    for c in cites:
        if c.cadence is None:
            c.cadence = cad
        if c.category is None:
            c.category = cat
    return cites


def _build_citations(tool: dict, result: dict) -> list[Citation]:
    data = result.get("data")
    if "search" in tool["name"] or tool.get("connector") == "rag":
        rag = _rag_citations(tool, data)
        if rag is not None:
            return rag
    if tool.get("connector") == "google_news" or tool["name"].endswith("__news"):
        news = _news_citations(tool, data)
        if news is not None:
            return news
    src = tool.get("source")
    ctype = _datasets_type(tool)
    market = _market_hint(tool, data)
    # A filings *listing* → one evidence card per distinct filing document (each its
    # own source: its filing_url + what the filing is).
    if isinstance(data, dict) and isinstance(data.get("filings"), list):
        out, seen = [], set()
        for f in data["filings"][:6]:
            if not isinstance(f, dict):
                continue
            u = (f.get("filing_url") or f.get("source_url") or f.get("url")
                 or _filing_link(market, f.get("accession_number"), f.get("cik")))
            if not u or u in seen:
                continue
            seen.add(u)
            fa = f.get("filed") or f.get("report_period") or f.get("as_of")
            fa = str(fa)[:10] if fa else None
            out.append(Citation(
                tool=tool["name"], source=src, url=u, kind="filing", as_of=fa,
                freshness=compute_freshness(fa), page=f.get("accession_number"),
                snippet=(f.get("title") or f.get("form") or f.get("filing_type") or None)))
        if out:
            return out
    # Derived figures (financials / metrics / prices): show the SPECIFIC figures used +
    # link to the exact filing they came from — not a label or a directory listing.
    as_of = _latest_date(data)
    url, accn, cik = _canonical_provenance(data)        # canonical filing link / accession
    if not url and accn:                                # build it from the identifier
        url = _filing_link(market, accn, cik)
    snippet, table = _evidence(tool, data)              # the real figures + extracted table
    return [Citation(tool=tool["name"], source=src, url=url, kind=ctype, as_of=as_of,
                     freshness=compute_freshness(as_of), snippet=snippet, table=table, page=accn,
                     evidence_image_url=_evidence_url(data, accn, cik, market))]


def dedup_citations(cites: list[Citation]) -> list[Citation]:
    """Collapse repeats — the same (source, url) cited by several tool calls should
    appear once (fixes the '📎 OpenDART · 📎 OpenDART · …' repetition)."""
    seen: set = set()
    out: list[Citation] = []
    for c in cites:
        key = (c.source, c.url)
        if key in seen:
            continue
        seen.add(key)
        c.index = len(out) + 1  # 1-based [n] anchor
        out.append(c)
    return out
