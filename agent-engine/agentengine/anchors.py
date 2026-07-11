"""Inline [n] source anchors + evidence marking (PH-4c).

Detects/places the bracketed [n] markers that keep every answer source-anchored,
and flags which citations actually *backed* the answer (evidence) vs were merely
consulted. Extracted from ``citations.py``; ``citations.py`` re-exports these and
agent.py imports has_anchors / anchor_markers / mark_evidence via that module.
"""

from __future__ import annotations

import re

from agentengine.models import Artifact, Citation

_ANCHOR_RE = re.compile(r"\[\d+\]")


def has_anchors(text: str | None) -> bool:
    """True if the prose already carries inline [n] markers (e.g. gemini wrote them)."""
    return bool(_ANCHOR_RE.search(text or ""))


def anchor_markers(indices) -> str:
    """Compact trailing anchor group: [1][2][3] (the deterministic floor when the
    model didn't place markers inline — keeps every answer source-anchored)."""
    return "".join(f"[{i}]" for i in indices if i)


def mark_evidence(cites: list[Citation], answer: str, artifacts: list[Artifact]) -> list[Citation]:
    """Flag which citations are *evidence* (actually backed the answer) vs merely
    consulted. Evidence = cited by [n] in the prose OR backs a rendered artifact.
    The Live Context shows only evidence; everything consulted stays in 도구·출처.
    When the model wrote no inline [n] at all, evidence falls back to the citations
    that actually returned data (a url / snippet / table) — never the bare labels."""
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer or "")}
    art_tools = {a.tool for a in artifacts if a.tool}
    for c in cites:
        c.used = (c.index in cited) or (c.tool in art_tools)
    if cites and not any(c.used for c in cites):
        data_bearing = [c for c in cites if c.url or c.snippet or c.table]
        for c in (data_bearing or cites):
            c.used = True
    return cites


# --- ANCHOR-NORM: 묶음 인용 정규화 ------------------------------------------------------------
# 모델이 [1,2]·[3, 4]·[3·4]·[2-5]처럼 묶음 마커를 쓰면 클라 링크화(단일 [n]만)와 used 마킹
# (\[(\d+)\])이 모두 놓쳐 생텍스트로 남는다. 결정적으로 [1][2]… 개별 마커로 펼친다.
# 3자리 숫자 제한이라 연도([2024, 2025])는 건드리지 않고, [텍스트](링크)도 매칭되지 않는다.
_GROUP_RE = re.compile(r"\[(\d{1,3}(?:\s*[,·\-–]\s*\d{1,3})+)\]")
_PARTIAL_RE = re.compile(r"\[[\d\s,·\-–]*$")


def _expand_group(nums: str) -> str:
    out: list[int] = []
    for part in re.split(r"[,·]", nums):
        part = part.strip()
        m = re.fullmatch(r"(\d{1,3})\s*[-–]\s*(\d{1,3})", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < b - a <= 10:          # [2-5] → 2,3,4,5 (비정상 범위는 양끝만)
                out.extend(range(a, b + 1))
            else:
                out.extend((a, b))
        elif part.isdigit():
            out.append(int(part))
    return "".join(f"[{n}]" for n in out)


def normalize_anchor_groups(text: str) -> str:
    """[1,2]·[3-5] → [1][2]·[3][4][5]. 단일 [n]·마크다운 링크·연도 표기는 그대로."""
    return _GROUP_RE.sub(lambda m: _expand_group(m.group(1)) or m.group(0), text or "")


class AnchorStream:
    """스트리밍용: 청크 경계에서 잘린 '[1,' 꼬리를 다음 청크까지 보류한 뒤 정규화해 방출 —
    클라이언트가 받는 토큰과 서버 final_text가 항상 동일·정규화 상태를 유지한다."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> str:
        s = self._buf + (chunk or "")
        m = _PARTIAL_RE.search(s)
        if m:
            safe, self._buf = s[: m.start()], s[m.start():]
        else:
            safe, self._buf = s, ""
        return normalize_anchor_groups(safe)

    def flush(self) -> str:
        out = normalize_anchor_groups(self._buf)
        self._buf = ""
        return out
