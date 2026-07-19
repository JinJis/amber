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
_make_db(_CP, "create table api_keys(id text primary key, project_id text, prefix text);"
              "insert into api_keys values('k1','prj_1','vgk_aa');"
              # SIMPL-1: Project is the account; its name is the owner label (was Tenant.name)
              "create table projects(id text primary key, name text, plan text, internal int default 0);"
              "insert into projects values('prj_1','Acme Capital','pro',0);"
              # COST: llm_usage (with COST-2 breakdown cols) + usage_events for the /costs page
              "create table llm_usage(id integer primary key, service text, model text, kind text,"
              " input_tokens int, output_tokens int, calls int, estimated int,"
              " cached_input_tokens int default 0, tool_input_tokens int default 0,"
              " thinking_tokens int default 0, project_id text, ts datetime default (datetime('now')));"
              "insert into llm_usage(service,model,kind,input_tokens,output_tokens,calls,estimated,"
              "cached_input_tokens,thinking_tokens,project_id,ts) values"
              "('agent-engine','gemini-2.5-flash','plan',1000000,500000,3,0,200000,50000,'prj_1',datetime('now'));"
              "insert into llm_usage(service,model,kind,input_tokens,output_tokens,calls,estimated,project_id,ts)"
              " values('rag','gemini-embedding-2','embed_query',400000,0,2,1,'prj_1',datetime('now'));"
              "create table usage_events(id integer primary key, connector_id text, cost_units int,"
              " project_id text, ts datetime default (datetime('now')));"
              "insert into usage_events(connector_id,cost_units,project_id,ts) values('prices',5,'prj_1',datetime('now'));"
              "insert into usage_events(connector_id,cost_units,project_id,ts) values('rag',20,'prj_1',datetime('now'));"
              # COST-3: background-sweep provider call counts
              "create table provider_usage(id integer primary key, provider text, calls int,"
              " ts datetime default (datetime('now')));"
              "insert into provider_usage(provider,calls,ts) values('yahoo',120,datetime('now'));"
              "insert into provider_usage(provider,calls,ts) values('sec_edgar',40,datetime('now'));")
_make_db(_ST, "create table users(email text primary key, project_id text, plan text, api_key text);"
              "insert into users values('a@b.com','prj_1','pro','vgk_x');"
              "create table agents(id text primary key, user_email text, name text);"
              # ask_feed_cache exists at reflection time so the DB browser groups it under 피드·캐시;
              # tests still (re)seed rows via sqlite3 as needed.
              "create table ask_feed_cache(scope text primary key, payload text,"
              " signature text, generated_at text);")
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
    assert "projects" in DB_STATUS["controlplane"]["tables"]
    assert "users" in DB_STATUS["studio"]["tables"]
    assert "financial_facts" in DB_STATUS["datasets"]["tables"]
    assert all(v["error"] is None for v in DB_STATUS.values())


def test_auth_required_then_login():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    # every console page is gated
    assert client.get("/catalog", follow_redirects=False).status_code == 302
    assert client.get("/db/controlplane/projects", follow_redirects=False).status_code == 302
    assert client.post("/login", data={"username": "admin", "password": "nope"}).status_code == 401
    r = client.post("/login", data={"username": "admin", "password": "secret"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_healthz_open():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_pipelines_shows_unified_feed_card():
    """Pipelines 콘솔에 4개 스코프를 한데 모은 통합 '홈 트렌드 피드' 카드 — 각 스코프의 라벨·카드 수·
    실제 생성된 카드 프리뷰(question) + 뉴스/섹션/전부 갱신 버튼이 뜬다."""
    c = sqlite3.connect(_ST)
    c.executescript(
        "delete from ask_feed_cache;"
        "insert into ask_feed_cache(scope,payload,generated_at) values"
        "('earnings_radar','{\"cards\":["
        "{\"kind\":\"earnings_upcoming\",\"question\":\"엔비디아 이번 분기 실적 언제 나와요?\"},"
        "{\"kind\":\"earnings_surprise\",\"question\":\"테슬라 어닝 서프라이즈 있었어요?\"}]}',"
        "'2026-07-17 00:00:00');")
    c.commit(); c.close()
    _login()
    r = client.get("/pipelines")
    assert r.status_code == 200
    # 통합 카드 + 4개 스코프 라벨이 모두(생성 안 된 스코프는 '생성 전'으로) 표시된다
    assert "홈 트렌드 피드" in r.text
    for label in ("Macro Trends", "어닝 레이더", "투자거장·수급", "히스토리 랩"):
        assert label in r.text
    assert "카드 <b>2</b>개" in r.text                          # earnings_radar 캐시의 카드 수
    assert "엔비디아 이번 분기 실적 언제 나와요?" in r.text        # 실제 생성된 카드 프리뷰(question)
    assert "생성 전" in r.text                                   # 아직 생성 안 된 스코프의 배지
    # 세 갱신 버튼이 각 엔드포인트로 wired
    assert "/ops/askfeed/refresh-sections" in r.text
    assert "/ops/askfeed/refresh-all" in r.text
    assert "action=/ops/askfeed/refresh>" in r.text            # 뉴스 갱신(정확 경로 매치)


def test_ops_refresh_sections_proxies_studio(monkeypatch):
    """'지금 갱신 ▶' → studio /ask-feed/refresh-sections 프록시 → /pipelines로 메시지 리다이렉트."""
    import httpx

    async def _fake_post(self, url, **kw):
        assert url.endswith("/ask-feed/refresh-sections")
        return httpx.Response(200, json={"scopes": 3, "refreshed": 2,
                                         "cards_by_scope": {"earnings_radar": 5, "guru_flows": 3,
                                                            "history_lab": 4}})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    _login()
    r = client.post("/ops/askfeed/refresh-sections", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/pipelines?msg=")


def test_ops_refresh_all_proxies_both_studio_endpoints(monkeypatch):
    """'전부 갱신 ▶' → studio /ask-feed/refresh + /ask-feed/refresh-sections 둘 다 프록시 → 303."""
    import httpx

    seen: list[str] = []

    async def _fake_post(self, url, **kw):
        seen.append(url)
        if url.endswith("/ask-feed/refresh"):
            return httpx.Response(200, json={"refreshed": True, "cards": 6})
        return httpx.Response(200, json={"scopes": 3, "refreshed": 2,
                                         "cards_by_scope": {"earnings_radar": 5}})
    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)
    _login()
    r = client.post("/ops/askfeed/refresh-all", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/pipelines?msg=")
    assert any(u.endswith("/ask-feed/refresh") for u in seen)
    assert any(u.endswith("/ask-feed/refresh-sections") for u in seen)


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


def test_users_page_lists_accounts():
    _login()
    r = client.get("/users")
    assert r.status_code == 200
    assert "Acme Capital" in r.text and "Projects" in r.text   # SIMPL-1: accounts, not tenants


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
    assert "/db/controlplane/projects" in r.text and "/db/studio/users" in r.text


def test_db_index_groups_tables_by_domain():
    """/db는 각 서비스 DB의 테이블을 도메인 그룹으로 묶어 소제목과 함께 보여준다 — studio는 피드·캐시,
    대화, 유저·인증 등으로. 칩(테이블 링크 + 행수)은 그대로 유지된다."""
    _login()
    r = client.get("/db")
    assert r.status_code == 200
    # studio의 도메인 소제목 — ask_feed_cache는 '피드·캐시', agents는 '대화', users는 '유저·인증'으로
    assert "피드·캐시" in r.text and "대화" in r.text and "유저·인증" in r.text
    # 칩 마크업(테이블 링크)은 보존
    assert "/db/studio/ask_feed_cache" in r.text
    assert "/db/studio/users" in r.text


def test_db_browse_rows_relative_urls():
    _login()
    r = client.get("/db/controlplane/projects")
    assert r.status_code == 200 and "Acme Capital" in r.text
    assert "http://localhost" not in r.text             # links relative → proxy-safe
    assert "/db/controlplane/projects/row/0" in r.text


def test_db_row_detail_and_edit_link():
    _login()
    r = client.get("/db/studio/users/row/0")
    assert r.status_code == 200 and "a@b.com" in r.text and "prj_1" in r.text
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
    assert client.get("/db/controlplane/projects/row/9999").status_code == 404


# --- OPS-2: 델타 수집 방식 · OpenDART 쿼터 카드 · Runs(수집 이력) ----------------
class _JsonResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._d


def _fake_get_client(routes: dict, seen: list | None = None):
    """AsyncClient stand-in — GET dispatches on the first matching URL fragment (canned JSON);
    anything unmatched returns {} (a reachable-but-empty service)."""

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...

        async def get(self, url, timeout=None):
            if seen is not None:
                seen.append(url)
            for frag, data in routes.items():
                if frag in url:
                    return _JsonResp(data)
            return _JsonResp({})

    return _Client


_QUOTA = {"provider": "opendart", "day_kst": "2026-07-11", "limit_per_key": 20000,
          "keys": [{"key": "…abcd", "used_today": 1200, "limit": 20000, "remaining": 18800, "blocked": False},
                   {"key": "…wxyz", "used_today": 20000, "limit": 20000, "remaining": 0, "blocked": True}],
          "history": [{"day": "2026-07-11", "key": "…abcd", "calls": 1200},
                      {"day": "2026-07-10", "key": "…abcd", "calls": 900}]}

_REGISTRY = {"pipelines": [
    {"id": "financials", "kind": "backfill", "label": "재무제표", "markets": ["US", "KR"],
     "default": True, "desc": "3대 재무제표", "source": "SEC EDGAR · OpenDART",
     "store": "financial_facts", "min_interval_seconds": 604800,
     "delta": "저장된 최신 분기가 신선한 종목은 건너뛰어요", "latest": None},
    {"id": "news", "kind": "news", "label": "뉴스 → RAG", "markets": ["US", "KR"],
     "default": True, "desc": "헤드라인 색인", "source": "Google News", "store": "RAG corpus",
     "min_interval_seconds": 3600, "delta": "항상 최신 N건만", "latest": None},
], "queue": {"totals": {"todo": 0, "doing": 0, "succeeded": 3, "failed": 0},
             "periodic": [{"task": "sweep_financials", "pipeline_id": "financials",
                           "cron": "0 3 * * 1", "label": "재무제표", "source": "SEC EDGAR · OpenDART"}],
             "tasks": ["run_pipeline"], "universe": "us_sp500"}}


def test_pipelines_page_mode_radio_quota_card_and_tools_section(monkeypatch):
    import httpx as _httpx
    _login()
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/pipelines": _REGISTRY, "/admin/quota": _QUOTA,
        "/admin/jobs": {"jobs": [], "total": 0}, "/admin/universes": {"universes": []},
    }))
    r = client.get("/pipelines")
    assert r.status_code == 200
    # 수집 방식 radio — 델타가 기본으로 선택, 전체는 옵트인
    assert "수집 방식" in r.text
    assert "name=mode value=delta checked" in r.text and "name=mode value=full" in r.text
    # OpenDART 쿼터 카드 — 키별 사용량/남은 호출 + 소진 배지 + 이력
    assert "OpenDART 쿼터" in r.text and "오늘 21,200/40,000" in r.text
    assert "남음 18,800" in r.text and "일일 한도 소진 · KST 자정 리셋" in r.text
    assert "최근 14일 사용 이력" in r.text
    # 파이프라인 카드는 레지스트리의 델타 설명을 그대로 보여주고, 크론 스윕 버튼은 델타를 명시
    assert "델타: 저장된 최신 분기가 신선한 종목은 건너뛰어요" in r.text
    assert "지금 수집(델타) ▶" in r.text
    # 부가 도구(Macro Trends·로고·RAG 프로브)는 하나의 '도구' 섹션으로 페이지 맨 아래에
    tools_at = r.text.index("<h2>도구</h2>")
    assert tools_at > r.text.index("백필")
    assert r.text.index("Macro Trends") > tools_at and r.text.index("RAG ingest") > tools_at
    # 전체 이력은 /runs로 안내
    assert "/runs" in r.text


def test_pipelines_quota_card_degrades_without_keys(monkeypatch):
    import httpx as _httpx
    _login()
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/quota": {"provider": "opendart", "day_kst": "2026-07-11",
                         "limit_per_key": 20000, "keys": [], "history": []},
    }))
    r = client.get("/pipelines")
    assert r.status_code == 200 and "OPENDART_API_KEYS" in r.text   # 키 없음 → 설정 안내 (무 날조)


def test_overview_quota_tile_and_runs_jump(monkeypatch):
    import httpx as _httpx
    _login()
    quota = {"keys": [{"key": "…a", "remaining": 1234, "blocked": False},
                      {"key": "…b", "remaining": 18800, "blocked": False},
                      {"key": "…c", "remaining": 0, "blocked": True}], "history": []}
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({"/admin/quota": quota}))
    r = client.get("/")
    assert r.status_code == 200
    # 살아있는 키들의 최소 남은 호출 수(보수적)를 타일로
    assert "OpenDART 남은 쿼터" in r.text and "1,234" in r.text
    assert "수집 이력 →" in r.text                     # jump pill → /runs
    assert "href='/runs'" in r.text                    # nav의 Runs 항목


_RUN_JOBS = [
    {"id": 7, "kind": "news", "market": "US", "spec": "news · 3 tickers", "status": "success",
     "rows": 12, "total": 3, "done": 3, "error": None, "error_details": [],
     "started_at": "2026-07-10T04:00:00", "ended_at": "2026-07-10T04:02:30"},
    {"id": 6, "kind": "backfill", "market": "KR", "spec": "universe:kr_kospi200 · delta", "status": "error",
     "rows": 0, "total": 10, "done": 4, "error": "실패 2 · 원인 1종",
     "error_details": [{"error": "ReadTimeout: DART", "tickers": ["005930", "000660"], "count": 2}],
     "started_at": "2026-07-10T03:00:00", "ended_at": "2026-07-10T03:01:00"},
]


def test_runs_page_renders_filter_bar_pager_and_rows(monkeypatch):
    import httpx as _httpx
    _login()
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/jobs": {"jobs": _RUN_JOBS, "total": 120, "offset": 0, "limit": 50},
        "/admin/pipelines": _REGISTRY,
    }))
    r = client.get("/runs")
    assert r.status_code == 200
    # 필터 바 — 파이프라인(레지스트리 라벨)·시장·상태
    assert "전체 파이프라인" in r.text and "전체 시장" in r.text and "전체 상태" in r.text
    assert "뉴스 → RAG · news" in r.text
    # 페이저 — 총 건수 + 다음 페이지 (offset 0 → 이전 없음)
    assert "총 120건" in r.text and "1–2 표시" in r.text
    assert "/runs?offset=50" in r.text and "← 이전" not in r.text
    # 행 — 상세 링크·파이프라인 라벨·진행률·소요 시간·원인별 오류 펼침
    assert "/runs/7" in r.text and "뉴스 → RAG" in r.text and "3/3" in r.text
    assert "2분 30초" in r.text
    assert "실패 2 · 원인 1종" in r.text and "ReadTimeout: DART — 2종목: 005930, 000660" in r.text


def test_runs_filters_forward_to_datasets_and_stay_selected(monkeypatch):
    import httpx as _httpx
    _login()
    seen: list[str] = []
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/jobs": {"jobs": [_RUN_JOBS[1]], "total": 1, "offset": 0, "limit": 50},
        "/admin/pipelines": _REGISTRY,
    }, seen))
    r = client.get("/runs?kind=backfill&market=KR&status=error")
    assert r.status_code == 200
    jobs_url = next(u for u in seen if "/admin/jobs" in u)
    assert "limit=50&offset=0" in jobs_url
    assert "kind=backfill" in jobs_url and "market=KR" in jobs_url and "status=error" in jobs_url
    # 선택값이 폼에 유지된다 (새로고침·북마크 안전)
    assert "value='backfill' selected" in r.text and "value=KR selected" in r.text


def test_run_detail_renders_header_errors_and_activity_log(monkeypatch):
    import httpx as _httpx
    _login()
    activity = {"activity": [   # datasets는 최신순으로 반환한다
        {"id": 3, "job_id": 6, "kind": "backfill", "market": "KR", "level": "error",
         "message": "[000660] 실패 — ReadTimeout", "at": "2026-07-10T03:00:50"},
        {"id": 2, "job_id": 6, "kind": "backfill", "market": "KR", "level": "info",
         "message": "[005930] 10 rows ✓", "at": "2026-07-10T03:00:20"},
        {"id": 1, "job_id": 6, "kind": "backfill", "market": "KR", "level": "info",
         "message": "▶ 시작 · 2종목", "at": "2026-07-10T03:00:00"},
    ]}
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/jobs": {"jobs": _RUN_JOBS, "total": 2},
        "/admin/queue/activity": activity,
    }))
    r = client.get("/runs/6")
    assert r.status_code == 200
    # 헤더 — 실행 번호·spec·소요 시간·빵부스러기
    assert "수집 실행 #6" in r.text and "← 수집 이력" in r.text
    assert "universe:kr_kospi200 · delta" in r.text and "1분 0초" in r.text
    # 원인별(그룹) 오류 + 실패 종목만 재시도 (backfill → financials 파이프라인으로 매핑)
    assert "오류 상세" in r.text and "ReadTimeout: DART" in r.text
    assert "실패 종목만 재시도" in r.text and "value='financials'" in r.text
    # 활동 로그는 시간순(오래된 순)으로 — 읽는 방향 그대로
    assert "활동 로그" in r.text
    i1, i2, i3 = (r.text.index("▶ 시작 · 2종목"), r.text.index("[005930] 10 rows"),
                  r.text.index("[000660] 실패"))
    assert i1 < i2 < i3


def test_run_detail_not_found_is_honest(monkeypatch):
    import httpx as _httpx
    _login()
    monkeypatch.setattr(_httpx, "AsyncClient", _fake_get_client({
        "/admin/jobs": {"jobs": [], "total": 0},
    }))
    r = client.get("/runs/9999")
    assert r.status_code == 200 and "찾지 못했어요" in r.text   # 없는 실행 → 정직한 안내 (500 아님)


def test_ops_pipelines_run_forwards_mode(monkeypatch):
    import httpx as _httpx
    _login()
    captured: list[dict] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"started": True}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...

        async def post(self, url, json=None, timeout=None):
            captured.append({"url": url, "json": json})
            return _Resp()

    monkeypatch.setattr(_httpx, "AsyncClient", _Client)
    # 폼의 radio 값이 datasets payload의 mode로 그대로 전달된다
    r = client.post("/ops/pipelines/run",
                    data={"preset": "us_mega", "pipelines": ["prices"], "mode": "full"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/pipelines")
    assert captured[-1]["url"].endswith("/admin/pipelines/run")
    assert captured[-1]["json"]["mode"] == "full" and captured[-1]["json"]["preset"] == "us_mega"
    # mode를 안 보내면(레거시 폼·재시도 버튼) 기본은 델타
    client.post("/ops/pipelines/run", data={"market": "US", "tickers": "AAPL", "pipelines": ["news"]},
                follow_redirects=False)
    assert captured[-1]["json"]["mode"] == "delta" and captured[-1]["json"]["tickers"] == ["AAPL"]
    # 이상한 값은 델타로 강제 (datasets에 쓰레기 값을 넘기지 않는다)
    client.post("/ops/pipelines/run", data={"preset": "us_mega", "mode": "bogus"}, follow_redirects=False)
    assert captured[-1]["json"]["mode"] == "delta"


# --- Costs page (COST-1/2) ------------------------------------------------
def test_costs_page_prices_usage_with_cache_discount():
    """/costs가 llm_usage를 캐시 할인 반영해 달러화하고, 유저·서비스·커넥터·요율표·미추적을 렌더."""
    _login()
    r = client.get("/costs")
    assert r.status_code == 200
    t = r.text
    # gemini-2.5-flash: 800k@0.30 + 200k캐시@0.03 + 500k출력@2.50 = 0.24+0.006+1.25 = $1.4960
    # (캐시 할인 없으면 $1.5500 — 이 값이 뜨면 할인이 적용된 것)
    assert "1.4960" in t and "1.5500" not in t
    assert "gemini-2.5-flash" in t
    assert "Acme Capital" in t                 # 유저별 롤업(계정 이름 = Project.name)
    assert "prices" in t and "rag" in t        # 게이트웨이 커넥터
    assert "<svg" in t                          # 일별 비용 스파크라인
    assert "요율 기준일" in t                    # 스테일 요율 배너
    assert "커넥터 호출" in t                    # 유저별 롤업의 커넥터 열
    # 새 컬럼 헤더(캐시·생각) + 추정 배지
    assert "캐시" in t and "생각" in t and "추정" in t
    # COST-3: 백그라운드 스윕 상류 호출 섹션
    assert "백그라운드 스윕 상류 호출" in t and "yahoo" in t and "sec_edgar" in t


def test_costs_range_selector():
    _login()
    assert client.get("/costs?days=7").status_code == 200
    assert client.get("/costs?days=90").status_code == 200
    # 이상한 값은 기본 30일로 강등 (렌더는 계속)
    assert client.get("/costs?days=999").status_code == 200


def test_sparkline_helper():
    from adminpanel.main import _sparkline
    assert "데이터 없음" in _sparkline([])
    assert "<svg" in _sparkline([1.0])          # 1점도 렌더(중복으로 평평)
    svg = _sparkline([0.0, 1.0, 0.5, 2.0])
    assert "<polyline" in svg and "<polygon" in svg
