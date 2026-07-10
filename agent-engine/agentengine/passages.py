"""EV-PASSAGE — real content in filing-listing evidence, not the report title.

A filings *index* tool (SEC 8-K 목록, DART 공시목록) returns metadata only, so its citations carried
the report TITLE as snippet — and the in-app viewer dutifully highlighted… the title ("주요사항보고서").
That's not evidence a reader can verify. This pass runs at done-time for the LISTING citations the
answer actually used: it queries the ingested filing text (RAG, through the gateway) with the
answer sentence that cited [n], accession-matches the hits, and swaps the snippet + highlight
target for the REAL passage. Deterministic plumbing (no LLM), best-effort, time-capped — on any
miss the citation keeps its current shape (never worse than before).
"""

from __future__ import annotations

import asyncio
import re

from agentengine.evidence import rag_evidence_url

_MD = re.compile(r"[*_`#>|]")


def _norm_accn(v) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()


def looks_like_title(snippet) -> bool:
    """A listing snippet is a report NAME (short); a real passage is prose. <80 chars ⇒ title-ish."""
    s = (str(snippet or "")).strip()
    return len(s) < 80


def sentence_for(answer: str | None, idx) -> str | None:
    """The answer sentence that cites [idx] — the best retrieval query for the passage the
    answer leaned on. Taken as the text window immediately BEFORE the [n] marker, cut at the
    previous sentence boundary (robust for Korean prose where 요/다 precede the marker).
    Markdown/markers stripped; None when the marker isn't in the prose."""
    if not answer or not idx:
        return None
    pos = answer.find(f"[{idx}]")
    if pos < 0:
        return None
    window = answer[max(0, pos - 260):pos]
    # start after the previous sentence-ending punctuation (keep the citing sentence only)
    m = list(re.finditer(r"[.!?\n]", window[:-2] if len(window) > 2 else ""))
    if m:
        window = window[m[-1].end():]
    t = re.sub(r"\{\{figure:\d+\}\}", " ", window)
    t = re.sub(r"\[\d+\]", " ", t)
    t = _MD.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" -•")
    return t[:300] if len(t) >= 12 else None


async def enrich_listing_passages(call_tool, rag_tool: dict, targets: list[tuple[dict, str]],
                                  answer: str | None, timeout: float = 3.0) -> None:
    """targets = [(citation_dict, market)] — mutates the citation dicts in place (they are the
    same objects serialized into the `done` event). Each lookup is independent + best-effort."""
    if not rag_tool or not targets:
        return

    async def one(cit: dict, market: str) -> None:
        accn = cit.get("page")   # listing citations carry the accession in `page`
        query = sentence_for(answer, cit.get("index")) or " ".join(
            str(x) for x in (cit.get("snippet"), cit.get("doc_type"), cit.get("source")) if x)[:200]
        if not accn or not query.strip():
            return
        try:
            res = await call_tool(rag_tool, {"query": query, "top_k": 10})
        except Exception:  # noqa: BLE001 — enrichment never fails the turn
            return
        data = res.get("data") if isinstance(res, dict) else None
        hits = (data or {}).get("hits") if isinstance(data, dict) else None
        want = _norm_accn(accn)
        for h in hits or []:
            prov = (h or {}).get("provenance") or {}
            if _norm_accn(prov.get("accession")) != want:
                continue
            text = str(h.get("text") or "").strip()
            body = re.sub(r"^\[[^\]]{1,80}\]\s*", "", text)   # drop a "[Item 1A …]" heading prefix
            if len(body) < 40:
                continue
            cit["snippet"] = body[:300]
            ev = rag_evidence_url(market, accn, body[:200])
            if ev:
                cit["evidence_image_url"] = ev
            return

    try:
        await asyncio.wait_for(
            asyncio.gather(*[one(c, m) for c, m in targets], return_exceptions=True), timeout)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 — partial enrichment is fine
        pass
