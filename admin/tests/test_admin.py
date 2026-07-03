"""Admin console tests — build throwaway service DBs, drive the panel over HTTP.

DB urls are set via env BEFORE importing the app (reflection runs at import). Ops
calls to other services fail gracefully (no stack needed), so pages still render.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

_TMP = tempfile.mkdtemp()


def _make_db(path: str, ddl: str) -> None:
    c = sqlite3.connect(path)
    c.executescript(ddl)
    c.commit()
    c.close()


_CP = f"{_TMP}/controlplane.db"
_ST = f"{_TMP}/studio.db"
_DS = f"{_TMP}/datasets.db"
_make_db(_CP, "create table tenants(id text primary key, name text);"
              "insert into tenants values('ten_1','Acme Capital');"
              "create table api_keys(id text primary key, project_id text, prefix text);"
              "insert into api_keys values('k1','prj_1','vgk_aa');")
_make_db(_ST, "create table users(email text primary key, tenant_id text, api_key text);"
              "insert into users values('a@b.com','ten_1','vgk_x');"
              "create table agents(id text primary key, user_email text, name text);")
_make_db(_DS, "create table financial_facts(id integer primary key, ticker text, value real);"
              "insert into financial_facts values(1,'AAPL',391000000000.0);")

os.environ["CONTROLPLANE_DB"] = f"sqlite:///{_CP}"
os.environ["STUDIO_DB"] = f"sqlite:///{_ST}"
os.environ["DATASETS_DB"] = f"sqlite:///{_DS}"
os.environ["ADMINUI_USERNAME"] = "admin"
os.environ["ADMINUI_PASSWORD"] = "secret"

from fastapi.testclient import TestClient  # noqa: E402

from adminpanel.main import DB_STATUS, app  # noqa: E402

client = TestClient(app)


def _login():
    client.post("/login", data={"username": "admin", "password": "secret"})


# --- reflection + auth ----------------------------------------------------
def test_reflection_found_every_table():
    assert set(DB_STATUS) == {"controlplane", "studio", "datasets"}
    assert "tenants" in DB_STATUS["controlplane"]["tables"]
    assert "users" in DB_STATUS["studio"]["tables"]
    assert "financial_facts" in DB_STATUS["datasets"]["tables"]
    assert all(v["error"] is None for v in DB_STATUS.values())


def test_auth_required_then_login():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    # every console page is gated
    assert client.get("/catalog", follow_redirects=False).status_code == 302
    assert client.get("/db/controlplane/tenants", follow_redirects=False).status_code == 302
    assert client.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401
    r = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_healthz_open():
    assert client.get("/healthz").json() == {"status": "ok"}


# --- console pages (services down → graceful) -----------------------------
def test_overview_renders_with_nav_and_health():
    _login()
    r = client.get("/")
    assert r.status_code == 200
    assert "Overview" in r.text and "Subsystem health" in r.text
    for label in ("Catalog", "Pipelines", "Data", "Users", "DB browser"):
        assert label in r.text
    assert "Control plane" in r.text and "Data plane" in r.text
    # refresh is operator-controlled now: a top-bar control, NOT a forced meta-refresh
    assert "http-equiv" not in r.text and 'id=rauto' in r.text


def test_overview_queue_reachable_reads_healthy(monkeypatch):
    # The Overview's queue subsystem reads healthy when /admin/queue comes back with its job DB
    # reachable (no 'error' field). Replaces the old scheduler-state health check.
    import httpx as _httpx
    _login()

    class _Resp:
        def __init__(self, data):
            self._d, self.status_code, self.headers = data, 200, {"content-type": "application/json"}

        def json(self):
            return self._d

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, timeout=None):
            if url.endswith("/admin/queue"):
                return _Resp({"totals": {"todo": 1, "doing": 0, "succeeded": 5, "failed": 0},
                              "periodic": [], "tasks": ["run_pipeline"]})
            return _Resp({})

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    r = client.get("/")
    assert r.status_code == 200
    assert "Queue (Procrastinate)" in r.text
    assert "unreachable / down" not in r.text   # every subsystem (incl. the queue) reads healthy


def test_catalog_page_renders_without_services():
    _login()
    r = client.get("/catalog")
    assert r.status_code == 200
    assert "unreachable" in r.text.lower()             # gateway down → explicit warning


def test_pipelines_page_and_triggers():
    _login()
    r = client.get("/pipelines")
    assert r.status_code == 200
    # PH-PIPE: scheduler banner + per-pipeline visualization + unified backfill + jobs
    assert "스케줄러" in r.text and "파이프라인" in r.text and "백필" in r.text
    assert "/ops/pipelines/run" in r.text          # the unified backfill form posts here
    assert "아직 수집 작업이 없어요" in r.text       # jobs empty-state (no datasets server in test)


def test_ops_pipelines_run_posts_to_datasets(monkeypatch):
    import httpx as _httpx
    _login()
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"started": True}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json=None, timeout=None):
            captured["url"], captured["json"] = url, json
            return _Resp()

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    r = client.post("/ops/pipelines/run",
                    data={"preset": "us_mega", "pipelines": ["prices", "news"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/pipelines")
    assert captured["url"].endswith("/admin/pipelines/run")
    assert captured["json"]["preset"] == "us_mega" and captured["json"]["pipelines"] == ["prices", "news"]


def test_queue_page_renders_jobs_and_controls(monkeypatch):
    # The Queue page lists Procrastinate jobs with retry/cancel controls + the cron sweep schedule.
    import httpx as _httpx
    _login()

    class _Resp:
        def __init__(self, data):
            self._d, self.status_code, self.headers = data, 200, {"content-type": "application/json"}

        def json(self):
            return self._d

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, url, timeout=None):
            if url.endswith("/admin/queue"):
                return _Resp({"totals": {"todo": 2, "doing": 1, "succeeded": 9, "failed": 1},
                              "periodic": [{"task": "sweep_news", "pipeline_id": "news",
                                            "cron": "0 * * * *", "label": "뉴스 → RAG", "source": "Google News"}],
                              "tasks": ["run_pipeline"], "universe": "us_sp500"})
            if "/admin/queue/jobs" in url:
                return _Resp({"jobs": [
                    {"id": 7, "task": "run_pipeline", "queue": "ingest", "status": "failed",
                     "lock": "pipe:news:US", "args": {"pipeline_id": "news", "market": "US", "tickers": ["AAPL"]},
                     "attempts": 3, "scheduled_at": "2026-06-25T00:00:00"}]})
            return _Resp({})

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    r = client.get("/queue")
    assert r.status_code == 200
    assert "Procrastinate" in r.text
    assert "0 * * * *" in r.text                                   # the cron sweep schedule
    assert "/ops/queue/sweep/news" in r.text                       # run-now button
    assert "/ops/queue/jobs/7/retry" in r.text                     # a failed job → retry control
    assert "/ops/queue/jobs/7/cancel" in r.text                    # …and cancel control


def test_ops_queue_controls_proxy_to_datasets(monkeypatch):
    import httpx as _httpx
    _login()
    seen = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"deferred": True}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json=None, timeout=None):
            seen.append(url)
            return _Resp()

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    a = client.post("/ops/queue/sweep/prices", follow_redirects=False)
    b = client.post("/ops/queue/jobs/5/retry", follow_redirects=False)
    c = client.post("/ops/queue/jobs/5/cancel", follow_redirects=False)
    assert a.status_code == b.status_code == c.status_code == 303
    assert all(loc.headers["location"].startswith("/queue") for loc in (a, b, c))
    assert seen[0].endswith("/admin/queue/sweep/prices")
    assert seen[1].endswith("/admin/queue/jobs/5/retry")
    assert seen[2].endswith("/admin/queue/jobs/5/cancel")


def test_data_page_shows_store_empty_and_row_counts():
    _login()
    r = client.get("/data")
    assert r.status_code == 200
    assert "empty" in r.text.lower()
    assert "Stored rows by table" in r.text and "financial_facts" in r.text


def test_users_page_lists_tenants():
    _login()
    r = client.get("/users")
    assert r.status_code == 200
    assert "Acme Capital" in r.text and "Tenants" in r.text


# --- ops triggers proxy to datasets --------------------------------------
def test_ops_backfill_posts_to_datasets(monkeypatch):
    import httpx as _httpx
    _login()
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"started": True}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    r = client.post("/ops/backfill", data={"market": "US", "tickers": "AAPL MSFT"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/pipelines")
    assert captured["url"].endswith("/admin/backfill")
    assert captured["json"]["tickers"] == ["AAPL", "MSFT"]


def test_ops_backfill_precompute_also_indexes_evidence(monkeypatch):
    import httpx as _httpx
    _login()
    calls = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"started": True}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, json=None, timeout=None):
            calls.append((url, json))
            return _Resp()

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    r = client.post("/ops/backfill", data={"preset": "us_mega", "precompute": "1"}, follow_redirects=False)
    assert r.status_code == 303
    paths = [u for u, _ in calls]
    assert any(u.endswith("/admin/backfill") for u in paths)
    assert any(u.endswith("/admin/evidence-docs") for u in paths)


# --- DB browser + styled CRUD (no sqladmin) ------------------------------
def test_db_index_lists_tables_with_counts():
    _login()
    r = client.get("/db")
    assert r.status_code == 200
    assert "/db/controlplane/tenants" in r.text and "/db/studio/users" in r.text


def test_db_browse_rows_relative_urls():
    _login()
    r = client.get("/db/controlplane/tenants")
    assert r.status_code == 200 and "Acme Capital" in r.text
    assert "http://localhost" not in r.text             # links relative → proxy-safe
    assert "/db/controlplane/tenants/row/0" in r.text


def test_db_row_detail_and_edit_link():
    _login()
    r = client.get("/db/studio/users/row/0")
    assert r.status_code == 200 and "a@b.com" in r.text and "ten_1" in r.text
    assert "/db/studio/users/row/0/edit" in r.text      # editable (has PK)


def test_db_edit_updates_a_row():
    _login()
    f = client.get("/db/datasets/financial_facts/row/0/edit")
    assert f.status_code == 200 and "f_value" in f.text
    r = client.post("/db/datasets/financial_facts/row/0/edit",
                    data={"f_id": "1", "f_ticker": "AAPL", "f_value": "42"}, follow_redirects=False)
    assert r.status_code == 303
    assert "42" in client.get("/db/datasets/financial_facts/row/0").text


def test_db_create_and_delete_row():
    _login()
    r = client.post("/db/studio/agents/new",
                    data={"f_id": "ag1", "f_user_email": "a@b.com", "f_name": "Created"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Created" in client.get("/db/studio/agents").text
    d = client.post("/db/studio/agents/row/0/delete", follow_redirects=False)
    assert d.status_code == 303
    assert "Created" not in client.get("/db/studio/agents").text


def test_db_unknown_table_404():
    _login()
    assert client.get("/db/controlplane/nope").status_code == 404
    assert client.get("/db/controlplane/tenants/row/9999").status_code == 404
