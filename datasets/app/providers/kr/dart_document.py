"""KR DART disclosure document fetch — OpenDART ``document.xml`` API → combined HTML markup.

This is the KR filing's original markup: the in-app viewer renders it and the filing-text RAG
ingest extracts its text (see ``app/store/filing_html.py`` + ``app/store/filing_ingest.py``). One
사업보고서 ZIP bundles the main body **plus separate files** (e.g. the audited financial statements /
감사보고서), so we concatenate every document file into one tree.
"""

from __future__ import annotations

import io
import re
import zipfile

from app.config import settings
from app.http import fetch_bytes

_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def _decode_doc(raw: bytes) -> str:
    """Decode DART document bytes (EUC-KR/CP949 historically; UTF-8 newer)."""
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def fetch_document_markup(rcept_no: str) -> str | None:
    """Download the DART disclosure document (``document.xml`` API → ZIP of markup) for a
    receipt number and return its combined markup. None on any failure (no key, network, bad
    zip) → the viewer degrades to the external source link."""
    if not settings.opendart_api_key or not rcept_no:
        return None
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={settings.opendart_api_key}&rcept_no={rcept_no}"
    try:
        blob = await fetch_bytes("opendart", url)
    except Exception:  # noqa: BLE001 — upstream/network → graceful (None)
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        return None
    parts: list[str] = []
    with zf:
        for name in zf.namelist():
            if not name.lower().endswith((".xml", ".html", ".htm")):
                continue
            try:
                text = _decode_doc(zf.read(name))
            except (KeyError, OSError):
                continue
            text = _XML_DECL.sub("", text.lstrip())  # drop a leading <?xml …?> so it nests cleanly
            if text.strip():
                parts.append(text)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "<html><body>" + "\n".join(parts) + "</body></html>"
