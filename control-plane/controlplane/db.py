"""Control-plane store engine + session (SQLite default, Postgres via DATABASE_URL)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from controlplane.config import settings


class Base(DeclarativeBase):
    pass


def _ensure_database(url: str) -> None:
    """Create the target Postgres database if it doesn't exist yet (idempotent). No-op for SQLite.
    Lets the stack come up on a fresh Postgres with NO host-mounted init script — robust across
    hosts (no bind-mount permission/SELinux gotchas). Connects to the always-present ``postgres``
    maintenance DB to issue ``CREATE DATABASE``; retries while Postgres is still coming up."""
    if not url.startswith("postgresql"):
        return
    import time

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    target = make_url(url)
    admin_url = target.set(database="postgres")
    last: Exception | None = None
    for attempt in range(8):
        try:
            admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
            with admin.connect() as conn:
                if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"),
                                    {"n": target.database}).scalar():
                    conn.execute(text(f'CREATE DATABASE "{target.database}"'))
            admin.dispose()
            return
        except Exception as exc:  # noqa: BLE001 — Postgres may still be starting; back off and retry
            last = exc
            time.sleep(min(1.0 * (attempt + 1), 5.0))
    raise RuntimeError(f"could not ensure database {target.database!r} exists: {last}")


def _make_engine():
    url = settings.database_url
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


_ensure_database(settings.database_url)  # self-create the Postgres DB if missing (no init script needed)
engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _add_missing_columns() -> None:
    """Lightweight forward migration (ported from studio-api): ADD COLUMN for fields added to a
    model AFTER its table was first created — ``create_all`` never alters existing tables.
    Idempotent; runs for both SQLite (unit tests) and long-lived Postgres (compose)."""
    dialect = engine.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    names = set(inspector.get_table_names())

    def add_cols(table: str, cols: dict[str, str]) -> None:
        if table not in names:
            return
        have = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for col, decl in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))

    add_cols("projects", {"plan": "VARCHAR(24)"})            # PLAN-2: per-plan gateway rate tier
    add_cols("llm_usage", {"project_id": "VARCHAR(40)"})     # METER-1: per-user cost attribution


def init_db() -> None:
    from controlplane import models  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns()
