"""Ingestion store engine + session.

SQLite by default (zero-setup for dev); set ``DATABASE_URL`` to a ``postgresql://``
URL in production. The same schema serves both — this is the store that backs the
screener / line-items search and (later) bulk historical backfill.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


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
    if url.startswith("sqlite"):
        # Concurrent pipeline writers were hitting "database is locked". WAL lets readers run
        # alongside the single writer, and a long busy_timeout makes a write WAIT for the lock
        # instead of erroring. `timeout` is the driver-level busy timeout (seconds).
        eng = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30}, future=True)

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, future=True)


_ensure_database(settings.database_url)  # self-create the Postgres DB if missing (no init script needed)
engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    from app.store import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)
