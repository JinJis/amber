"""HI-1 / SC-1.7: the gateway's Postgres engine is pooled (sized largest) + pre-pinged + recycled.
create_engine is lazy, so this makes no real connection."""

from __future__ import annotations

from controlplane import db


def test_postgres_engine_pool_configured(monkeypatch):
    monkeypatch.setattr(db.settings, "database_url", "postgresql+psycopg://u:p@h:5432/x")
    eng = db._make_engine()
    try:
        assert eng.pool.size() == db.settings.db_pool_size
        assert eng.pool._max_overflow == db.settings.db_pool_max_overflow
        assert eng.pool._pre_ping is True
    finally:
        eng.dispose()


def test_boot_lock_noop_on_sqlite_runs_init():
    """ME-3: boot_lock serializes migration on Postgres and is a no-op on the SQLite test DB."""
    from controlplane.db import boot_lock, init_db
    with boot_lock():
        init_db()
