"""Tool result → citations: provenance-bearing source cards + the extracted figures.

Pure data-shaping of known datasets/RAG/news result shapes (NOT reasoning — which
tools to call is the planner's job). Produces `Citation` objects (with the real
figures + a /evidence link where available), dedupes them, and marks which actually
backed the answer (evidence vs merely consulted).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from agentengine.evidence import _evidence_url, filing_evidence_url, rag_evidence_url
from agentengine.freshness import compute_freshness
from agentengine.models import Citation, Computation
from agentengine.provenance import (
    _canonical_provenance,
    _filing_link,
    _market_from_link,
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


def _cik_from_url(url: str | None) -> str | None:
    """Pull the CIK from a canonical SEC url (/edgar/data/{cik}/…) so a listing citation can
    build its filing-HTML evidence URL even when the row omits an explicit cik."""
    import re as _re
    m = _re.search(r"edgar/data/(\d+)", url or "")
    return m.group(1) if m else None


def _first_item_header(items: str | None) -> str | None:
    """The first 8-K item code → the literal header the SEC document uses ('Item 5.02'), a
    reliable highlight target that appears verbatim in the 8-K HTML."""
    if not items:
        return None
    first = str(items).split(",")[0].strip()
    return f"Item {first}" if first else None


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
        # a "[Item 1A …]" / "[제2장 …]" heading prefix is chunker metadata, not document text —
        # strip it so the snippet reads clean AND the viewer's text-highlight needle can match.
        text = re.sub(r"^\[[^\]]{1,80}\]\s*", "", text)
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
    the catalog tool dict) so the pin→alert flow can gate on it downstream.

    A FAILED call (non-200 / no data) contributes nothing: previously it still produced a bare
    catalog-label card ('Platform RAG (filings/news)', no url, no snippet) that polluted the
    evidence panel with sources the answer never used."""
    if result.get("status") != 200 or result.get("data") is None:
        return []
    cites = _build_citations(tool, result)
    cad, cat = tool.get("cadence"), tool.get("category")
    for c in cites:
        if c.cadence is None:
            c.cadence = cad
        if c.category is None:
            c.category = cat
    return cites


def _derived_computation(tool: dict, data) -> Computation | None:
    """M-DERIV (DRV-2): a derived figure's citation carries its derivation. Prefer a
    computation the DATA PLANE embedded in the response (DRV-1 — computed at the
    computation site, single truth); else build it for the agent-side derived tools
    exactly like the artifact does. Never let a malformed trace break the citation."""
    if not isinstance(data, dict):
        return None
    try:
        snap = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else {}
        embedded = data.get("computation") or snap.get("computation")
        if isinstance(embedded, dict):
            return Computation.model_validate(embedded)
        name = tool.get("name") or ""
        from agentengine.artifacts import (
            _backtest_computation,
            _quant_computation,
            _valuation_computation,
        )
        if name.endswith("__valuation"):
            return _valuation_computation(data)
        if name.endswith("__quant_screen"):
            return _quant_computation(data)
        if name.endswith("__backtest"):
            return _backtest_computation(data)
    except Exception:  # noqa: BLE001 — derivation is enrichment, never a failure mode
        return None
    return None


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
    # IMP-15: the price chain may fall back (Yahoo → Stooq/KIS); when the response itself
    # names the upstream that actually served, cite THAT — never the static catalog label.
    if isinstance(data, dict):
        snap = data.get("snapshot")
        served = data.get("source") or (snap.get("source") if isinstance(snap, dict) else None)
        if isinstance(served, str) and served.strip():
            src = served
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
            # datasets Filing rows serialize `filing_date`/`report_date` — without them every
            # listing citation shipped as_of=None (empty freshness dot on the card).
            fa = (f.get("filed") or f.get("filing_date") or f.get("report_date")
                  or f.get("report_period") or f.get("as_of"))
            fa = str(fa)[:10] if fa else None
            form = f.get("form") or f.get("filing_type")
            # 8-K fix: a real summary (8-K event labels / doc description), not a bare form label —
            # so the card shows WHAT the filing reports and the viewer has a text target.
            desc = f.get("description") or f.get("title")
            snippet = desc or form or None
            # give the listing citation a filing-HTML evidence URL so the viewer renders the REAL
            # document in-app (not just the form). Highlight the first 8-K item header when known.
            accn = f.get("accession_number")
            cik = f.get("cik") or _cik_from_url(u)
            hl = _first_item_header(f.get("items"))
            row_market = market or _market_from_link(u, accn)
            ev = filing_evidence_url(row_market, accn, cik, hl) if accn else None
            out.append(Citation(
                tool=tool["name"], source=src, url=u, kind="filing", as_of=fa,
                freshness=compute_freshness(fa), page=accn, doc_type=form,
                snippet=snippet, evidence_image_url=ev))
        if out:
            return out
    # Derived figures (financials / metrics / prices): show the SPECIFIC figures used +
    # link to the exact filing they came from — not a label or a directory listing.
    as_of = _latest_date(data)
    url, accn, cik = _canonical_provenance(data)        # canonical filing link / accession
    market = market or _market_from_link(url, accn)     # recover from the row's own provenance
    if not url and accn:                                # build it from the identifier
        url = _filing_link(market, accn, cik)
    snippet, table = _evidence(tool, data)              # the real figures + extracted table
    return [Citation(tool=tool["name"], source=src, url=url, kind=ctype, as_of=as_of,
                     freshness=compute_freshness(as_of), snippet=snippet, table=table, page=accn,
                     evidence_image_url=_evidence_url(data, accn, cik, market),
                     computation=_derived_computation(tool, data))]


_MERGE_FIELDS = ("evidence_image_url", "computation", "table", "snippet",
                 "as_of", "freshness", "doc_type", "ticker", "page")


def merge_citation(survivor, incoming) -> None:
    """Fill the survivor's gaps from a duplicate — two tools citing the same document often
    carry complementary fields (one has the in-app evidence anchor, the other the summary).
    Dropping the duplicate must not drop its evidence. Works on Citation objects or dicts."""
    get = (lambda o, k: o.get(k)) if isinstance(survivor, dict) else getattr
    put = (lambda o, k, v: o.__setitem__(k, v)) if isinstance(survivor, dict) else setattr
    for k in _MERGE_FIELDS:
        if get(survivor, k) is None and get(incoming, k) is not None:
            put(survivor, k, get(incoming, k))


def citation_key(source, url, tool=None) -> tuple:
    """Dedup identity: (source, url); a url-less citation (derived figures) falls back to the
    tool name so two derived tools on the same connector don't collapse into one card."""
    return (source, url) if url else (source, tool)


def dedup_citations(cites: list[Citation]) -> list[Citation]:
    """Collapse repeats — the same (source, url) cited by several tool calls should appear once
    (fixes the '📎 OpenDART · 📎 OpenDART · …' repetition). Two collapses, both merging instead
    of dropping fields: the exact key, and the same url under a DIFFERENT source label (e.g.
    'SEC EDGAR' vs '공시 (SEC/DART)' for one 8-K — one card, all evidence kept)."""
    by_key: dict = {}
    by_url: dict = {}
    out: list[Citation] = []
    for c in cites:
        key = citation_key(c.source, c.url, c.tool)
        survivor = by_key.get(key) or (by_url.get(c.url) if c.url else None)
        if survivor is not None:
            merge_citation(survivor, c)
            continue
        by_key[key] = c
        if c.url:
            by_url[c.url] = c
        c.index = len(out) + 1  # 1-based [n] anchor
        out.append(c)
    return out
