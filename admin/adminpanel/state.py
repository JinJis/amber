"""Reflected service-DB state for the admin DB browser (RF-14, split from main.py).

Reflects each configured service DB at import — table/column/pk metadata + a live engine + typed
``Table`` objects — into module-level registries the views read. Kept out of main.py so the request
handlers don't carry the stateful reflection layer, and so it's importable/testable on its own.
"""

from __future__ import annotations

from sqlalchemy import MetaData, create_engine, text

from adminpanel.config import DATABASES

DB_STATUS: dict[str, dict] = {}   # key -> {title, tables:[...], error, meta:{table->{columns,pk}}}
ENGINES: dict[str, object] = {}   # key -> sqlalchemy Engine
TABLES: dict[str, dict] = {}      # key -> {table_name -> sqlalchemy Table} (for typed CRUD)


def _mount_database(key: str, title: str, url: str) -> None:
    """Reflect a service DB: capture table/column/pk metadata + Table objects (typed
    CRUD) + the live engine, so the styled DB browser pages and edits rows directly."""
    try:
        engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        md = MetaData()
        md.reflect(bind=engine)
    except Exception as e:  # DB missing / unreachable — note it, keep the panel up
        DB_STATUS[key] = {"title": title, "tables": [], "error": str(e)[:200], "meta": {}}
        return

    ENGINES[key] = engine
    TABLES[key] = dict(md.tables)
    meta: dict[str, dict] = {}
    for tname, tbl in sorted(md.tables.items()):
        meta[tname] = {"columns": [c.name for c in tbl.columns],
                       "pk": [c.name for c in tbl.primary_key.columns]}
    DB_STATUS[key] = {"title": title, "tables": sorted(meta), "error": None, "meta": meta}


for _k, _t, _u in DATABASES:
    _mount_database(_k, _t, _u)


def _table_counts(key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    engine = ENGINES.get(key)
    meta = DB_STATUS.get(key, {}).get("meta", {})
    if engine is None:
        return out
    with engine.connect() as conn:
        for tname in meta:
            try:
                out[tname] = conn.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()
            except Exception:
                out[tname] = "?"
    return out


def _query(key: str, sql: str, params: dict | None = None) -> list:
    engine = ENGINES.get(key)
    if engine is None:
        return []
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()
    except Exception:
        return []


def _has(key: str, table: str) -> bool:
    return table in DB_STATUS.get(key, {}).get("meta", {})
