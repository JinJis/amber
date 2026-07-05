"""QT-2 — the number audit (publish trust floor, PUBLISH_SPEC §5).

Deterministic post-answer check: every numeral in the prose must correspond to a value that
actually came back from a tool this turn. Pure data processing (extraction + tolerant matching),
not answer logic — the LLM never grades numbers here. The audit result rides the `done` event
(and the desk feed drops failing cards); M-SHARE will refuse to mint share cards for content
with unsupported numbers.

Matching is deliberately failure-averse in ONE direction only: a numeral is flagged only when
NO tool value matches it under its own stated unit/scale (%, 조/억/만, B/M/K) at display
rounding — false "unsupported" flags erode trust in the audit itself.
"""

from __future__ import annotations

import math
import re

# tokens like 2.81, +2.81%, -5, 1,234.5, $185.6, 15조, 6,623억, 2.00B — captured with their unit
_NUM_RE = re.compile(
    r"(?<![\w.])[+\-−]?\$?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>조|억|만|[BMK]\b|%|퍼센트|bp)?",
)
_SCALE = {"조": 1e12, "억": 1e8, "만": 1e4, "B": 1e9, "M": 1e6, "K": 1e3}

# numerals that are structure, not claims: [n] anchors, years/dates, list markers, 분기/차 ordinals
_DATEY = re.compile(r"\d{4}[-./년]\s?\d{1,2}([-./월]\s?\d{1,2}일?)?|\d{4}년|\d{1,2}월(\s?\d{1,2}일)?|\d{1,2}일\b")
_ANCHOR = re.compile(r"\[\d+(?:,\s*\d+)*\]")
_QUARTER = re.compile(r"\d[QØ분]|Q\d|\d분기|\d단계|\d위\b|\d개\b|\d가지|\d건\b|\d년|\d개월|\d주\b|\d거래일")


def extract_numbers(text: str) -> list[dict]:
    """Claim-bearing numerals in prose: [{raw, value, pct, span}]. Dates/years, [n] anchors, and
    count-ish ordinals (3가지, 2분기, 60건, 20거래일) are structure, not figures — skipped."""
    if not text:
        return []
    # replace [n] anchors with SAME-LENGTH whitespace so every span still indexes the
    # ORIGINAL text — the ledger (LG-1) hands these spans to the UI for prose highlighting.
    cleaned = _ANCHOR.sub(lambda m: " " * len(m.group(0)), text)
    out: list[dict] = []
    for m in _NUM_RE.finditer(cleaned):
        s, e = m.span()
        tail = cleaned[e:e + 4]
        ctx = cleaned[max(0, s - 1):e + 4]
        if _DATEY.match(cleaned[s:e + 3]) or _QUARTER.match(m.group("num")[-1] + tail):
            continue
        raw_num = m.group("num").replace(",", "")
        try:
            val = float(raw_num)
        except ValueError:
            continue
        unit = m.group("unit")
        # a bare 4-digit integer that reads as a year (1900–2099) with no unit → date-ish, skip
        if unit is None and val.is_integer() and 1900 <= val <= 2099 and "." not in raw_num:
            continue
        # count-ish suffixes right after the number (건/개/번/가지/명/곳/차례) → structure
        if re.match(r"\s?(건|개|번|가지|명|곳|차례|년|월|일|분기|위|단계|종목)", tail):
            continue
        pct = unit in ("%", "퍼센트")
        scaled = val * _SCALE.get(unit or "", 1.0)
        out.append({"raw": m.group(0).strip(), "value": scaled, "pct": pct, "span": (s, e),
                    "_unit": unit})
    return _merge_composites(out)


_UNIT_RANK = {"조": 3, "억": 2, "만": 1}


def _merge_composites(nums: list[dict]) -> list[dict]:
    """Korean composite numerals — "4,161억 6,100만" is ONE figure (416.161B), not two. Merge
    adjacent rows whose units strictly descend (조 > 억 > 만) with ≤1 char between spans; the
    merged row sums the values and spans the whole phrase. Without this the tail token becomes
    a false-amber "미확인" row in the ledger."""
    out: list[dict] = []
    i = 0
    while i < len(nums):
        cur = dict(nums[i])
        rank = _UNIT_RANK.get(cur.get("_unit") or "")
        j = i + 1
        while (rank and j < len(nums)):
            nxt = nums[j]
            nrank = _UNIT_RANK.get(nxt.get("_unit") or "")
            if not nrank or nrank >= rank or nxt["span"][0] - cur["span"][1] > 1 or nxt["pct"]:
                break
            cur["value"] += nxt["value"]
            cur["raw"] = f"{cur['raw']} {nxt['raw']}"
            cur["span"] = (cur["span"][0], nxt["span"][1])
            rank = nrank
            j += 1
        cur.pop("_unit", None)
        out.append(cur)
        i = j
    return out


def _walk_numbers(obj, pool: set[float], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        if math.isfinite(v):
            pool.add(v)
        return
    if isinstance(obj, str):
        for n in extract_numbers(obj):
            pool.add(n["value"])
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, pool, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj[:500]:
            _walk_numbers(v, pool, depth + 1)


def collect_pool(tool_results: list) -> set[float]:
    """Every numeric leaf from this turn's tool results (dicts/lists/numeric strings)."""
    pool: set[float] = set()
    for r in tool_results or []:
        _walk_numbers(r, pool)
    return pool


def _matches(x: float, pool: set[float], pct: bool) -> bool:
    """x matches a pool value at display rounding: the pool value ROUNDED to x's precision equals
    x, or relative diff ≤ 0.5%. A % numeral also tries its fraction (2.81% vs 0.0281) and its
    sign-agnostic form (낙폭 55% vs stored −55.0)."""
    cands = {x, -x}
    if pct:
        cands |= {x / 100.0, -x / 100.0}
    for c in cands:
        dp = 0
        s = f"{abs(c):.10f}".rstrip("0")
        if "." in s:
            dp = len(s.split(".")[1])
        for v in pool:
            if v == c:
                return True
            if abs(v) > 0 and abs(v - c) / abs(v) <= 0.005:
                return True
            try:
                if round(v, min(dp, 6)) == round(c, min(dp, 6)):
                    return True
            except (OverflowError, ValueError):
                continue
    return False


def audit_answer(answer: str, tool_results: list) -> dict:
    """The QT-2 audit: {checked, supported, unsupported: [raw…]} for the final prose."""
    nums = extract_numbers(answer)
    pool = collect_pool(tool_results)
    unsupported = [n["raw"] for n in nums if not _matches(n["value"], pool, n["pct"])]
    return {"checked": len(nums), "supported": len(nums) - len(unsupported),
            "unsupported": unsupported[:20]}


def audit_ledger(answer: str, attributed: list) -> dict:
    """LG-1 — the Figure Ledger: the QT-2 audit with per-numeral SOURCE ATTRIBUTION.

    ``attributed`` is an ordered list of ``(citation_index | None, data)`` pairs — the same
    tool payloads the plain audit sees, but each tagged with the 1-based [n] of the citation
    it backs (None for artifact payloads / unindexed sources). Every claim numeral in the
    prose becomes a ledger row::

        {raw, value, pct, span: [s, e], citation_idx: int | None, supported: bool}

    Attribution is first-match in citation order (the [n] the reader would check first).
    A numeral matched only by an unindexed pool still counts as supported (citation_idx None
    — rendered as "차트·표 데이터"); no match at all → supported=False (the amber row).
    The aggregate keys stay identical to ``audit_answer`` so every existing consumer
    (done event, desk-feed gate, share gate) keeps working unchanged.
    """
    nums = extract_numbers(answer)
    pools: list[tuple[int | None, set[float]]] = []
    for idx, data in attributed or []:
        pool: set[float] = set()
        _walk_numbers(data, pool)
        if pool:
            pools.append((idx if isinstance(idx, int) else None, pool))
    ledger: list[dict] = []
    unsupported: list[str] = []
    for n in nums:
        cit: int | None = None
        supported = False
        for idx, pool in pools:
            if _matches(n["value"], pool, n["pct"]):
                supported = True
                cit = idx
                if idx is not None:   # prefer a REAL [n]; keep scanning only while unindexed
                    break
        if not supported:
            unsupported.append(n["raw"])
        ledger.append({"raw": n["raw"], "value": n["value"], "pct": n["pct"],
                       "span": list(n["span"]), "citation_idx": cit, "supported": supported})
    return {"checked": len(nums), "supported": len(nums) - len(unsupported),
            "unsupported": unsupported[:20], "ledger": ledger[:60]}
