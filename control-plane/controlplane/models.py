"""Control-plane ORM: projects (accounts) → API keys + activations + usage/audit.

A **project** is the account identity at the data plane — it owns keys and activations and carries the
plan/internal flags. An **activation** records that a project enabled a connector from the data-plane
catalog, the entitlement the gateway checks on every request. (The old Tenant parent was removed: this
is a 1-account-per-user B2C product, so Tenant was always 1:1 with Project and carried no logic — the
gateway, metering, audit and RAG isolation all key on `project_id`. See ROADMAP SIMPL-1.)
"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from controlplane.db import Base


def _uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class Project(Base):
    __tablename__ = "projects"
    # PROV-1: `owner_ref` is the caller's stable identity for this account (studio sends the user's
    # email; "system"/"guest" for the platform singletons) and is UNIQUE — it is what makes
    # POST /admin/provision idempotent, so concurrent first-requests for the same owner converge on
    # ONE project instead of each minting their own. NULL is allowed (and repeatable: both dialects
    # treat NULLs as distinct in a unique index) so the older un-owned create paths — ops scripts,
    # scripts/e2e.sh, coverage.sh — keep working untouched.
    __table_args__ = (Index("ix_projects_owner_ref", "owner_ref", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("prj"))
    # A human-facing label for the account — studio sets it to the owner's email (was Tenant.name).
    name: Mapped[str] = mapped_column(String(128))
    owner_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # PLAN-2: the product plan tier (guest|free|pro), set by studio's apply_plan. Drives the
    # per-key gateway rate limit (abuse backstop). NULL = legacy/ops project → global default.
    plan: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # SYS-1: an INTERNAL project is the platform's own infrastructure (the background feed pipelines),
    # NOT a commercial tenant. The gateway exempts it from the entitlement (activation) check — it is
    # entitled to the whole governed catalog by definition — while STILL metering/rate-limiting/auditing
    # it. Set server-side only (admin API); a user can never mark their own project internal. Keeping
    # this SEPARATE from `plan` is deliberate: commercial gating (per-user, per-plan activations) and
    # "this is platform infra that just runs" are different concepts and must not be conflated.
    internal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # PROV-1: the highest fence a key rotation has been accepted at. Rotation retires the previous
    # secret, so a LATE call from a provisioner that has already been superseded would kill the key its
    # successor is using. Callers that can be superseded pass a monotonically increasing fence; a
    # rotation carrying a lower one is refused instead of applied. NULL = no rotation fenced yet.
    key_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    # PROV-1: one row per (project, connector). The select-then-insert in admin.activate() is not
    # atomic, so concurrent activations of the same connector used to duplicate rows silently; the DB
    # is the arbiter now and the endpoint converges on the IntegrityError.
    __table_args__ = (UniqueConstraint("project_id", "connector_id", name="uq_activations_project_connector"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: _uid("act"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    connector_id: Mapped[str] = mapped_column(String(64), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Optional BYO upstream credentials (JSON string) for restricted connectors.
    byo_credentials: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"
    # HI-13: the settings usage aggregate filters by project_id; retention drops by ts. A composite
    # (project_id, ts) serves the per-project scan (and its project_id prefix replaces the old single
    # index) and a ts index serves the retention delete.
    __table_args__ = (
        Index("ix_usage_events_project_ts", "project_id", "ts"),
        Index("ix_usage_events_ts", "ts"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column()
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
    # COST-2: usage_metadata breakdowns (subsets of input_tokens/output_tokens, for the dashboard —
    # NOT re-added to the totals). cached_input = context-cache hits (priced at the cache discount);
    # tool_input = function-calling prompt tokens; thinking = thoughts (already inside output_tokens).
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # METER-1: which tenant project this call served — per-user cost attribution (unit economics).
    # NULL = shared/background work (feeds, ops) or a pre-attribution row.
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class ProviderUsage(Base):
    """COST-3: background-sweep upstream call counts per provider (Yahoo/SEC/DART/news…). That sweep
    layer bypasses the gateway, so these calls were invisible to the cost dashboard; datasets batches
    per-provider counts and POSTs them here (opt-in). One row per flush; the dashboard sums them."""

    __tablename__ = "provider_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(48), index=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_ts", "ts"),)   # HI-13: retention drop scan
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(nullable=True)
    api_key_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # access | denied | admin
    detail: Mapped[str] = mapped_column(String(512))
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageRollup(Base):
    """HI-13: cumulative per-project totals for usage_events that retention has DROPPED — so the
    settings usage aggregate still reports lifetime calls/cost after the raw rows age out. Written by
    the retention job (SUM of the rows it deletes), read by the /usage aggregate alongside live rows."""

    __tablename__ = "usage_rollup"
    project_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_units: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
