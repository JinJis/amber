"""RAG data models. Chunks carry the platform trust envelope (provenance)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Provenance fields a chunk/doc may carry (the trust envelope for unstructured data).
# Single source of truth — the store imports this to rebuild Chunk provenance on read (RF-03).
PROVENANCE_FIELDS = ("source", "doc_type", "ticker", "market", "as_of", "url", "section", "accession")


class IngestDoc(BaseModel):
    text: str
    doc_id: str | None = None
    source: str | None = Field(None, description="e.g. 'SEC EDGAR', 'OpenDART', 'Google News'.")
    doc_type: str | None = None
    ticker: str | None = None
    market: str | None = None
    as_of: str | None = None
    url: str | None = None
    section: str | None = None
    accession: str | None = None
    # Owning tenant (control-plane project_id). Normally stamped by the gateway from
    # the caller's key, not set by the client. None = unscoped/global (dev/admin).
    tenant: str | None = None


class Chunk(BaseModel):
    id: str
    text: str
    source: str | None = None
    doc_type: str | None = None
    ticker: str | None = None
    market: str | None = None
    as_of: str | None = None
    url: str | None = None
    section: str | None = None
    accession: str | None = None
    tenant: str | None = None  # isolation dimension; deliberately NOT in provenance()

    def provenance(self) -> dict:
        # tenant is intentionally excluded — it's an isolation key, not user-facing provenance.
        return {k: getattr(self, k) for k in PROVENANCE_FIELDS if getattr(self, k) is not None}


class IngestRequest(BaseModel):
    documents: list[IngestDoc]


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = None
    ticker: str | None = None
    market: str | None = None


class SearchHit(BaseModel):
    text: str
    score: float
    provenance: dict


def doc_to_chunks(doc: IngestDoc, texts: list[str]) -> list[Chunk]:
    # Namespace the id by tenant so two tenants ingesting the same text don't collide
    # on the vector store's primary key (which would clobber one tenant's chunk).
    base = doc.doc_id or str(abs(hash(doc.text)) % (10**10))
    base = f"{doc.tenant}::{base}" if doc.tenant else base
    return [
        Chunk(
            id=f"{base}::{i}", text=t, source=doc.source, doc_type=doc.doc_type, ticker=doc.ticker,
            market=doc.market, as_of=doc.as_of, url=doc.url, section=doc.section, accession=doc.accession,
            tenant=doc.tenant,
        )
        for i, t in enumerate(texts)
    ]
