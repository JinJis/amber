"""Structure-aware text chunking (RQ-2).

The old chunker packed raw characters: overlap sliced mid-word, oversized paragraphs were
window-split mid-sentence, tables shattered, and headings vanished into the flow. Retrieval
quality follows chunk quality, so this version:

* packs whole PARAGRAPHS, splitting oversized ones on SENTENCE boundaries (KR ``…다.`` and
  EN ``.!?`` enders) — a chunk never starts or ends mid-sentence unless a single sentence
  alone exceeds the budget (then a character window is the honest fallback);
* keeps TABLE rows atomic — a ``|``-delimited line (how the ingesters serialize HTML tables)
  is never split, and consecutive table rows stay in one chunk when they fit;
* carries the enclosing HEADING as a ``[heading]`` prefix on every chunk under it — "Item 1A.
  Risk Factors" context survives into each chunk so both embeddings and the lexical leg see it;
* overlap carries the last complete sentence(s) (≤ ``overlap`` chars), not a raw char slice.
"""

from __future__ import annotations

import re

_PARA = re.compile(r"\n\s*\n")
# sentence enders: EN terminators / KR 종결(다.) / CJK 。 — keep the delimiter with the sentence
_SENT = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)\s+")
# a heading: short single line, no terminal period, and either a known section marker
# (Item N / PART I / 제N장·절 / "1." enumerations) or Title-like (short + no sentence punct).
_HEADING_MARK = re.compile(
    r"^(item\s+\d|part\s+[ivx]+|제\s*\d+\s*[장절부]|\d{1,2}\.\s+\S|[IVX]+\.\s+\S)", re.IGNORECASE)


def _is_table_line(line: str) -> bool:
    return line.count(" | ") >= 1 or line.startswith("|")


def _is_heading(para: str) -> bool:
    if "\n" in para or len(para) > 90 or para.endswith((".", "다.", "!", "?")):
        return False
    return bool(_HEADING_MARK.match(para)) or (len(para) <= 60 and para.endswith(":"))


def _sentences(text: str) -> list[str]:
    return [s for s in (_SENT.split(text)) if s and s.strip()]


def _tail_sentences(text: str, overlap: int) -> str:
    """The last complete sentence(s) of ``text`` within ``overlap`` chars — a readable
    continuation context instead of a mid-word slice."""
    if overlap <= 0:
        return ""
    sents = _sentences(text)
    tail = ""
    for s in reversed(sents):
        candidate = f"{s} {tail}".strip() if tail else s
        if len(candidate) > overlap:
            break
        tail = candidate
    return tail


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    # 1) split into units: paragraphs, with headings tracked (not emitted alone) and table
    #    blocks split into atomic row-lines so packing can break BETWEEN rows only.
    units: list[tuple[str, str]] = []  # (heading_context, unit_text)
    heading = ""
    for para in (p.strip() for p in _PARA.split(text)):
        if not para:
            continue
        if _is_heading(para):
            heading = para.rstrip(":")
            continue
        lines = para.split("\n")
        if any(_is_table_line(ln) for ln in lines):
            for ln in lines:
                if ln.strip():
                    units.append((heading, ln.strip()))
        else:
            units.append((heading, para))

    # 2) oversized non-table units → sentence pieces (char-window only as a last resort)
    pieces: list[tuple[str, str]] = []
    for h, u in units:
        if len(u) <= size or _is_table_line(u):
            pieces.append((h, u))
            continue
        buf = ""
        for s in _sentences(u):
            while len(s) > size:  # a single monster sentence — honest char fallback
                pieces.append((h, s[:size]))
                s = s[size:]
            if buf and len(buf) + len(s) + 1 > size:
                pieces.append((h, buf))
                buf = s
            else:
                buf = f"{buf} {s}".strip() if buf else s
        if buf:
            pieces.append((h, buf))

    # 3) pack pieces into chunks; flush on size OR heading change; prefix the heading context.
    chunks: list[str] = []
    buf, buf_heading = "", ""

    def _flush() -> None:
        nonlocal buf
        if buf:
            chunks.append(f"[{buf_heading}]\n{buf}" if buf_heading else buf)
            buf = ""

    for h, piece in pieces:
        if buf and (h != buf_heading or len(buf) + len(piece) + 1 > size):
            carried = _tail_sentences(buf, overlap) if h == buf_heading else ""
            _flush()
            buf = f"{carried} {piece}".strip() if carried else piece
        else:
            buf = f"{buf}\n{piece}".strip() if buf and _is_table_line(piece) else \
                  (f"{buf} {piece}".strip() if buf else piece)
        buf_heading = h
    _flush()
    return chunks
