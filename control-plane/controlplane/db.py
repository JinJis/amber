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
    kwargs: dict = dict(connect_args=connect_args, future=True)
    if url.startswith("postgresql"):
        # HI-1: pre-ping + recycle + a sized pool. This DB backs the gateway (every entitlement/
        # meter/audit), so the default 5+10=15 cap with pre_ping off is the tightest bottleneck under
        # load. SQLite (unit tests) keeps its default.
        kwargs.update(pool_pre_ping=True, pool_recycle=settings.db_pool_recycle_seconds,
                      pool_size=settings.db_pool_size, max_overflow=settings.db_pool_max_overflow)
    return create_engine(url, **kwargs)


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
    add_cols("projects", {"internal": "BOOLEAN DEFAULT FALSE"})   # SYS-1: internal/system project class
    add_cols("projects", {"owner_ref": "VARCHAR(256)"})      # PROV-1: idempotency key for /admin/provision
    add_cols("llm_usage", {"project_id": "VARCHAR(40)"})     # METER-1: per-user cost attribution
    add_cols("llm_usage", {                                  # COST-2: usage_metadata breakdowns
        "cached_input_tokens": "INTEGER DEFAULT 0",
        "tool_input_tokens": "INTEGER DEFAULT 0",
        "thinking_tokens": "INTEGER DEFAULT 0"})


from contextlib import contextmanager


@contextmanager
def boot_lock():
    """ME-3: serialize concurrent replica boots so create_all / ALTER can't crash-loop when replicas
    start simultaneously. A blocking pg session advisory lock; a no-op on SQLite (single process)."""
    from sqlalchemy import text

    if engine.dialect.name != "postgresql":
        yield
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": 0x7667434B})  # 'vgCK'
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 0x7667434B})
            conn.commit()


def _add_missing_indexes() -> None:
    """HI-13: create_all only indexes NEW tables — a long-lived usage_events/audit_log predates the
    composite/ts indexes and needs an explicit CREATE INDEX IF NOT EXISTS. Idempotent both dialects;
    best-effort (never blocks boot)."""
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return
    from sqlalchemy import text

    stmts = (
        "CREATE INDEX IF NOT EXISTS ix_usage_events_project_ts ON usage_events (project_id, ts)",
        "CREATE INDEX IF NOT EXISTS ix_usage_events_ts ON usage_events (ts)",
        "CREATE INDEX IF NOT EXISTS ix_audit_log_ts ON audit_log (ts)",
        # PROV-1: the idempotency guarantee of /admin/provision — get-or-create by owner_ref is only
        # race-proof because the DB refuses a second row. A pre-existing projects table gets it here
        # (create_all only indexes NEW tables). Safe to build: owner_ref starts all-NULL and NULLs are
        # distinct in a unique index on both SQLite and Postgres.
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_projects_owner_ref ON projects (owner_ref)",
        # PROV-1: same for one-activation-per-(project, connector).
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_activations_project_connector ON activations (project_id, connector_id)",
    )
    for s in stmts:
        try:
            with engine.begin() as conn:
                conn.execute(text(s))
        except Exception as exc:  # noqa: BLE001 — table may not exist yet / build races; never block boot
            if "UNIQUE" in s:
                # PROV-1: a UNIQUE index that fails to build is not cosmetic — it is the arbiter that
                # /admin/provision's idempotency rests on, and without it concurrent provisioning can
                # split an account again. The usual cause is duplicate rows left by an older build, so
                # say so loudly instead of leaving a silent gap. (A fresh DB gets it from create_all.)
                import logging

                logging.getLogger("controlplane.db").error(
                    "PROV-1: could not create %s — provisioning idempotency is NOT enforced on this "
                    "database (de-duplicate the table, then restart): %s", s.split(" ON ")[0], exc)


def _drop_deprecated_tenancy() -> None:
    """SIMPL-1: the Tenant parent was removed (1-account-per-user product — it was always 1:1 with
    Project and carried no logic). On a long-lived DB that predates this, `projects.tenant_id` is NOT
    NULL with no default, so ORM inserts that no longer supply it would fail — drop the column, then the
    now-orphan `tenants` table. Idempotent + best-effort (a fresh DB never has them → no-op)."""
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    try:
        if "projects" in tables and "tenant_id" in {c["name"] for c in inspector.get_columns("projects")}:
            with engine.begin() as conn:
                # SQLite refuses DROP COLUMN while an index references it — drop the index first (a
                # no-op on PG, where DROP COLUMN cascades to its own index/FK).
                conn.execute(text("DROP INDEX IF EXISTS ix_projects_tenant_id"))
                conn.execute(text("ALTER TABLE projects DROP COLUMN tenant_id"))
        if "tenants" in tables:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS tenants"))
    except Exception:  # noqa: BLE001 — never block boot on a cleanup migration
        pass


def init_db() -> None:
    from controlplane import models  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns()
    _drop_deprecated_tenancy()
    _add_missing_indexes()
