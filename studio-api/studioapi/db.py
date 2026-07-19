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
    kwargs: dict = dict(connect_args=connect_args, future=True)
    if url.startswith("postgresql"):
        # HI-1: pre-ping + recycle + a sized pool. The default QueuePool (5+10=15) with pre_ping off
        # raises on a stale/recycled connection and exhausts under concurrency (worsened by sessions
        # held across LLM/gateway I/O). SQLite (unit tests) keeps its default.
        kwargs.update(pool_pre_ping=True, pool_recycle=settings.db_pool_recycle_seconds,
                      pool_size=settings.db_pool_size, max_overflow=settings.db_pool_max_overflow)
    return create_engine(url, **kwargs)


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
    names = set(inspector.get_table_names())

    def add_cols(table: str, cols: dict[str, str]) -> None:
        """ADD COLUMN each missing column of ``table`` (skip absent table / present columns)."""
        if table not in names:
            return
        have = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for col, decl in cols.items():
                if col not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {decl}"))

    add_cols("pinned_artifacts",
             {"board_id": "VARCHAR(48)", "x": "INTEGER", "y": "INTEGER", "w": "INTEGER", "h": "INTEGER"})
    add_cols("users", {  # F1 onboarding flag · M-DESK last-visit window
        "onboarded": f"BOOLEAN DEFAULT {bool_default}", "last_seen_at": ts,
        # PLAN-1/REF-1/AUTH-4: 플랜 게이팅 + 레퍼럴 + 메일 게이트 (기존 유저는 verified 취급 —
        # 지금까지의 가입 경로는 전부 구글 OAuth라 이메일이 실재한다)
        "plan_updated_at": ts, "bonus_daily_turns": "INTEGER DEFAULT 0", "bonus_turns_until": ts,
        "referral_code": "VARCHAR(16)", "referred_by": "VARCHAR(256)",
        "connectors_reconciled_ver": "VARCHAR(32)",   # ME-2: persistent reconcile version
        "email_verified": ("BOOLEAN DEFAULT true" if dialect == "postgresql" else "BOOLEAN DEFAULT 1")})
    add_cols("messages", {"artifacts": "TEXT", "audit": "TEXT",   # inline figures + number audit
                          "suggestions": "TEXT",                  # 더 파고들기 chips survive reload
                          # V-7 공유 훅 — chat.py의 assistant INSERT가 이 컬럼을 쓰므로, 빠져 있으면
                          # 오래된 DB에서 답변 저장(인용·아티팩트 영속화)이 통째로 실패한다.
                          "hook": "VARCHAR(160)"})
    add_cols("share_links", {"expires_at": ts, "og_image": "TEXT"})  # IMP-13 expiry · SH-2b OG image
    add_cols("card_taps", {"qhash": "VARCHAR(16)"})   # RC-2: 카드 단위 인기 집계(홈 보드 랭킹)


def _add_missing_indexes() -> None:
    """HI-6: create_all only adds indexes to NEW tables — a long-lived Postgres DB whose turn_usage
    predates the composite indexes needs an explicit CREATE INDEX IF NOT EXISTS. Idempotent on both
    dialects; best-effort (never blocks boot)."""
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return
    from sqlalchemy import text

    stmts = (
        "CREATE INDEX IF NOT EXISTS ix_turn_usage_user_day ON turn_usage (user_email, day)",
        "CREATE INDEX IF NOT EXISTS ix_turn_usage_user_month ON turn_usage (user_email, month)",
    )
    for s in stmts:
        try:
            with engine.begin() as conn:
                conn.execute(text(s))
        except Exception:  # noqa: BLE001 — table may not exist yet / build races; never block boot
            pass


from contextlib import contextmanager


@contextmanager
def boot_lock():
    """ME-3: serialize concurrent replica boots so create_all / seed / ALTER / CREATE INDEX can't
    IntegrityError-crash-loop when replicas start simultaneously. A blocking pg session advisory lock
    (the waiter re-runs the idempotent init after the first finishes); a no-op on SQLite (1 process)."""
    from sqlalchemy import text

    is_pg = engine.dialect.name == "postgresql"
    if not is_pg:
        yield
        return
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": 0x7667424B})  # 'vgBK'
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": 0x7667424B})
            conn.commit()


def init_db() -> None:
    from studioapi import models  # noqa: F401

    Base.metadata.create_all(engine)
    _add_missing_columns()
    _add_missing_indexes()
