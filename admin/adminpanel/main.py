"""ValueGraph admin — operations console.

A left-nav mission-control over the whole platform, organized by operator
job-to-be-done (Overview · Catalog · Pipelines · Data · Users · DB browser):

* **Catalog** — what the service offers, live from the manifest: every data
  source/connector, each resource → REST path → MCP tool, RAG + agent backends.
* **Pipelines** — every ingest/precompute job as a live progress card + controls.
* **Data / Users** — ingestion-store + RAG health; tenants/projects/keys/activations.
* **DB browser** — our own styled CRUD over every reflected service table (no
  sqladmin → no unstyled raw-HTML fallback).

One session login gates everything (a guard middleware).
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from adminpanel.clients import _ok, _safe_get
from adminpanel.config import settings
from adminpanel.logging_config import install_request_logging, setup_logging
# Reflected service-DB state + DB helpers live in state.py (RF-14); re-exported here so importers
# (and tests) that reference `adminpanel.main.DB_STATUS` keep working.
from adminpanel.state import (  # noqa: F401
    DB_STATUS,
    ENGINES,
    TABLES,
    _has,
    _mount_database,
    _query,
    _table_counts,
)
from adminpanel.views import (
    JOB_STATUS_CLASS,
    QUEUE_STATUS_CLASS,
    QUEUE_STATUS_LABEL,
    UPSTREAM_DOT,
    UPSTREAM_LABEL,
    _cell,
    _esc,
    badge,
    login_page,
    page,
    progress,
    sdot,
    tile,
)

setup_logging()

app = FastAPI(title="ValueGraph Admin")
install_request_logging(app)

from adminpanel import db_browser  # noqa: E402
app.include_router(db_browser.router)


# --- auth -----------------------------------------------------------------
async def _guard(request: Request, call_next):
    p = request.url.path
    if p in ("/login", "/logout", "/healthz"):
        return await call_next(request)
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


app.add_middleware(BaseHTTPMiddleware, dispatch=_guard)
app.add_middleware(SessionMiddleware, secret_key=settings.adminui_secret)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if request.session.get("authed"):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(login_page())


@app.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form("")):
    if username == settings.adminui_username and password == settings.adminui_password:
        request.session["authed"] = True
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(login_page("<div class=e>Invalid credentials.</div>"), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# --- shared HTML bits (fetch helpers → clients.py; DB state/helpers → state.py — RF-14) ---------
def _flash(msg: str) -> str:
    return f"<div class=flash>{_esc(msg)}</div>" if msg else ""


# --- Overview -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def overview(request: Request, msg: str = ""):
    async with httpx.AsyncClient() as c:
        queue = await _safe_get(c, f"{settings.datasets_url}/admin/queue")
        stats = await _safe_get(c, f"{settings.datasets_url}/admin/store/stats")
        jobs = await _safe_get(c, f"{settings.datasets_url}/admin/jobs")
        raginfo = await _safe_get(c, f"{settings.rag_url}/rag/info")
        catalog = await _safe_get(c, f"{settings.gateway_url}/catalog")
        agentinfo = await _safe_get(c, f"{settings.agent_engine_url}/agent/info")

    conns = catalog.get("connectors") or []
    tool_count = sum(len(cn.get("resources") or []) for cn in conns) if conns else catalog.get("count", "?")
    job_list = jobs.get("jobs") if isinstance(jobs, dict) else []
    running = [j for j in (job_list or []) if j.get("status") == "running"]
    errored = [j for j in (job_list or []) if j.get("status") == "error"][:5]
    q_totals = queue.get("totals") or {}
    q_pending, q_doing = q_totals.get("todo", 0), q_totals.get("doing", 0)

    tiles = "".join([
        tile("data sources", len(conns) if conns else "?", "◈", "/catalog"),
        tile("catalog tools", tool_count, "⚙", "/catalog"),
        tile("RAG embedder", raginfo.get("embedding_backend", "—"), "▤", "/data", small=True),
        tile("queue pending", q_pending if _ok(queue) else "—", "⚙", "/queue"),
        tile("store facts", stats.get("total_facts", "—"), "▦", "/data"),
        tile("queue running", q_doing if _ok(queue) else len(running), "●", "/queue"),
    ])

    # the queue is healthy if the overview came back AND its job DB was reachable (no 'error' field)
    queue_up = _ok(queue) and not queue.get("error")
    checks = [
        ("Gateway / catalog", _ok(catalog)),
        ("Data plane (datasets)", _ok(stats) or _ok(queue)),
        ("RAG", _ok(raginfo)),
        ("Agent engine", _ok(agentinfo)),
        ("Queue (Procrastinate)", queue_up),
    ]
    health = "".join(
        f"<div class=card><h3>{sdot('ok' if up else 'err')} {_esc(name)}</h3>"
        f"<div class=sub>{'reachable' if up else 'unreachable / down'}</div></div>"
        for name, up in checks
    )
    for key, info in DB_STATUS.items():
        up = info["error"] is None
        health += (f"<div class=card><h3>{sdot('ok' if up else 'err')} DB · {_esc(info['title'])}</h3>"
                   f"<div class=sub>{len(info.get('meta', {}))} tables · {_esc(info['error']) if not up else 'reflected'}</div></div>")

    err_html = ""
    if errored:
        rows = "".join(f"<tr><td>{_esc(j['id'])}</td><td>{_esc(j.get('kind'))}</td>"
                       f"<td class=err>{_cell(j.get('error'), 120)}</td></tr>" for j in errored)
        err_html = ("<h2>Recent errors</h2><div class=tablewrap><table><thead><tr><th>#</th><th>kind</th>"
                    f"<th>error</th></tr></thead><tbody>{rows}</tbody></table></div>")

    body = (
        _flash(msg)
        + "<h2>At a glance</h2><div class=tiles>" + tiles + "</div>"
        + "<h2>Subsystem health</h2><div class=grid>" + health + "</div>"
        + err_html
        + "<h2>Jump to</h2><div>"
        + "".join(f"<span class=pill><a href='{h}'>{_esc(l)}</a></span>"
                  for h, l in [("/catalog", "Catalog →"), ("/pipelines", "Pipelines →"),
                               ("/queue", "Queue →"), ("/data", "Data →"), ("/users", "Users →"),
                               ("/db", "DB browser →")])
        + "</div>"
    )
    return HTMLResponse(page("/", "Overview", body, refresh=bool(running)))


# --- Catalog --------------------------------------------------------------
@app.get("/catalog", response_class=HTMLResponse)
async def catalog_view(request: Request):
    async with httpx.AsyncClient() as c:
        catalog = await _safe_get(c, f"{settings.gateway_url}/catalog")
        raginfo = await _safe_get(c, f"{settings.rag_url}/rag/info")
        agentinfo = await _safe_get(c, f"{settings.agent_engine_url}/agent/info")

    conns = catalog.get("connectors") or []
    tool_count = sum(len(cn.get("resources") or []) for cn in conns)

    cards = ""
    for cn in conns:
        cid = cn.get("id", "?")
        lic = cn.get("license") or {}
        up = cn.get("upstream") or {}
        meta_badges = " ".join(
            [badge(m) for m in (cn.get("markets") or [])]
            + [badge("key required", "warn") if up.get("requires_key") else badge("keyless", "ok"),
               badge("redistributable", "ok") if lic.get("redistribution") else badge("restricted", "err")]
        )
        rrows = ""
        for r in (cn.get("resources") or []):
            prov = (r.get("provenance") or {}).get("source") or "—"
            tool = f"{cid}__{r.get('name')}"
            rrows += (f"<tr><td><code>{_esc(tool)}</code></td>"
                      f"<td>{badge(_esc(r.get('method', 'GET')))} <code>{_esc(r.get('path'))}</code></td>"
                      f"<td>{_esc(', '.join(r.get('markets') or cn.get('markets') or []))}</td>"
                      f"<td class=muted>{_esc(prov)}</td></tr>")
        cards += (
            f"<div class=card><h3>{_esc(cn.get('name'))} <span class=muted>{_esc(cid)}</span></h3>"
            f"<div class=sub>{_esc(cn.get('description') or '')}</div>"
            f"<div style='margin-bottom:10px'>{meta_badges}</div>"
            f"<div class=tablewrap><table><thead><tr><th>MCP tool</th><th>REST</th><th>markets</th><th>source</th></tr></thead>"
            f"<tbody>{rrows or '<tr><td colspan=4 class=muted>no resources</td></tr>'}</tbody></table></div></div>"
        )

    rag_card = (
        "<div class=card><h3>◩ RAG</h3><div class=sub>provenance-first retrieval</div>"
        + "".join(f"<div><span class=muted>{_esc(k)}:</span> <code>{_esc(raginfo.get(k, '—'))}</code></div>"
                  for k in ("embedding_backend", "embedding_model", "reranker_backend", "vector_store"))
        + ("<div class=warn>RAG unreachable</div>" if not _ok(raginfo) else "")
        + "</div>"
    )
    agent_card = (
        "<div class=card><h3>✦ Agent engine</h3><div class=sub>planner + tool loop</div>"
        + "".join(f"<div><span class=muted>{_esc(k)}:</span> <code>{_esc(agentinfo.get(k, '—'))}</code></div>"
                  for k in ("llm_backend", "model"))
        + ("<div class=warn>Agent engine unreachable</div>" if not _ok(agentinfo) else "")
        + "</div>"
    )

    summary = "".join([
        tile("connectors", len(conns), "◈", "/catalog"),
        tile("MCP tools", tool_count, "⚙", "/catalog"),
        tile("RAG embedder", raginfo.get("embedding_backend", "—"), "▤", "/data", small=True),
        tile("agent model", agentinfo.get("model", "—"), "✦", "/catalog", small=True),
    ])

    err = "<div class=warn>Gateway/catalog unreachable — start the stack to see live connectors.</div>" if not conns else ""
    body = ("<p class=hint>Live from the connector manifest (<code>/catalog</code>), <code>/rag/info</code> and "
            "<code>/agent/info</code> — every data source, its REST routes &amp; the MCP tool each exposes "
            "(<code>{connector}__{resource}</code>). Never hand-maintained.</p>"
            + "<div class=tiles>" + summary + "</div>" + err
            + "<h2>Retrieval &amp; agent</h2><div class=grid>" + rag_card + agent_card + "</div>"
            + "<h2>Data sources / connectors</h2><div class=grid>" + cards + "</div>")
    return HTMLResponse(page("/catalog", "Catalog", body))


# --- Pipelines ------------------------------------------------------------
def _interval_label(seconds: int) -> str:
    if not seconds:
        return "—"
    if seconds % 604800 == 0:
        return f"{seconds // 604800}주마다"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}일마다"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}시간마다"
    if seconds % 60 == 0:
        return f"{seconds // 60}분마다"
    return f"{seconds}초마다"


def _pipeline_card(p: dict, cron_by_pid: dict[str, str]) -> str:
    """Visualize one pipeline: source → store, cron sweep, latest run (status/rows/error), run-now."""
    pid = p["id"]
    cron = cron_by_pid.get(pid)
    # each pipeline has its OWN cadence (min_interval_seconds) → shown as a human label next to its cron.
    cadence = p.get("min_interval_seconds") or 0
    sched_txt = (f"⏱ {_interval_label(cadence)}" if cron else "수동 전용")
    sched_cls = "ok" if cron else ""
    cron_txt = f" <span class=muted><code>{_esc(cron)}</code></span>" if cron else ""
    j = p.get("latest") or {}
    if j:
        st = j.get("status")
        kind = JOB_STATUS_CLASS.get(st, "")
        tot, dn = j.get("total") or 0, j.get("done") or 0
        last = (f"<div class=sub>최근 실행 {badge(_esc(st), kind)} · 수집 {_esc(j.get('rows', 0))}행"
                + (f" · {dn}/{tot}" if tot else "") + f"<br><span class=muted>{_esc((j.get('started_at') or '')[:19])}</span>")
        if j.get("error"):
            last += f"<br><span class=err>{_cell(j.get('error'), 80)}</span>"
        last += "</div>"
    else:
        last = "<div class=sub muted>아직 실행 기록 없음</div>"
    markets = " ".join(badge(m) for m in p.get("markets", []))
    # 원천 API · 쿼리 — operators can see EXACTLY which upstream endpoint + request each pipeline issues.
    api_lines = p.get("upstream") or []
    fetch = p.get("fetch") or ""
    detail = ""
    if api_lines or fetch:
        body = "\n".join(api_lines)
        if fetch:
            body += ("\n\n" if body else "") + "fetch: " + fetch
        detail = (f"<details class=errlog><summary>원천 API · 쿼리</summary>"
                  f"<pre>{_esc(body)}</pre></details>")
    if cron:
        # cron-scheduled pipeline → one-click sweep over the configured universe.
        run_now = (f"<form class=ops method=post action='/ops/queue/sweep/{_esc(pid)}'>"
                   f"<button class=p>지금 수집 ▶</button></form>")
    else:
        # manual-only pipeline (no auto-cron — these are rate-limited/metered: e.g. Alpha Vantage
        # 25 calls/day, Document AI per-page). Run is TICKER-SCOPED on purpose: a full-universe run
        # would blow the quota/cost, so that stays only in the backfill form below. Enter a few tickers.
        mkt = (p.get("markets") or ["US"])[0]
        run_now = (
            f"<form class=ops method=post action='/ops/pipelines/run'>"
            f"<input type=hidden name=pipelines value='{_esc(pid)}'>"
            f"<input type=hidden name=market value='{_esc(mkt)}'>"
            f"<input name=tickers required placeholder='AAPL MSFT … (티커 직접 입력)' size=26>"
            f"<button class=p>지금 수집 ▶</button></form>")
    return (
        f"<div class=card><h3>{_esc(p['label'])} {badge(sched_txt, sched_cls)}{cron_txt}</h3>"
        f"<div class=sub>{_esc(p.get('desc') or '')}</div>"
        f"<div class=flow><span class=pill>{_esc(p.get('source'))}</span> <span class=arrow>→</span> "
        f"<span class=pill><code>{_esc(p.get('store'))}</code></span> {markets}</div>"
        f"{detail}{last}<div class=opsrow>{run_now}</div></div>"
    )


@app.get("/pipelines", response_class=HTMLResponse)
async def pipelines(request: Request, msg: str = ""):
    async with httpx.AsyncClient() as c:
        pdata = await _safe_get(c, f"{settings.datasets_url}/admin/pipelines")
        jobs = await _safe_get(c, f"{settings.datasets_url}/admin/jobs")
        universes = await _safe_get(c, f"{settings.datasets_url}/admin/universes")

    registry = pdata.get("pipelines") or []
    queue = pdata.get("queue") or {}
    periodic = queue.get("periodic") or []
    cron_by_pid = {s["pipeline_id"]: s["cron"] for s in periodic}
    totals = queue.get("totals") or {}
    queue_up = not queue.get("error") and "totals" in queue

    job_list = jobs.get("jobs") if isinstance(jobs, dict) else []
    running = any(j.get("status") == "running" for j in (job_list or []))

    # --- queue banner: the Procrastinate scheduler (worker) — cron sweeps + live job counts ---
    qstate = ("run" if totals.get("doing") else "ok") if queue_up else "err"
    qbadge = badge("가동중" if queue_up else "큐 DB 연결 안됨", qstate)
    counts = (f"<span class=pill>대기 <b>{_esc(totals.get('todo', 0))}</b></span>"
              f"<span class=pill>실행중 <b>{_esc(totals.get('doing', 0))}</b></span>"
              f"<span class=pill>완료 <b>{_esc(totals.get('succeeded', 0))}</b></span>"
              f"<span class=pill>실패 <b>{_esc(totals.get('failed', 0))}</b></span>") if queue_up else ""
    sweeps = " · ".join(f"{_esc(s['label'])} <code>{_esc(s['cron'])}</code>" for s in periodic) or "없음"
    queue_banner = (
        "<div class=card><h3>⚙ 큐 스케줄러 (Procrastinate · 워커) " + qbadge + "</h3>"
        f"<div class=flow>{counts}<span class=pill>크론 스윕 <b>{_esc(len(periodic))}</b>개</span></div>"
        f"<div class=sub>자동 수집(크론): {sweeps}</div>"
        "<div class=sub muted>워커가 정해진 크론에 유니버스를 스윕해 작업을 큐에 넣고 재시도와 함께 처리합니다. "
        "개별 작업 모니터링·재시도·취소는 <a href=/queue>Queue →</a>. 자동 수집을 멈추려면 워커를 중지하세요 "
        "(<code>docker compose stop worker</code>).</div>"
        "<div class=opsrow>"
        "<a class='btn p' href=/queue>큐 작업 보기 →</a>"
        "<form class=ops method=post action=/ops/selftest><button>self-test</button></form>"
        "</div></div>"
    )

    # --- per-pipeline visualization cards ---
    cards = "".join(_pipeline_card(p, cron_by_pid) for p in registry) or "<div class=empty>파이프라인 레지스트리를 불러오지 못했어요.</div>"

    # --- unified backfill: pick universe + pipelines, run together ---
    # CE-0: a one-click "full universe" option = the sweep's configured spec (multi-preset,
    # resolved dynamically server-side), so the operator can deep-backfill everything at once.
    full_spec = queue.get("universe") or ""
    full_opt = (f"<option value='{_esc(full_spec)}'>★ 전체 유니버스 (스윕: {_esc(full_spec)})</option>"
                if full_spec else "")
    preset_opts = full_opt + "".join(
        f"<option value='{_esc(u['id'])}'>{_esc(u['label'])} · {_esc(u['market'])} ({_esc(u['count'])})</option>"
        for u in (universes.get("universes") or [])
    ) or "<option value=''>(presets unavailable)</option>"
    pipe_checks = "".join(
        f"<label class=chk><input type=checkbox name=pipelines value='{_esc(p['id'])}'"
        + (" checked" if p.get("default") else "") + f"> {_esc(p['label'])}</label>"
        for p in registry
    )
    backfill = f"""
<h2>백필 — 한 번에 구성해서 수집</h2>
<div class=card><div class=sub>유니버스(프리셋 또는 직접 입력)를 고르고, 돌릴 파이프라인을 선택해 한 번에 수집합니다.
S&amp;P·코스피·코스닥 전체는 직접 입력란에 티커를 붙여넣으세요.</div>
  <form class=ops2 method=post action=/ops/pipelines/run>
    <div class=row><label>프리셋</label><select name=preset>{preset_opts}</select></div>
    <div class=row><label>직접 입력</label><select name=market><option>US</option><option>KR</option></select>
      <input name=tickers placeholder="AAPL MSFT / 005930 … (입력 시 프리셋 무시)" size=40></div>
    <div class=row><label>파이프라인</label><div class=checks>{pipe_checks}</div></div>
    <button class=p>수집 시작</button>
  </form></div>"""

    # --- jobs table (live) ---
    if job_list:
        rows = ""
        for j in job_list:
            st = j.get("status")
            kind = JOB_STATUS_CLASS.get(st, "")
            tot, dn = j.get("total") or 0, j.get("done") or 0
            ptxt = f"{dn}/{tot}" if tot else "—"
            err = j.get("error") or ""
            # full error on click (no truncation) — expand to read the whole stack/SQL.
            err_cell = (f"<details class=errlog><summary>{_cell(err, 60)}</summary>"
                        f"<pre>{_esc(err)}</pre></details>") if err else ""
            rows += (
                f"<tr><td>{_esc(j['id'])}</td><td>{badge(_esc(j.get('kind')))}</td>"
                f"<td>{_esc(j.get('market') or '')}</td><td class=wrap>{_cell(j.get('spec'), 40)}</td>"
                f"<td>{badge(_esc(st), kind)}</td>"
                f"<td><div style='display:flex;align-items:center;gap:8px'>{progress(dn, tot, kind)}<span class=muted>{_esc(ptxt)}</span></div></td>"
                f"<td>{_esc(j.get('rows') if j.get('rows') is not None else '')}</td>"
                f"<td class=muted>{_esc((j.get('started_at') or '')[:19])}</td>"
                f"<td class=err>{err_cell}</td></tr>"
            )
        jobs_html = ("<div class=tablewrap><table><thead><tr><th>#</th><th>pipeline</th><th>mkt</th><th>spec</th>"
                     "<th>status</th><th>progress</th><th>rows</th><th>started</th><th>error</th></tr></thead>"
                     f"<tbody>{rows}</tbody></table></div>")
    else:
        jobs_html = "<div class=empty>아직 수집 작업이 없어요 — 아래에서 백필을 실행하세요.</div>"

    # --- secondary triggers (RAG probes) ---
    extra = """
<h2>기타</h2>
<div class=grid>
  <div class=card><h3>RAG ingest</h3><div class=sub>코퍼스에 문서 추가</div>
    <form class=ops method=post action=/ops/rag/ingest>
      <input name=text placeholder="document text" size=22 required>
      <input name=source placeholder=source value=admin size=10><button class=p>Ingest</button></form></div>
  <div class=card><h3>RAG search</h3><div class=sub>시맨틱 프로브</div>
    <form class=ops method=post action=/ops/rag/search>
      <input name=query placeholder="semantic query" size=22 required><button class=p>Search</button></form></div>
</div>"""

    body = (_flash(msg)
            + "<p class=hint>모든 데이터 파이프라인을 한곳에서 — 무엇을 어떤 경로로 수집해 어디에 쌓는지, "
              "주기·상태·에러를 시각화합니다. 작업이 도는 동안 자동 새로고침됩니다.</p>"
            + "<h2>큐 스케줄러</h2><div class=grid>" + queue_banner + "</div>"
            + "<h2>파이프라인</h2><div class=grid>" + cards + "</div>"
            + backfill
            + f"<h2>수집 작업 {'· ⟳ live' if running else ''}</h2>" + jobs_html
            + extra)
    return HTMLResponse(page("/pipelines", "Pipelines", body, refresh=running))


# --- Data -----------------------------------------------------------------
@app.get("/upstream", response_class=HTMLResponse)
async def upstream_view(request: Request):
    """CE-HEALTH: per-connector upstream health — reachable? latency? key present?"""
    async with httpx.AsyncClient() as c:
        data = await _safe_get(c, f"{settings.datasets_url}/admin/upstream-health")
    ups = data.get("upstreams") or []
    if ups:
        rows = "".join(
            f"<tr><td>{sdot(UPSTREAM_DOT.get(u['status'], 'err'))} {_esc(u['name'])}</td>"
            f"<td>{badge(UPSTREAM_LABEL.get(u['status'], u['status']), UPSTREAM_DOT.get(u['status'], ''))}</td>"
            f"<td class=mono>{_esc(u.get('http_status') or '—')}</td>"
            f"<td class=mono>{_esc(u.get('latency_ms'))} ms</td>"
            f"<td>{'필요' if u.get('requires_key') else '불필요'}"
            f"{' · ' + ('✅ 설정됨' if u.get('key_present') else '❌ 미설정') if u.get('requires_key') else ''}</td></tr>"
            for u in ups)
        table = ("<div class=tablewrap><table><thead><tr><th>업스트림</th><th>상태</th><th>HTTP</th>"
                 f"<th>지연</th><th>API 키</th></tr></thead><tbody>{rows}</tbody></table></div>")
        summary = f"<div class=flow><span class=pill>정상 <b>{_esc(data.get('healthy'))}</b> / {_esc(data.get('total'))}</span></div>"
    else:
        table = "<div class=warn>업스트림 헬스를 불러오지 못했습니다 (datasets 연결 확인).</div>"
        summary = ""
    body = ("<h2>업스트림 API 헬스</h2>"
            "<p class=muted>각 커넥터의 외부 데이터 소스 도달성·지연·키 설정을 가볍게 프로브합니다 "
            "(쿼터 소모 없음). 새로고침하면 다시 측정합니다.</p>" + summary + table)
    return HTMLResponse(page("/upstream", "Upstream", body))


@app.get("/data", response_class=HTMLResponse)
async def data_view(request: Request):
    async with httpx.AsyncClient() as c:
        stats = await _safe_get(c, f"{settings.datasets_url}/admin/store/stats")
        raginfo = await _safe_get(c, f"{settings.rag_url}/rag/info")

    by_market = stats.get("by_market") or []
    if by_market:
        rows = "".join(
            f"<tr><td>{badge(_esc(m['market']))}</td><td>{_esc(m['tickers'])}</td><td>{_esc(m['facts'])}</td>"
            f"<td class=muted>{_esc(m.get('earliest_report_period'))} → {_esc(m.get('latest_report_period'))}</td></tr>"
            for m in by_market)
        store_html = ("<div class=tablewrap><table><thead><tr><th>market</th><th>tickers</th><th>facts</th>"
                      f"<th>report-period range</th></tr></thead><tbody>{rows}</tbody></table></div>")
    else:
        store_html = ("<div class=warn>The ingestion store is <b>empty</b>. Run a backfill in "
                      "<a href=/pipelines>Pipelines</a> — otherwise screener / historical / 13F-ticker "
                      "endpoints return nothing.</div>")

    counts = {k: _table_counts(k) for k in DB_STATUS}
    db_rows = ""
    for key, info in DB_STATUS.items():
        for tname in info.get("meta", {}):
            db_rows += (f"<tr><td>{_esc(info['title'])}</td><td><a href='/db/{key}/{tname}'><code>{_esc(tname)}</code></a></td>"
                        f"<td>{_esc(counts[key].get(tname, '?'))}</td></tr>")

    rag_html = "".join(f"<span class=pill>{_esc(k)}: <code>{_esc(raginfo.get(k, '—'))}</code></span>"
                       for k in ("embedding_backend", "reranker_backend", "vector_store"))

    coverage = (
        "<div class=flow>"
        f"<span class=pill>재무 facts <b>{_esc(stats.get('total_facts', '—'))}</b></span>"
        f"<span class=pill>가격 bars <b>{_esc(stats.get('price_bars', '—'))}</b> · {_esc(stats.get('price_tickers', '—'))}종목</span>"
        f"<span class=pill>배당·분할 <b>{_esc(stats.get('corporate_actions', '—'))}</b></span>"
        "</div>") if _ok(stats) else ""

    body = ("<h2>Ingestion store</h2>" + coverage + store_html
            + "<h2>RAG corpus backends</h2><div>" + (rag_html or "<span class=warn>RAG unreachable</span>") + "</div>"
            + "<h2>Stored rows by table</h2><div class=tablewrap><table><thead><tr><th>database</th><th>table</th>"
              "<th>rows</th></tr></thead><tbody>" + (db_rows or "<tr><td colspan=3 class=muted>no databases</td></tr>")
            + "</tbody></table></div>")
    return HTMLResponse(page("/data", "Data", body))


# --- Users / tenants ------------------------------------------------------
def _simple_table(key: str, table: str, cols: list[str], limit: int = 50) -> str:
    if not _has(key, table):
        return f"<div class=empty>No <code>{_esc(table)}</code> table.</div>"
    have = [c for c in cols if c in DB_STATUS[key]["meta"][table]["columns"]] or DB_STATUS[key]["meta"][table]["columns"][:6]
    sel = ", ".join(f'"{c}"' for c in have)
    rows = _query(key, f'SELECT {sel} FROM "{table}" LIMIT {limit}')
    head = "".join(f"<th>{_esc(c)}</th>" for c in have)
    trs = "".join("<tr>" + "".join(f"<td class=wrap>{_cell(v, 60)}</td>" for v in r) + "</tr>" for r in rows)
    link = f"<a href='/db/{key}/{table}'>open in DB browser →</a>"
    return (f"<div class=tablewrap><table><thead><tr>{head}</tr></thead>"
            f"<tbody>{trs or f'<tr><td colspan={len(have)} class=muted>no rows</td></tr>'}</tbody></table></div>"
            f"<div class=hint>{link}</div>")


@app.get("/users", response_class=HTMLResponse)
async def users_view(request: Request):
    cp_up = DB_STATUS.get("controlplane", {}).get("error") is None
    if not cp_up:
        body = "<div class=warn>Control-plane DB not mounted/reflected — tenant &amp; entitlement views unavailable.</div>"
        return HTMLResponse(page("/users", "Users", body))

    body = (
        "<p class=hint>Who can use what, and what they used — from the control-plane "
        "(tenants → projects → API keys → activations → usage) and studio users.</p>"
        + "<h2>Tenants</h2>" + _simple_table("controlplane", "tenants", ["id", "name", "created_at"])
        + "<h2>Projects</h2>" + _simple_table("controlplane", "projects", ["id", "tenant_id", "name", "created_at"])
        + "<h2>API keys</h2>" + _simple_table("controlplane", "api_keys", ["id", "project_id", "prefix", "created_at"])
        + "<h2>Activations (entitlements)</h2>" + _simple_table("controlplane", "activations", ["id", "project_id", "connector_id", "created_at"])
        + "<h2>Recent usage</h2>" + _simple_table("controlplane", "usage_events", ["id", "project_id", "tool", "ts"], limit=30)
        + "<h2>Studio users</h2>" + _simple_table("studio", "users", ["email", "tenant_id", "project_id"])
    )
    return HTMLResponse(page("/users", "Users", body))


# --- Queue (Procrastinate) — monitor + control ----------------------------


@app.get("/queue", response_class=HTMLResponse)
async def queue_view(request: Request, msg: str = "", status: str = ""):
    async with httpx.AsyncClient() as c:
        ov = await _safe_get(c, f"{settings.datasets_url}/admin/queue")
        jq = f"{settings.datasets_url}/admin/queue/jobs?limit=100" + (f"&status={status}" if status else "")
        jobs = await _safe_get(c, jq)
        act = await _safe_get(c, f"{settings.datasets_url}/admin/queue/activity?limit=50")

    if not _ok(ov):
        body = _flash(msg) + "<div class=warn>큐 정보를 불러오지 못했습니다 (datasets 연결 확인).</div>"
        return HTMLResponse(page("/queue", "Queue", body))
    if ov.get("error"):
        # the overview rendered but the queue DB was unreachable — still show the cron schedule.
        note = f"<div class=warn>큐 DB 연결 실패: {_esc(ov['error'])} — 워커/Postgres 상태를 확인하세요.</div>"
    else:
        note = ""

    totals = ov.get("totals") or {}
    tiles = "".join(tile(QUEUE_STATUS_LABEL[k], totals.get(k, 0), "●", f"/queue?status={k}", small=True)
                    for k in ("todo", "doing", "succeeded", "failed") )

    # periodic cron sweeps + a run-now button each
    sweeps = ""
    for s in (ov.get("periodic") or []):
        sweeps += (f"<tr><td>{_esc(s['label'])}</td><td><code>{_esc(s['cron'])}</code></td>"
                   f"<td class=muted>{_esc(s.get('source') or '')}</td>"
                   f"<td><form class=ops method=post action='/ops/queue/sweep/{_esc(s['pipeline_id'])}'>"
                   f"<button class=p>지금 수집 ▶</button></form></td></tr>")
    sweeps_html = ("<div class=tablewrap><table><thead><tr><th>파이프라인</th><th>크론</th><th>원천</th>"
                   f"<th></th></tr></thead><tbody>{sweeps}</tbody></table></div>")

    # live jobs with retry/cancel controls
    job_list = jobs.get("jobs") if isinstance(jobs, dict) else []
    rows = ""
    for j in (job_list or []):
        st = j.get("status")
        args = j.get("args") or {}
        scope = f"{args.get('pipeline_id', j.get('task'))} · {args.get('market', '')}"
        tcount = len(args.get("tickers") or []) if isinstance(args.get("tickers"), list) else ""
        ctl = f"<a class='ops linkbtn' href='/queue/job/{_esc(j['id'])}'>로그</a>"
        if st == "failed":
            ctl += (f"<form class=ops method=post action='/ops/queue/jobs/{_esc(j['id'])}/retry'>"
                    f"<button class=p>재시도</button></form>")
        if st in ("todo", "doing", "failed"):
            ctl += (f"<form class=ops method=post action='/ops/queue/jobs/{_esc(j['id'])}/cancel'>"
                    f"<button class=danger>취소</button></form>")
        rows += (
            f"<tr><td>{_esc(j['id'])}</td><td>{badge(_esc(j.get('task')))}</td>"
            f"<td class=muted>{_esc(j.get('queue'))}</td>"
            f"<td class=wrap>{_esc(scope)}{f' · {tcount}종목' if tcount else ''}</td>"
            f"<td>{badge(QUEUE_STATUS_LABEL.get(st, st), QUEUE_STATUS_CLASS.get(st, ''))}</td>"
            f"<td>{_esc(j.get('attempts'))}</td>"
            f"<td class=muted>{_esc((j.get('scheduled_at') or '')[:19])}</td>"
            f"<td><div class=opsrow>{ctl}</div></td></tr>"
        )
    jobs_html = ("<div class=tablewrap><table><thead><tr><th>#</th><th>task</th><th>queue</th><th>scope</th>"
                 "<th>status</th><th>시도</th><th>scheduled</th><th></th></tr></thead>"
                 f"<tbody>{rows or '<tr><td colspan=8 class=muted>작업 없음</td></tr>'}</tbody></table></div>")

    filt = " · ".join(
        (f"<b>{QUEUE_STATUS_LABEL[k]}</b>" if status == k else f"<a href='/queue?status={k}'>{QUEUE_STATUS_LABEL[k]}</a>")
        for k in ("todo", "doing", "succeeded", "failed")
    )
    running = bool(totals.get("doing") or totals.get("todo"))
    act_list = act.get("activity") if isinstance(act, dict) else []
    act_html = ("<h2>활동 로그" + (" · ⟳ live" if running else "") + "</h2>"
                + "<p class=hint>실행 중인 파이프라인이 무엇을, 어디서, 어떤 결과로 수집하는지 실시간으로 보여줍니다.</p>"
                + _activity_feed(act_list or []))
    body = (_flash(msg) + note
            + "<p class=hint>Procrastinate 큐 — Postgres가 브로커입니다(Redis 없음). 워커가 크론 스윕을 돌려 "
              "작업을 큐에 넣고 재시도와 함께 처리합니다. 여기서 작업을 모니터링하고 재시도/취소할 수 있어요.</p>"
            + "<div class=tiles>" + tiles + "</div>"
            + act_html
            + "<h2>자동 수집 (크론 스윕)</h2>" + sweeps_html
            + f"<h2>작업 {'· ⟳ live' if running else ''}</h2>"
            + f"<div class=hint>필터: 전체 · {filt}"
            + (f" · <a href='/queue'>초기화</a>" if status else "") + "</div>"
            + jobs_html)
    return HTMLResponse(page("/queue", "Queue", body, refresh=running))


# Procrastinate event types → (icon, css class) for the timeline.
_EVENT_STYLE = {
    "deferred": ("⏳", ""), "scheduled": ("⏱", ""), "started": ("▶", "run"),
    "deferred_for_retry": ("↻", "warn"), "succeeded": ("✓", "ok"),
    "failed": ("✗", "err"), "abort_requested": ("🛑", "warn"), "aborted": ("⛔", "err"),
    "cancelled": ("⊘", ""),
}


@app.get("/queue/job/{job_id}", response_class=HTMLResponse)
async def queue_job_detail(request: Request, job_id: int):
    """Diagnostic page for one queue job — the Procrastinate event timeline + the linked pipeline
    run's IngestionJob error note. This is where 'filing_text가 왜 안 되는지' becomes visible."""
    async with httpx.AsyncClient() as c:
        d = await _safe_get(c, f"{settings.datasets_url}/admin/queue/jobs/{job_id}")
    if not _ok(d):
        return HTMLResponse(page("/queue", f"Job {job_id}",
                                 "<div class=warn>작업 정보를 불러오지 못했습니다.</div>"
                                 "<p><a href='/queue'>← 큐로</a></p>"))
    job = d.get("job") or {}
    args = job.get("args") or {}
    scope = f"{args.get('pipeline_id', job.get('task'))} · {args.get('market', '')}"
    tcount = len(args.get("tickers") or []) if isinstance(args.get("tickers"), list) else ""
    st = job.get("status")

    head = (f"<div class=tablewrap><table><tbody>"
            f"<tr><td class=muted>작업</td><td>#{_esc(job.get('id'))} · {badge(_esc(job.get('task')))} "
            f"· {_esc(scope)}{f' · {tcount}종목' if tcount else ''}</td></tr>"
            f"<tr><td class=muted>상태</td><td>{badge(QUEUE_STATUS_LABEL.get(st, st), QUEUE_STATUS_CLASS.get(st, ''))} "
            f"· 시도 {_esc(job.get('attempts'))}</td></tr>"
            f"<tr><td class=muted>lock</td><td><code>{_esc(job.get('lock') or '')}</code></td></tr>"
            f"<tr><td class=muted>scheduled</td><td class=muted>{_esc((job.get('scheduled_at') or '')[:19])}</td></tr>"
            f"</tbody></table></div>")

    # event timeline — the smoking gun (deferred → started → abort_requested → failed, etc.)
    ev_rows = ""
    for e in (d.get("events") or []):
        icon, cls = _EVENT_STYLE.get(e.get("type"), ("•", ""))
        ev_rows += (f"<tr><td>{icon}</td><td>{badge(_esc(e.get('type')), cls)}</td>"
                    f"<td class=muted>{_esc((e.get('at') or '')[:23].replace('T', ' '))}</td></tr>")
    ev_html = ("<h2>이벤트 타임라인</h2><div class=tablewrap><table><thead><tr><th></th><th>type</th>"
               f"<th>at (UTC)</th></tr></thead><tbody>{ev_rows or '<tr><td colspan=3 class=muted>이벤트 없음</td></tr>'}"
               "</tbody></table></div>")

    # linked IngestionJob — the per-pipeline run outcome + error note (e.g. 'FAILED ReadTimeout ×N')
    ing = d.get("ingestion")
    if ing:
        ing_status = ing.get("status")
        err = ing.get("error")
        ing_html = (
            "<h2>파이프라인 실행 (IngestionJob)</h2>"
            f"<div class=tablewrap><table><tbody>"
            f"<tr><td class=muted>상태</td><td>{badge(_esc(ing_status), JOB_STATUS_CLASS.get(ing_status, ''))} "
            f"· {_esc(ing.get('done'))}/{_esc(ing.get('total'))} 처리 · {_esc(ing.get('rows'))} chunks</td></tr>"
            f"<tr><td class=muted>시작</td><td class=muted>{_esc((ing.get('started_at') or '')[:19])} "
            f"→ {_esc((ing.get('ended_at') or '—')[:19])}</td></tr>"
            f"</tbody></table></div>"
            + (f"<div class='logbox {('err' if ing_status == 'error' else '')}'>{_esc(err)}</div>"
               if err else "<p class=muted>기록된 오류 메모가 없습니다.</p>"))
    else:
        ing_html = ("<h2>파이프라인 실행 (IngestionJob)</h2>"
                    "<p class=muted>이 작업과 매칭되는 IngestionJob 기록이 없습니다 "
                    "(작업이 시작 전 취소되었거나 기록 전 종료됨).</p>")

    # live activity feed — what the run is fetching, from where, with what result (newest first)
    running = st in ("doing", "todo")
    act_html = ("<h2>활동 로그" + (" · ⟳ live" if running else "") + "</h2>"
                + _activity_feed(d.get("activity") or []))

    body = (f"<p><a href='/queue'>← 큐로</a></p><h1 style='margin:0 0 4px'>작업 #{_esc(job_id)} 로그</h1>"
            + head + act_html + ev_html + ing_html)
    return HTMLResponse(page("/queue", f"Job {job_id}", body, refresh=running))


def _activity_feed(acts: list) -> str:
    """Render the granular pipeline-activity lines as a monospace live log (newest first)."""
    if not acts:
        return "<p class=muted>아직 활동 기록이 없습니다 (작업이 시작되면 실시간으로 표시됩니다).</p>"
    lines = ""
    for a in acts:
        lv = a.get("level")
        cls = "err" if lv == "error" else ("warn" if lv == "warn" else "")
        t = (a.get("at") or "")[11:19]      # HH:MM:SS
        mk = a.get("market") or ""
        lines += (f"<div class='actline {cls}'><span class=at>{_esc(t)}</span>"
                  f"<span class=mk>{_esc(mk)}</span><span class=msg>{_esc(a.get('message'))}</span></div>")
    return f"<div class='actfeed'>{lines}</div>"


@app.post("/ops/queue/sweep/{pipeline_id}")
async def ops_queue_sweep(request: Request, pipeline_id: str):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/queue/sweep/{pipeline_id}", timeout=30)
        ok = r.status_code == 200 and (r.json() or {}).get("deferred")
    return RedirectResponse(f"/queue?msg={pipeline_id}+{'enqueued' if ok else 'failed'}", status_code=303)


@app.post("/ops/queue/jobs/{job_id}/retry")
async def ops_queue_retry(request: Request, job_id: int):
    async with httpx.AsyncClient() as c:
        await c.post(f"{settings.datasets_url}/admin/queue/jobs/{job_id}/retry", timeout=20)
    return RedirectResponse(f"/queue?msg=job+{job_id}+retried", status_code=303)


@app.post("/ops/queue/jobs/{job_id}/cancel")
async def ops_queue_cancel(request: Request, job_id: int):
    async with httpx.AsyncClient() as c:
        await c.post(f"{settings.datasets_url}/admin/queue/jobs/{job_id}/cancel", timeout=20)
    return RedirectResponse(f"/queue?msg=job+{job_id}+cancel+requested", status_code=303)


@app.post("/ops/backfill")
async def ops_backfill(request: Request, preset: str = Form(""), market: str = Form("US"),
                       tickers: str = Form(""), precompute: str = Form("")):
    tick = [t.strip() for t in tickers.replace(",", " ").split() if t.strip()] or None
    if preset:
        payload, label = {"preset": preset, "deep": True}, preset
    else:
        payload, label = {"market": market, "tickers": tick, "deep": True}, f"{market}+{'+'.join(tick) if tick else '(no+tickers)'}"
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/backfill", json=payload, timeout=20)
        ok = r.status_code == 200
        ev = ""
        if precompute:
            pc = {"preset": preset} if preset else {"market": market, "tickers": tick}
            pr = await c.post(f"{settings.datasets_url}/admin/evidence-docs", json=pc, timeout=20)
            ev = "+evidence" if pr.status_code == 200 and pr.json().get("started") else "+(evidence+failed)"
    return RedirectResponse(f"/pipelines?msg=backfill+{'started' if ok else 'failed'}+{label}{ev}", status_code=303)


@app.post("/ops/pipelines/run")
async def ops_pipelines_run(request: Request, preset: str = Form(""), market: str = Form("US"),
                           tickers: str = Form(""), pipelines: list[str] = Form(default=[])):
    """PH-PIPE unified backfill: run the selected pipelines over a preset (or custom tickers)."""
    tick = [t.strip() for t in tickers.replace(",", " ").split() if t.strip()]
    if tick:
        payload, label = {"market": market, "tickers": tick, "pipelines": pipelines}, f"{market}:{len(tick)}t"
    else:
        payload, label = {"preset": preset, "pipelines": pipelines}, preset
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/pipelines/run", json=payload, timeout=20)
        ok = r.status_code == 200 and r.json().get("started")
    pl = "+".join(pipelines) if pipelines else "default"
    return RedirectResponse(f"/pipelines?msg=수집+{'시작' if ok else '실패'}+{label}+[{pl}]", status_code=303)


@app.post("/ops/news")
async def ops_news(request: Request, market: str = Form("US"), tickers: str = Form("")):
    tick = [t.strip() for t in tickers.replace(",", " ").split() if t.strip()] or None
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/news/ingest", json={"market": market, "tickers": tick}, timeout=20)
        ok = r.status_code == 200
    label = f"{market}+{'+'.join(tick) if tick else 'market'}"
    return RedirectResponse(f"/pipelines?msg=news+ingest+{'started' if ok else 'failed'}+{label}", status_code=303)


@app.post("/ops/selftest")
async def ops_selftest(request: Request):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{settings.datasets_url}/admin/selftest", timeout=60)
        j = r.json() if r.status_code == 200 else {}
    summary = f"selftest: {j.get('passed', '?')} passed / {j.get('failed', '?')} failed / {j.get('skipped', '?')} skipped"
    return RedirectResponse(f"/pipelines?msg={summary.replace(' ', '+')}", status_code=303)


@app.post("/ops/rag/ingest")
async def ops_rag_ingest(request: Request, text: str = Form(...), source: str = Form("admin"),
                         ticker: str = Form(""), url: str = Form("")):
    doc = {"text": text, "source": source}
    if ticker:
        doc["ticker"] = ticker
    if url:
        doc["url"] = url
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.rag_url}/rag/ingest", json={"documents": [doc]}, timeout=30)
        n = (r.json() or {}).get("chunks", "?") if r.status_code == 200 else f"err {r.status_code}"
    return RedirectResponse(f"/pipelines?msg=rag+ingested+{n}+chunks", status_code=303)


@app.post("/ops/rag/search", response_class=HTMLResponse)
async def ops_rag_search(request: Request, query: str = Form(...)):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.rag_url}/rag/search", json={"query": query, "top_k": 5}, timeout=30)
        hits = (r.json() or {}).get("hits", []) if r.status_code == 200 else []
    rows = "".join(
        f"<tr><td>{_esc(round(h.get('score', 0), 3))}</td><td>{_esc((h.get('provenance') or {}).get('source'))}</td>"
        f"<td class=wrap>{_cell(h.get('text', ''), 200)}</td></tr>" for h in hits
    ) or "<tr><td colspan=3 class=muted>no hits</td></tr>"
    body = (f"<div class=crumb><a href=/pipelines>← Pipelines</a></div><h2>RAG search · “{_esc(query)}”</h2>"
            f"<div class=tablewrap><table><thead><tr><th>score</th><th>source</th><th>text</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")
    return HTMLResponse(page("/pipelines", "RAG search", body))
