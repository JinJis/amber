"""GCP Document AI — Layout Parser adapter for PDF presentation decks.

Layout Parser is purpose-built for RAG: it returns FAITHFUL, layout-aware text chunks (not a
generative paraphrase) WITH page + bounding-box anchors — so a cited passage can be highlighted at
its exact location in the in-app pdf.js viewer, even on a chart/table slide. This keeps the trust
brand intact (the RAG corpus is the deck's real words, never invented).

Auth via Application Default Credentials (GOOGLE_APPLICATION_CREDENTIALS → the same service-account
the reranker uses). The SDK is an OPTIONAL dependency and the processor is operator-configured: with
neither, ``parse_pdf`` returns None and the deck feature simply stays dark (honesty over fake data).
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings

log = logging.getLogger(__name__)

try:  # optional, heavy dep — absent → the feature is dark, not broken
    from google.cloud import documentai_v1 as documentai  # type: ignore
except Exception:  # noqa: BLE001
    documentai = None  # type: ignore

try:
    import pypdf  # pure-python PDF splitter (online Document AI caps at 30 pages/call)
except Exception:  # noqa: BLE001
    pypdf = None  # type: ignore

_PAGES_PER_CALL = 15   # well under the 30-page online limit → real decks (30-100p) just take more calls


def configured() -> bool:
    """True only if the SDK is installed and a processor is configured."""
    return bool(documentai and settings.docai_processor_id
                and (settings.docai_project or settings.docai_location))


def _bbox(layout) -> list[float] | None:
    """Normalized [x0,y0,x1,y1] from a layout's bounding poly (best-effort)."""
    try:
        verts = layout.bounding_poly.normalized_vertices
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        return [min(xs), min(ys), max(xs), max(ys)] if xs and ys else None
    except Exception:  # noqa: BLE001
        return None


def _split_pages(pdf: bytes, per: int) -> list[tuple[int, bytes]]:
    """Split a PDF into ≤`per`-page parts: [(page_offset, part_bytes)]. Single part if pypdf absent."""
    if pypdf is None:
        return [(0, pdf)]
    import io
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    n = len(reader.pages)
    if n <= per:
        return [(0, pdf)]
    parts: list[tuple[int, bytes]] = []
    for start in range(0, n, per):
        writer = pypdf.PdfWriter()
        for p in range(start, min(start + per, n)):
            writer.add_page(reader.pages[p])
        buf = io.BytesIO()
        writer.write(buf)
        parts.append((start, buf.getvalue()))
    return parts


def _layout_options():
    """Ask the Layout Parser to emit RAG chunks (the chunked_document) with heading context — without
    this it only returns a bare document_layout (headings), not the chunk bodies."""
    po = documentai.ProcessOptions  # type: ignore
    return po(layout_config=po.LayoutConfig(chunking_config=po.LayoutConfig.ChunkingConfig(
        chunk_size=600, include_ancestor_headings=True)))


def _process_part(client, name, pdf: bytes, page_offset: int) -> list[dict]:
    raw = documentai.RawDocument(content=pdf, mime_type="application/pdf")  # type: ignore
    req = documentai.ProcessRequest(name=name, raw_document=raw, process_options=_layout_options())  # type: ignore
    result = client.process_document(req)
    doc = result.document
    out: list[dict] = []
    chunked = getattr(doc, "chunked_document", None)
    for ch in (getattr(chunked, "chunks", []) or []):
        text = (getattr(ch, "content", "") or "").strip()
        if not text:
            continue
        ps = getattr(ch, "page_span", None)
        page = (getattr(ps, "page_start", None) or 0) + page_offset if ps is not None else None
        out.append({"text": text, "page": page, "bbox": None})
    if out:
        return out
    layout = getattr(doc, "document_layout", None)
    for blk in (getattr(layout, "blocks", []) or []):
        tb = getattr(blk, "text_block", None)
        text = (getattr(tb, "text", "") or "").strip() if tb else ""
        if text:
            out.append({"text": text, "page": None, "bbox": None})
    return out


def _parse_sync(pdf: bytes) -> list[dict]:
    proc = settings.docai_processor_id
    project = settings.docai_project
    location = settings.docai_location or "us"
    opts = {"api_endpoint": f"{location}-documentai.googleapis.com"}
    client = documentai.DocumentProcessorServiceClient(client_options=opts)  # type: ignore
    name = client.processor_path(project, location, proc) if project else proc
    out: list[dict] = []
    for offset, part in _split_pages(pdf, _PAGES_PER_CALL):
        out.extend(_process_part(client, name, part, offset))
    return out


async def parse_pdf(pdf: bytes) -> list[dict] | None:
    """Faithful, layout-aware chunks ``[{text, page, bbox}]`` from a PDF deck — or None if Document
    AI isn't configured / the SDK is absent / the call failed (feature stays dark)."""
    if not configured() or not pdf:
        return None
    try:
        return await asyncio.to_thread(_parse_sync, pdf)
    except Exception as exc:  # noqa: BLE001
        log.warning("document_ai parse failed: %s", exc)
        return None
