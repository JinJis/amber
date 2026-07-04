"""Studio store engine + session (SQLite default, Postgres via DATABASE_URL)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from studioapi.config import settings


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
    """Lightweight forward migration: ADD COLUMN for fields added to a model AFTER its table was first
    created (``create_all`` only creates missing TABLES, never columns on an existing one). Runs for
    BOTH real runtimes — SQLite (unit tests) and Postgres (compose) — since a long-lived Postgres DB
    hits exactly this gap when the schema evolves. Idempotent: skips columns already present, and the
    type/default decls are chosen per dialect."""
    dialect = engine.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    from sqlalchemy import inspect, text

    ts = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"      # SQLAlchemy DateTime → TIMESTAMP on PG
    bool_default = "false" if dialect == "postgresql" else "0"
    inspector = inspect(engine)
    names = inspector.get_table_names()
    if "pinned_artifacts" in names:
        existing = {c["name"] for c in inspector.get_columns("pinned_artifacts")}
        add = {"board_id": "VARCHAR(48)", "x": "INTEGER", "y": "INTEGER", "w": "INTEGER", "h": "INTEGER"}
        with engine.begin() as conn:
            for col, decl in add.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE pinned_artifacts ADD COLUMN {col} {decl}"))
    if "users" in names:
        ucols = {c["name"] for c in inspector.get_columns("users")}
        with engine.begin() as conn:
            # F1: onboarding flag on users (default = not yet onboarded)
            if "onboarded" not in ucols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN onboarded BOOLEAN DEFAULT {bool_default}"))
            # M-DESK: last visit timestamp for the desk feed's "since last visit" windows
            if "last_seen_at" not in ucols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN last_seen_at {ts}"))
    # IMP-13: share expiry on existing share_links tables
    if "share_links" in names:
        scols = {c["name"] for c in inspector.get_columns("share_links")}
        if "expires_at" not in scols:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE share_links ADD COLUMN expires_at {ts}"))


def init_db() -> None:
    from studioapi import models  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns()
