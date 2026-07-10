"""Control-plane ORM: tenants → projects → API keys + activations + usage/audit.

A **project** is the unit of activation and keys. A tenant owns projects. An
**activation** records that a project enabled a connector from the data-plane
catalog — that is the entitlement the gateway checks on every request.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.db import Base


def _uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("ten"))
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("prj"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("key"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    prefix: Mapped[str] = mapped_column(String(16), index=True)  # lookup handle
    key_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the full key
    scopes: Mapped[str] = mapped_column(String(256), default="read")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Activation(Base):
    __tablename__ = "activations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("act"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    connector_id: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional BYO upstream credentials (JSON string) for restricted connectors.
    byo_credentials: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(index=True)
    api_key_id: Mapped[str] = mapped_column(String(40))
    connector_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(256))
    status: Mapped[int] = mapped_column(Integer)
    cost_units: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LlmUsage(Base):
    """COST-1: every Gemini call (chat plan/synthesis, feeds, enrichment, RAG embeddings) reports
    its token usage here — the admin cost dashboard prices these rows with the pricing registry.
    `estimated` marks rows whose tokens were approximated (e.g. embeddings — the API returns no
    usage metadata), so the dashboard can label them honestly."""

    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(24), index=True)   # agent-engine | rag | studio-api
    kind: Mapped[str] = mapped_column(String(32), index=True)      # plan|synthesis|intake|askfeed|…|embed
    model: Mapped[str] = mapped_column(String(64), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=1)
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(nullable=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # access | denied | admin
    detail: Mapped[str] = mapped_column(String(512))
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
