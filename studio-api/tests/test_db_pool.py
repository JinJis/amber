"""HI-1 / SC-1.7: the Postgres engine is pooled + pre-pinged + recycled (the SQLite unit-test DB
keeps its default). create_engine is lazy, so this makes no real connection."""

from __future__ import annotations

from studioapi import db


def test_postgres_engine_pool_configured(monkeypatch):
    monkeypatch.setattr(db.settings, "database_url", "postgresql+psycopg://u:p@h:5432/x")
    eng = db._make_engine()
    try:
        assert eng.pool.size() == db.settings.db_pool_size
        assert eng.pool._max_overflow == db.settings.db_pool_max_overflow
        assert eng.pool._pre_ping is True
    finally:
        eng.dispose()
