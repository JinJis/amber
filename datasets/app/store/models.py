"""ORM models for the ingestion store.

``FinancialFact`` is point-in-time / restatement-aware: the same (ticker, line
item, period, report_period) can exist under different ``accession_number``s, so
an originally-reported value and a later restatement are both retained rather
than overwritten (CLAUDE.md: reconcile, don't overwrite). ``accession_number``
is stored as "" (not NULL) when absent so the uniqueness constraint holds.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.store.db import Base


class FinancialFact(Base):
    __tablename__ = "financial_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(2), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    statement: Mapped[str] = mapped_column(String(16))  # income | balance | cashflow
    line_item: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    period: Mapped[str] = mapped_column(String(10))  # annual | quarterly | ttm
    report_period: Mapped[date] = mapped_column(index=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filing_date: Mapped[date | None] = mapped_column(nullable=True)
    accession_number: Mapped[str] = mapped_column(String(40), default="")
    source: Mapped[str] = mapped_column(String(24))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "market", "ticker", "statement", "line_item", "period", "report_period",
            "accession_number", name="uq_financial_fact",
        ),
        Index("ix_fact_screen", "line_item", "period", "report_period"),
        Index("ix_fact_lookup", "market", "ticker", "line_item", "period"),
    )


class IngestionJob(Base):
    """One ingestion run (manual backfill or a scheduled refresh) — so operators can
    see what was loaded, when, how many rows, and any error, instead of guessing
    why the store is empty. Surfaced in the admin ops console (PH-1)."""

    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # backfill | scheduled
    market: Mapped[str | None] = mapped_column(String(2), nullable=True)
    spec: Mapped[str | None] = mapped_column(String(256), nullable=True)  # tickers / universe / "deep"
    status: Mapped[str] = mapped_column(String(12), index=True)  # running | success | error
    rows: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)  # tickers to process
    done: Mapped[int] = mapped_column(Integer, default=0)   # tickers processed (live progress)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PipelineActivity(Base):
    """A live, granular activity feed for pipeline runs (what's being fetched, from where, with what
    result) — the worker writes milestone lines here as it works, so the admin can show in real time
    what a running pipeline is doing instead of only a status/progress bar. Capped (old rows trimmed).
    Worker and datasets share this DB, so the admin (which talks to datasets) sees worker activity."""

    __tablename__ = "pipeline_activity"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)  # IngestionJob id
    kind: Mapped[str] = mapped_column(String(16), index=True)   # filing_text | prices | news | …
    market: Mapped[str | None] = mapped_column(String(2), nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warn | error
    message: Mapped[str] = mapped_column(String(512))
    at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class PriceBar(Base):
    """One end-of-day OHLCV bar (PH-PIPE). Collected periodically by the prices pipeline so
    market data accumulates in the store (served on-demand today; cached/offline serving later)."""

    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(2), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    interval: Mapped[str] = mapped_column(String(8), default="day")  # day | week | month
    bar_date: Mapped[date] = mapped_column(index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(24))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("market", "ticker", "interval", "bar_date", name="uq_price_bar"),
        Index("ix_price_lookup", "market", "ticker", "interval", "bar_date"),
    )


class CorporateAction(Base):
    """A dividend or split event (PH-PIPE). Collected periodically so dividend/split history
    accumulates in the store. ``kind`` = dividend | split; the relevant fields are populated
    per kind (dividend → amount; split → ratio/numerator/denominator)."""

    __tablename__ = "corporate_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(2), index=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # dividend | split
    event_date: Mapped[date] = mapped_column(index=True)  # ex-date (dividend) / effective (split)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)     # dividend per share
    ratio: Mapped[str | None] = mapped_column(String(24), nullable=True)   # e.g. "4:1"
    source: Mapped[str] = mapped_column(String(24))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("market", "ticker", "kind", "event_date", name="uq_corp_action"),
    )


class Company(Base):
    __tablename__ = "companies"

    market: Mapped[str] = mapped_column(String(2), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    cik: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
