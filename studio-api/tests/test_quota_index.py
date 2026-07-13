"""HI-6 / SC-1.8: the (user_email, day) and (user_email, month) composite indexes back every
per-turn quota query. This pins that init_db creates them (via create_all + the migration step).
The per-user advisory lock that serializes concurrent consumption is Postgres-only (a no-op on the
SQLite test DB, which serializes writes anyway), so it isn't exercised here."""

from __future__ import annotations

from sqlalchemy import inspect

from studioapi.db import engine, init_db


def test_turn_usage_composite_indexes_created():
    init_db()
    names = {i["name"] for i in inspect(engine).get_indexes("turn_usage")}
    assert "ix_turn_usage_user_day" in names
    assert "ix_turn_usage_user_month" in names
