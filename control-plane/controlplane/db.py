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


def init_db() -> None:
    from controlplane import models  # noqa: F401

    Base.metadata.create_all(engine)
