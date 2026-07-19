"""ValueGraph admin — operations console.

A left-nav mission-control over the whole platform, organized by operator
job-to-be-done (Overview · Catalog · Pipelines · Runs · Queue · Data · Users · DB browser):

* **Catalog** — what the service offers, live from the manifest: every data
  source/connector, each resource → REST path → MCP tool, RAG + agent backends.
* **Pipelines** — every ingest/precompute job as a live progress card + controls
  (델타/전체 수집 방식 + OpenDART 쿼터 카드 포함).
* **Runs (수집 이력)** — the FULL run history, filterable + paged; each run opens a
  verbose detail view (원인별 실패 + 활동 로그 전체).
* **Data / Users** — ingestion-store + RAG health; tenants/projects/keys/activations.
* **DB browser** — our own styled CRUD over every reflected service table (no
  sqladmin → no unstyled raw-HTML fallback).

One session login gates everything (a guard middleware).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from hmac import compare_digest

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from sqlalchemy import text as sa_text
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from adminpanel.clients import _ok, _safe_get
from adminpanel.config import assert_production_secrets, settings
from adminpanel.security import LoginThrottle, ip_allowed
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


@asynccontextmanager
async def _lifespan(_: FastAPI):
    assert_production_secrets()  # SC-0/CR-10: production은 dev 크레덴셜·세션 시크릿으로 기동 불가
    yield


app = FastAPI(title="Amber Admin", lifespan=_lifespan)
install_request_logging(app)

from adminpanel import db_browser  # noqa: E402
app.include_router(db_browser.router)


# --- auth -----------------------------------------------------------------
# SC-0.2/CR-10: brute-force throttle for the single admin credential (in-memory; single process).
_login_throttle = LoginThrottle()


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy the real client is the first hop of X-Forwarded-For. Only trust this
    # when the panel actually sits behind a trusted proxy (the operator sets the allowlist to match).
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


async def _guard(request: Request, call_next):
    p = request.url.path
    if p == "/healthz":  # container healthcheck (loopback) — never gate it
        return await call_next(request)
    # SC-0.2: source-IP allowlist (empty → allow all). Refuse before the login form.
    if not ip_allowed(_client_ip(request), settings.adminui_ip_allowlist):
        return PlainTextResponse("forbidden", status_code=403)
    if p in ("/login", "/logout"):
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
    ip = _client_ip(request)
    if _login_throttle.locked(ip):
        return HTMLResponse(
            login_page("<div class=e>Too many attempts — try again in a few minutes.</div>"),
            status_code=429,
        )
    # timing-safe compare (utf-8 bytes so non-ASCII secrets don't raise)
    ok = compare_digest(username.encode(), settings.adminui_username.encode()) & compare_digest(
        password.encode(), settings.adminui_password.encode()
    )
    if ok:
        _login_throttle.clear(ip)
        request.session["authed"] = True
        return RedirectResponse("/", status_code=302)
    _login_throttle.record_failure(ip)
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
        quota = await _safe_get(c, f"{settings.datasets_url}/admin/quota")

    conns = catalog.get("connectors") or []
    tool_count = sum(len(cn.get("resources") or []) for cn in conns) if conns else catalog.get("count", "?")
    job_list = jobs.get("jobs") if isinstance(jobs, dict) else []
    running = [j for j in (job_list or []) if j.get("status") == "running"]
    errored = [j for j in (job_list or []) if j.get("status") == "error"][:5]
    q_totals = queue.get("totals") or {}
    q_pending, q_doing = q_totals.get("todo", 0), q_totals.get("doing", 0)

    # OpenDART 남은 쿼터 — 살아 있는 키들의 최소 남은 호출 수(보수적), 전부 막혔으면 '소진'.
    qkeys = (quota.get("keys") or []) if _ok(quota) else []
    alive = [int(k.get("remaining") or 0) for k in qkeys if not k.get("blocked")]
    if not qkeys:
        q_left, q_small = "—", False
    elif not alive or max(alive) <= 0:
        q_left, q_small = "소진", True
    else:
        q_left, q_small = f"{min(alive):,}", False

    tiles = "".join([
        tile("data sources", len(conns) if conns else "?", "◈", "/catalog"),
        tile("catalog tools", tool_count, "⚙", "/catalog"),
        tile("RAG embedder", raginfo.get("embedding_backend", "—"), "▤", "/data", small=True),
        tile("queue pending", q_pending if _ok(queue) else "—", "⚙", "/queue"),
        tile("store facts", stats.get("total_facts", "—"), "▦", "/data"),
        tile("queue running", q_doing if _ok(queue) else len(running), "●", "/queue"),
        tile("OpenDART 남은 쿼터", q_left, "🔑", "/pipelines", small=q_small),
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
                               ("/runs", "수집 이력 →"), ("/queue", "Queue →"), ("/data", "Data →"),
                               ("/users", "Users →"), ("/db", "DB browser →")])
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
def _fmt_duration(started: str | None, ended: str | None) -> str:
    """ISO 시각 두 개 → '1시간 5분' / '2분 30초' / '12초' — 사람이 읽는 실행 소요 시간."""
    if not started or not ended:
        return ""
    from datetime import datetime
    try:
        s = datetime.fromisoformat(started.replace("Z", ""))
        e = datetime.fromisoformat(ended.replace("Z", ""))
        secs = max(0, int((e - s).total_seconds()))
    except ValueError:
        return ""
    if secs >= 3600:
        return f"{secs // 3600}시간 {secs % 3600 // 60}분"
    if secs >= 60:
        return f"{secs // 60}분 {secs % 60}초"
    return f"{secs}초"


def _quota_card(quota: dict) -> str:
    """OpenDART 일일 쿼터 카드 — 키별 오늘 사용량 바 + 남은 호출 + 소진 배지.

    수치는 우리가 계측한 호출 수(datasets·worker 공용 스토어) 기준이라 포털 실측과 오차가
    있을 수 있다 — 그래서 카드에도 그대로 적어 준다(무 날조)."""
    if not _ok(quota):
        return ("<div class=card><h3>🔑 OpenDART 쿼터</h3>"
                "<div class=sub>쿼터 정보를 불러오지 못했어요 — datasets 연결을 확인하세요.</div></div>")
    keys = quota.get("keys") or []
    if not keys:
        return ("<div class=card><h3>🔑 OpenDART 쿼터 " + badge("키 없음", "warn") + "</h3>"
                "<div class=sub>등록된 OpenDART 키가 없어요. <code>OPENDART_API_KEYS</code>에 키를 여러 개 "
                "넣으면 자동 로테이션으로 일일 한도를 나눠 써요 (키 하나면 <code>OPENDART_API_KEY</code>도 돼요).</div>"
                "</div>")
    rows, total_used, total_limit = "", 0, 0
    for k in keys:
        used, lim = int(k.get("used_today") or 0), int(k.get("limit") or 0)
        remaining = int(k.get("remaining") or 0)
        total_used, total_limit = total_used + used, total_limit + lim
        blocked = bool(k.get("blocked"))
        kind = "err" if blocked else ("warn" if lim and used >= lim * 0.8 else "ok")
        state = (badge("일일 한도 소진 · KST 자정 리셋", "err") if blocked
                 else f"<span class=qnum>남음 {remaining:,}</span>")
        rows += (f"<div class=qrow><code>{_esc(k.get('key'))}</code>{progress(used, lim, kind)}"
                 f"<span class=qnum>{used:,}/{lim:,}</span>{state}</div>")
    # 최근 14일 이력 — 날짜별 합계로 접어서 (verbose는 클릭해서)
    by_day: dict[str, int] = {}
    for h in (quota.get("history") or []):
        d = str(h.get("day") or "")
        by_day[d] = by_day.get(d, 0) + int(h.get("calls") or 0)
    hist_html = ""
    if by_day:
        lines = "".join(
            f"<tr><td class=mono>{_esc(d)}</td><td class=mono style='text-align:right'>{n:,}</td></tr>"
            for d, n in sorted(by_day.items(), reverse=True))
        hist_html = ("<details class=errlog style='margin-top:8px'>"
                     "<summary style='color:var(--muted)'>최근 14일 사용 이력</summary>"
                     "<div class=tablewrap style='margin-top:6px'><table><thead><tr><th>날짜 (KST)</th>"
                     f"<th>호출</th></tr></thead><tbody>{lines}</tbody></table></div></details>")
    day = quota.get("day_kst") or ""
    total_kind = "err" if total_limit and total_used >= total_limit else "ok"
    return ("<div class=card><h3>🔑 OpenDART 쿼터 " + badge(f"오늘 {total_used:,}/{total_limit:,}", total_kind)
            + (f" <span class=muted>{_esc(day)} KST</span>" if day else "") + "</h3>"
            "<div class=sub>키별 오늘 호출량이에요 — 한도가 다 찬 키는 자동으로 쉬고 KST 자정에 다시 살아나요. "
            "수치는 우리가 계측한 호출 수 기준이라 포털 실측과는 오차가 있을 수 있어요.</div>"
            + rows + hist_html + "</div>")


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
    # 델타 설명 — 이 파이프라인의 델타 모드가 무엇을 건너뛰는지 (레지스트리의 `delta` 필드 그대로).
    delta_line = (f"<div class='sub muted'>델타: {_esc(p['delta'])}</div>" if p.get("delta") else "")
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
        # cron-scheduled pipeline → one-click sweep over the configured universe (기본 델타 —
        # datasets의 sweep 엔드포인트가 mode=delta 기본이라 새로 나온 것만 수집해요).
        run_now = (f"<form class=ops method=post action='/ops/queue/sweep/{_esc(pid)}'>"
                   f"<button class=p>지금 수집(델타) ▶</button></form>")
    else:
        # manual-only pipeline (no auto-cron — these are rate-limited/metered: e.g. API Ninjas
        # transcripts, Document AI per-page). Run is TICKER-SCOPED on purpose: a full-universe run
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
        f"{delta_line}"
        f"<div class=flow><span class=pill>{_esc(p.get('source'))}</span> <span class=arrow>→</span> "
        f"<span class=pill><code>{_esc(p.get('store'))}</code></span> {markets}</div>"
        f"{detail}{last}<div class=opsrow>{run_now}</div></div>"
    )


@app.get("/pipelines", response_class=HTMLResponse)
async def pipelines(request: Request, msg: str = ""):
    async with httpx.AsyncClient() as c:
        pdata = await _safe_get(c, f"{settings.datasets_url}/admin/pipelines")
        jobs = await _safe_get(c, f"{settings.datasets_url}/admin/jobs?limit=8")   # 최근 몇 건만 — 전체는 /runs
        universes = await _safe_get(c, f"{settings.datasets_url}/admin/universes")
        quota = await _safe_get(c, f"{settings.datasets_url}/admin/quota")

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

    # --- 홈 트렌드 피드 (ask-feed) 통합 카드: 4개 스코프의 캐시 상태·신선도·카드 프리뷰 + 수동 갱신 ---
    # 백그라운드 인프로세스 루프(studio-api)라 Procrastinate 큐(/queue)엔 안 뜨므로, 여기서 생성 시각·
    # 신선도·실제 생성된 카드 프리뷰를 보고, 전체 payload는 DB 브라우저에서 본다.
    # (scope, 라벨, cadence 라벨, stale 임계 초) — 표시 순서 그대로.
    _feed_scopes = (
        ("news_feed", "🌍 Macro Trends", "뉴스 5분", 600),        # 5분 주기 → 10분 넘으면 오래됨
        ("earnings_radar", "📅 어닝 레이더", "섹션 1시간", 7200),   # 1시간 주기 → 2시간 넘으면 오래됨
        ("guru_flows", "🐘 투자거장·수급", "섹션 1시간", 7200),
        ("history_lab", "🕰️ 히스토리 랩", "섹션 1시간", 7200),
    )
    feed_rows_html = ""
    try:
        from datetime import datetime as _dt
        import json as _json
        by_scope: dict[str, tuple] = {}
        eng = ENGINES.get("studio")
        if eng is not None:
            with eng.connect() as conn:
                for r in conn.execute(sa_text(
                        "SELECT scope, generated_at, payload FROM ask_feed_cache "
                        "WHERE scope IN ('news_feed','earnings_radar','guru_flows','history_lab')")).all():
                    by_scope[r[0]] = (r[1], r[2])
        for scope, label, cadence, stale_after in _feed_scopes:
            row = by_scope.get(scope)
            if not row:
                # 아직 캐시 행이 없는 스코프 — '생성 전' 배지 + 안내
                feed_rows_html += (
                    "<div class=feedrow style='margin:8px 0'><div class=flow>"
                    f"<span class=pill>{_esc(label)}</span>{badge('생성 전')}"
                    f"<span class=pill>{_esc(cadence)}</span></div>"
                    "<div class=sub>(아직 생성 안 됨)</div></div>")
                continue
            gen_at, payload = row
            cards = (_json.loads(payload) if payload else {}).get("cards") or []
            gen_str = str(gen_at)[:16].replace("T", " ")           # yyyy-mm-dd hh:mm
            # 신선도: generated_at(UTC naive)과 지금을 비교, cadence별 임계로 판정.
            fresh = ""
            try:
                g = _dt.fromisoformat(str(gen_at).replace("Z", ""))
                age = (_dt.utcnow() - g).total_seconds()
                fresh = badge("신선", "ok") if age <= stale_after else badge("오래됨", "warn")
            except Exception:  # noqa: BLE001 — 파싱 불가한 시각이면 배지 생략
                pass
            # 실제 생성된 카드 프리뷰 — 상위 3개의 question (≈60자 절단, 이스케이프)
            qs = [(c.get("question") or "").strip() for c in cards[:3] if isinstance(c, dict)]
            qs = [q for q in qs if q]
            if qs:
                prev = "".join(
                    f"<div class=sub style='margin:1px 0 0 2px'>· {_esc(q[:60] + ('…' if len(q) > 60 else ''))}</div>"
                    for q in qs)
            else:
                prev = "<div class=sub>(아직 생성 안 됨)</div>"
            feed_rows_html += (
                "<div class=feedrow style='margin:8px 0'><div class=flow>"
                f"<span class=pill>{_esc(label)}</span>"
                f"<span class=pill>카드 <b>{len(cards)}</b>개</span>"
                f"<span class=pill>{_esc(gen_str)}</span>{fresh}"
                f"<span class=pill>{_esc(cadence)}</span></div>"
                f"{prev}</div>")
    except Exception:  # noqa: BLE001 — 첫 부팅엔 테이블이 없을 수 있음
        pass
    feed_card = (
        "<div class=card><h3>🏠 홈 트렌드 피드 (ask-feed)</h3>"
        + (feed_rows_html or "<div class=sub>아직 생성된 피드가 없어요.</div>")
        + "<div class=sub>물어보기 첫 화면의 질문 카드를 만들어요 — 뉴스·거시(5분)와 어닝·거장·히스토리 "
          "섹션(1시간)을 시장 전체 공유 캐시(studio <code>ask_feed_cache</code>)에 쌓아요. 접속 시 오래됐으면 "
          "read-through로 1회 갱신되고, 데이터가 그대로면(서명 동일) LLM 없이 끝나요. 히스토리 랩의 지수 "
          "가격 백필(^GSPC·^KS11·^VIX)은 아래 <b>가격(OHLCV)</b> 파이프라인에 포함돼요.</div>"
        "<div class=sub muted>백그라운드 인프로세스 루프(studio-api)라 Procrastinate 큐(<a href=/queue>/queue</a>)엔 "
        "안 뜨고, 생성 시각·프리뷰는 여기서, 전체 payload는 "
        "<a href='/db/studio/ask_feed_cache'>DB 브라우저</a>에서 봐요.</div>"
        "<div class=opsrow>"
        "<form class=ops method=post action=/ops/askfeed/refresh><button class=p>뉴스 갱신 ▶</button></form>"
        "<form class=ops method=post action=/ops/askfeed/refresh-sections><button class=p>섹션 갱신 ▶</button></form>"
        "<form class=ops method=post action=/ops/askfeed/refresh-all><button class=p>전부 갱신 ▶</button></form>"
        "</div></div>"
    )

    # --- 회사 로고: 유니버스 자동 채우기(하이브리드 해석기) + 놓친 종목 수동 업로드 ---
    logo_card = (
        "<div class=card><h3>🖼️ 회사 로고</h3>"
        "<div class=sub>종목 로고를 하이브리드 해석기(Logo.dev→FMP→파비콘)로 유니버스 전체에 미리 채워요. "
        "못 받은 종목은 아래에서 직접 올리면 그 종목의 모든 화면에 바로 적용돼요(무 날조 — 없으면 모노그램).</div>"
        # 자동 채우기: 기존 /ops/pipelines/run 재사용 (pipelines=logos)
        "<div class=opsrow><form class=ops method=post action=/ops/pipelines/run>"
        "<input type=hidden name=pipelines value=logos>"
        "<select name=preset>"
        "<option value='us_sp500,kr_kospi200,kr_kosdaq150'>US·KR 주요 (S&P500+코스피200+코스닥150)</option>"
        "<option value='us_sp500'>US · S&amp;P 500</option>"
        "<option value='kr_kospi200,kr_kosdaq150'>KR · 코스피200+코스닥150</option>"
        "<option value='kr_listed'>KR · 상장 전체 (OpenDART)</option>"
        "</select> <button class=p>로고 채우기 ▶</button></form></div>"
        # 수동 업로드: 해석기가 놓친 종목(특히 KR)
        "<div class=muted style='margin-top:8px'>못 받은 종목 직접 업로드 (PNG·JPEG·WEBP·SVG · 정사각 권장):</div>"
        "<div class=opsrow><form class=ops method=post action=/ops/logos/upload enctype=multipart/form-data>"
        "<select name=market><option value=US>US</option><option value=KR>KR</option></select> "
        "<input name=ticker placeholder='티커 (예: 005930.KS)' required> "
        "<input type=file name=logo accept='image/*' required> "
        "<button class=p>업로드 ▶</button></form></div></div>"
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
    <div class=row><label>수집 방식</label><div class=checks>
      <label class=chk><input type=radio name=mode value=delta checked> 델타 — 새로 나온 것만 (권장)</label>
      <label class=chk><input type=radio name=mode value=full> 전체 — 모두 다시 수집</label></div></div>
    <div class=row><label></label><span class=hint>델타는 이미 수집한 공시·분기·덱을 건너뛰고, 재무는 신선한 종목을 스킵해요 — 쿼터·임베딩을 아껴요.</span></div>
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

    # --- 도구 (부가 기능) — 위쪽은 수집 흐름에 집중하고, 단발성 도구는 맨 아래로 모아요 ---
    rag_tools = """
  <div class=card><h3>RAG ingest</h3><div class=sub>코퍼스에 문서 추가</div>
    <form class=ops method=post action=/ops/rag/ingest>
      <input name=text placeholder="document text" size=22 required>
      <input name=source placeholder=source value=admin size=10><button class=p>Ingest</button></form></div>
  <div class=card><h3>RAG search</h3><div class=sub>시맨틱 프로브</div>
    <form class=ops method=post action=/ops/rag/search>
      <input name=query placeholder="semantic query" size=22 required><button class=p>Search</button></form></div>"""
    tools = "<h2>도구</h2><div class=grid>" + feed_card + logo_card + rag_tools + "</div>"

    body = (_flash(msg)
            + "<p class=hint>모든 데이터 파이프라인을 한곳에서 — 무엇을 어떤 경로로 수집해 어디에 쌓는지, "
              "주기·상태·에러를 시각화합니다. 작업이 도는 동안 자동 새로고침됩니다. "
              "과거 실행의 전체 이력·상세 로그는 <a href=/runs>Runs(수집 이력)</a>에 있어요.</p>"
            + "<h2>큐 스케줄러 · 쿼터</h2><div class=grid>" + queue_banner + _quota_card(quota) + "</div>"
            + "<h2>파이프라인</h2><div class=grid>" + cards + "</div>"
            + backfill
            + f"<h2>수집 작업 · 최근 {len(job_list or [])}건 {'· ⟳ live' if running else ''}</h2>" + jobs_html
            + "<div class=hint style='margin-top:8px'><a href=/runs>전체 이력 → Runs(수집 이력)</a></div>"
            + tools)
    return HTMLResponse(page("/pipelines", "Pipelines", body, refresh=running))


# --- Runs (수집 이력) --------------------------------------------------------
_RUN_STATUS_LABEL = {"running": "실행중", "success": "성공", "error": "실패"}


def _runs_error_cell(j: dict) -> str:
    """이력 테이블의 오류 셀 — 클릭하면 전체 메시지 + 원인별(그룹) 실패 종목까지 펼쳐 보여줘요."""
    err = j.get("error") or ""
    details = j.get("error_details") or []
    if not err and not details:
        return ""
    full = err
    if details:
        lines = []
        for g in details:
            tickers = g.get("tickers") or []
            preview = ", ".join(str(t) for t in tickers[:20]) + ("…" if len(tickers) > 20 else "")
            lines.append(f"{g.get('error')} — {g.get('count')}종목: {preview}")
        full = (err + "\n\n" if err else "") + "\n".join(lines)
    summary = err or f"원인 {len(details)}종"
    return (f"<details class=errlog><summary>{_cell(summary, 60)}</summary>"
            f"<pre>{_esc(full)}</pre></details>")


@app.get("/runs", response_class=HTMLResponse)
async def runs_view(request: Request, kind: str = "", market: str = "", status: str = "", offset: int = 0):
    """수집 이력 — 모든 파이프라인 실행(IngestionJob)을 필터·페이지로 훑어봐요.
    파이프라인 페이지는 최근 몇 건만 보여주고, 전체 과거 런은 여기서 찾아요."""
    limit = 50
    offset = max(0, offset)
    filt_qs = "".join(f"&{k}={v}" for k, v in (("kind", kind), ("market", market), ("status", status)) if v)
    async with httpx.AsyncClient() as c:
        jobs = await _safe_get(c, f"{settings.datasets_url}/admin/jobs?limit={limit}&offset={offset}{filt_qs}")
        pdata = await _safe_get(c, f"{settings.datasets_url}/admin/pipelines")

    registry = pdata.get("pipelines") or []
    kind_label = {p.get("kind"): p.get("label") for p in registry}

    # --- 필터 바 (GET폼 — 새로고침·북마크에 안전) ---
    kind_opts = "<option value=''>전체 파이프라인</option>"
    known_kinds = set()
    for p in registry:
        k = p.get("kind") or ""
        known_kinds.add(k)
        sel = " selected" if kind == k else ""
        kind_opts += f"<option value='{_esc(k)}'{sel}>{_esc(p.get('label'))} · {_esc(k)}</option>"
    if kind and kind not in known_kinds:   # 레지스트리를 못 불러와도 선택값은 유지
        kind_opts += f"<option value='{_esc(kind)}' selected>{_esc(kind)}</option>"
    market_opts = "<option value=''>전체 시장</option>" + "".join(
        f"<option value={m}{' selected' if market == m else ''}>{m}</option>" for m in ("US", "KR"))
    status_opts = "<option value=''>전체 상태</option>" + "".join(
        f"<option value={s}{' selected' if status == s else ''}>{lbl}</option>"
        for s, lbl in _RUN_STATUS_LABEL.items())
    filter_bar = ("<form class=ops method=get action=/runs>"
                  f"<select name=kind>{kind_opts}</select>"
                  f"<select name=market>{market_opts}</select>"
                  f"<select name=status>{status_opts}</select>"
                  "<button class=p>필터 적용</button>"
                  + (" <a href=/runs>초기화</a>" if (kind or market or status) else "")
                  + "</form>")

    # --- 이력 테이블 + 페이저 ---
    job_list = (jobs.get("jobs") or []) if _ok(jobs) else []
    total = int(jobs.get("total") or 0) if _ok(jobs) else 0
    if not _ok(jobs):
        table = "<div class=warn>수집 이력을 불러오지 못했어요 — datasets 연결을 확인하세요.</div>"
    elif not job_list:
        table = "<div class=empty>조건에 맞는 수집 기록이 없어요.</div>"
    else:
        rows = ""
        for j in job_list:
            st = j.get("status")
            cls = JOB_STATUS_CLASS.get(st, "")
            tot, dn = j.get("total") or 0, j.get("done") or 0
            dur = _fmt_duration(j.get("started_at"), j.get("ended_at"))
            plabel = kind_label.get(j.get("kind")) or j.get("kind")
            rows += (
                f"<tr><td><a href='/runs/{_esc(j['id'])}'>#{_esc(j['id'])}</a></td>"
                f"<td>{badge(_esc(plabel))}</td><td>{_esc(j.get('market') or '')}</td>"
                f"<td class=wrap>{_cell(j.get('spec'), 60)}</td>"
                f"<td>{badge(_esc(st), cls)}</td>"
                f"<td><div style='display:flex;align-items:center;gap:8px'>{progress(dn, tot, cls)}"
                f"<span class=muted>{f'{dn}/{tot}' if tot else '—'}</span></div></td>"
                f"<td>{_esc(j.get('rows') if j.get('rows') is not None else '')}</td>"
                f"<td class=muted>{_esc((j.get('started_at') or '')[:19])}{f' · {dur}' if dur else ''}</td>"
                f"<td>{_runs_error_cell(j)}</td></tr>")
        table = ("<div class=tablewrap><table><thead><tr><th>#</th><th>pipeline</th><th>mkt</th><th>spec</th>"
                 "<th>status</th><th>progress</th><th>rows</th><th>started</th><th>error</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table></div>")

    lo = offset + 1 if job_list else 0
    hi = offset + len(job_list)
    pager = f"<div class=pgbar><span class=pg>총 {total:,}건 · {lo}–{hi} 표시</span>"
    if offset > 0:
        pager += f"<a class=pg href='/runs?offset={max(0, offset - limit)}{_esc(filt_qs)}'>← 이전</a>"
    if offset + limit < total:
        pager += f"<a class=pg href='/runs?offset={offset + limit}{_esc(filt_qs)}'>다음 →</a>"
    pager += "</div>"

    running = any(j.get("status") == "running" for j in job_list)
    body = ("<p class=hint>모든 파이프라인 실행의 과거 이력이에요 — 파이프라인·시장·상태로 거르고, "
            "각 실행을 누르면 원인별 실패와 전체 활동 로그까지 자세히 볼 수 있어요.</p>"
            + filter_bar + pager + table + pager)
    return HTMLResponse(page("/runs", "Runs", body, refresh=running))


@app.get("/runs/{job_id}", response_class=HTMLResponse)
async def run_detail(request: Request, job_id: int):
    """한 실행의 VERBOSE 뷰 — 전체 필드 헤더(소요 시간 포함), 원인별(그룹) 실패 종목,
    이 런이 남긴 활동 로그 전체(오래된 순 — 읽는 순서 그대로). 실행 중이면 자동 새로고침해요."""
    job, reachable = None, True
    async with httpx.AsyncClient() as c:
        # datasets에 단건 조회가 없어 이력 페이지를 최신부터 훑어요 (최근 4,000건까지 — 그 밖이면 정직하게 못 찾음).
        offset = 0
        while job is None and offset < 4000:
            d = await _safe_get(c, f"{settings.datasets_url}/admin/jobs?limit=500&offset={offset}")
            if not _ok(d):
                reachable = False
                break
            batch = d.get("jobs") or []
            job = next((x for x in batch if x.get("id") == job_id), None)
            offset += 500
            if not batch or offset >= int(d.get("total") or 0):
                break
        act = await _safe_get(c, f"{settings.datasets_url}/admin/queue/activity?job_id={job_id}&limit=500")

    crumb = "<div class=crumb><a href=/runs>← 수집 이력</a></div>"
    if job is None:
        why = ("datasets에 연결하지 못했어요 — 스택이 떠 있는지 확인하세요." if not reachable
               else "이 실행 기록을 찾지 못했어요 — 너무 오래돼 이력에서 밀려났을 수 있어요.")
        return HTMLResponse(page("/runs", f"Run {job_id}",
                                 crumb + f"<div class=empty>실행 #{_esc(job_id)} — {_esc(why)}</div>"))

    st = job.get("status")
    cls = JOB_STATUS_CLASS.get(st, "")
    tot, dn = job.get("total") or 0, job.get("done") or 0
    dur = _fmt_duration(job.get("started_at"), job.get("ended_at"))
    started = (job.get("started_at") or "")[:19]
    ended = (job.get("ended_at") or "")[:19]
    head = (
        "<div class=tablewrap><table><tbody>"
        f"<tr><td class=kvk>실행</td><td>#{_esc(job.get('id'))} · {badge(_esc(job.get('kind')))} "
        f"· {_esc(job.get('market') or '')}</td></tr>"
        f"<tr><td class=kvk>spec</td><td class=wrap><code>{_esc(job.get('spec') or '')}</code></td></tr>"
        f"<tr><td class=kvk>상태</td><td>{badge(_esc(st), cls)} · 진행 {_esc(dn)}/{_esc(tot)} "
        f"· 수집 {_esc(job.get('rows') if job.get('rows') is not None else '—')}행</td></tr>"
        f"<tr><td class=kvk>시간</td><td class=muted>{_esc(started)} → {_esc(ended or '진행중')}"
        + (f" · <b>{_esc(dur)}</b>" if dur else "") + "</td></tr>"
        "</tbody></table></div>")

    err_html = ""
    if job.get("error") or job.get("error_details"):
        err_html = ("<h2>오류 상세 (원인 → 종목)</h2>"
                    + _error_detail_html(job, job.get("error_details") or [], job.get("error")))

    # 활동 로그 — API는 최신순이라 뒤집어 시간순(오래된 순)으로 (로그는 위→아래로 읽혀야 하니까)
    acts = list(reversed((act.get("activity") or []) if _ok(act) else []))
    running = st == "running"
    act_html = ("<h2>활동 로그 · 전체" + (" · ⟳ live" if running else "") + "</h2>"
                "<p class=hint>이 실행이 무엇을 어디서 가져와 어떤 결과를 냈는지 시간순으로 보여줘요 — "
                "warn은 노란색, error는 빨간색이에요.</p>"
                + _activity_feed(acts))

    links = (f"<div style='margin-top:14px'>"
             f"<span class=pill><a href='/runs?kind={_esc(job.get('kind') or '')}'>이 파이프라인 이력 보기 →</a></span>"
             f"<span class=pill><a href=/runs>전체 이력 →</a></span></div>")

    body = (crumb + f"<h1 style='margin:0 0 4px'>수집 실행 #{_esc(job_id)}</h1>"
            + head + err_html + act_html + links)
    return HTMLResponse(page("/runs", f"Run {job_id}", body, refresh=running))


# --- Shares (V-4) -----------------------------------------------------------
@app.get("/shares", response_class=HTMLResponse)
async def shares_view(request: Request):
    """공유 랭킹 — 뭐가 퍼졌는지(실측 views desc). 그로스의 눈: 다음 짤을 결정하는 데이터."""
    rows, err = [], ""
    try:
        eng = ENGINES.get("studio")
        if eng is None:
            raise RuntimeError("studio DB not mounted")
        with eng.connect() as conn:  # type: ignore[union-attr]
            rows = conn.execute(sa_text(
                "SELECT token, title, kind, COALESCE(views,0) v, revoked, created_at "
                "FROM share_links ORDER BY COALESCE(views,0) DESC, created_at DESC LIMIT 100"
            )).all()
    except Exception as exc:  # noqa: BLE001 — 첫 부팅엔 테이블/컬럼이 없을 수 있음
        err = f"{type(exc).__name__}: {exc}"
    tr = "".join(
        f"<tr><td class=mono style='text-align:right'>{int(v):,}</td>"
        f"<td>{_esc(t or '')[:80]}</td><td class=mono>{_esc(k)}</td>"
        f"<td class=mono>{'해제됨' if r else '공개'}</td>"
        f"<td class=mono>{_esc(str(c)[:16])}</td>"
        f"<td class=mono>/s/{_esc(tok)[:14]}…</td></tr>"
        for tok, t, k, v, r, c in rows)
    body = ((f"<div class=flash>{_esc(err)}</div>" if err else "")
            + "<p class=hint>공유 링크 성과 — 공개 페이지의 실측 뷰(sendBeacon)만 셉니다. "
              "어떤 훅·콘텐츠가 퍼지는지가 다음 콘텐츠 결정의 근거예요.</p>"
            + ("<table class=t><tr><th>views</th><th>제목(훅)</th><th>종류</th><th>상태</th>"
               "<th>생성</th><th>토큰</th></tr>" + tr + "</table>" if tr
               else "<div class=empty>아직 공유가 없어요.</div>"))
    return HTMLResponse(page("/shares", "Shares", body, refresh=True))


# --- Costs (COST-1/2) -------------------------------------------------------
# Providers we call whose per-unit dollar cost the dashboard does NOT track (free tiers, or metered
# only as gateway call counts). Drawn explicitly so the "미추적" gap is visible, never implied $0.
_UNTRACKED_PROVIDERS = ("Yahoo·Stooq·KRX", "SEC EDGAR", "OpenDART·ECOS", "FRED·BLS·DBnomics",
                        "Finnhub·GDELT·NYT", "Google·Naver News")


def _sparkline(values: list[float], width: int = 640, height: int = 48) -> str:
    """Inline-SVG trend line of per-day values — no JS/libs, theme-colored. '데이터 없음' if empty."""
    vals = [max(0.0, float(v)) for v in values]
    if not vals:
        return "<div class=sub>데이터 없음</div>"
    if len(vals) == 1:
        vals = vals * 2
    vmax = max(vals) or 1.0
    step = width / (len(vals) - 1)
    pts = " ".join(f"{i * step:.1f},{height - (v / vmax) * (height - 6) - 3:.1f}" for i, v in enumerate(vals))
    area = f"0,{height} {pts} {width},{height}"
    return (f"<svg viewBox='0 0 {width} {height}' width='100%' height='{height}' "
            f"preserveAspectRatio='none' style='display:block'>"
            f"<polygon points='{area}' fill='var(--accent)' opacity='0.10'/>"
            f"<polyline points='{pts}' fill='none' stroke='var(--accent)' stroke-width='1.5'/></svg>")


_COST_RANGES = (7, 30, 90)


@app.get("/costs", response_class=HTMLResponse)
async def costs_view(request: Request, days: int = 30):
    """API 비용 대시보드 — LLM·임베딩·리랭커·문서AI 원가를 요율표로 달러화(캐시 할인·콜당 과금 포함),
    유저별·서비스별·일별로. 게이트웨이 호출량·고정 구독까지 한눈에. 요율은 데이터(.env PRICING_JSON), 코드가 아니다."""
    from adminpanel.pricing import cost_usd, fixed_costs, is_per_call, registry

    days = days if days in _COST_RANGES else 30
    reg = registry()
    from datetime import datetime as _dt, timedelta as _td
    since = _dt.utcnow() - _td(days=days)
    rows: list = []
    daily_rows: list = []
    conn_rows: list = []
    err = ""
    try:
        eng = ENGINES.get("controlplane")
        if eng is None:
            raise RuntimeError("controlplane DB not mounted")
        with eng.connect() as conn:  # type: ignore[union-attr]
            # dialect-neutral (runtime = Postgres, unit/dev = SQLite): cutoffs as bind params,
            # MAX(estimated) needs an int cast on Postgres (no MAX(bool)).
            rows = conn.execute(sa_text(
                "SELECT service, model, kind, SUM(input_tokens) i, SUM(output_tokens) o, "
                "SUM(calls) c, MAX(CAST(estimated AS INT)) e, "
                "SUM(cached_input_tokens) ci, SUM(thinking_tokens) th FROM llm_usage "
                "WHERE ts >= :since GROUP BY service, model, kind "
                "ORDER BY SUM(input_tokens)+SUM(output_tokens) DESC"
            ), {"since": since}).all()
            daily_rows = conn.execute(sa_text(
                "SELECT date(ts) d, model, SUM(input_tokens) i, SUM(output_tokens) o, "
                "SUM(cached_input_tokens) ci, SUM(calls) c FROM llm_usage "
                "WHERE ts >= :since GROUP BY date(ts), model"
            ), {"since": since}).all()
            conn_rows = conn.execute(sa_text(
                "SELECT connector_id, COUNT(*) n, SUM(cost_units) cu FROM usage_events "
                "WHERE ts >= :since AND connector_id IS NOT NULL "
                "GROUP BY connector_id ORDER BY COUNT(*) DESC LIMIT 30"
            ), {"since": since}).all()
    except Exception as exc:  # noqa: BLE001 — first boot: table may not exist yet
        err = f"{type(exc).__name__}: {exc}"

    # COST-3: background-sweep upstream calls — its own query/except so a not-yet-created table
    # (control-plane not rebuilt) leaves the section empty instead of breaking the whole page.
    sweep_rows: list = []
    try:
        eng2 = ENGINES.get("controlplane")
        if eng2 is not None:
            with eng2.connect() as conn:  # type: ignore[union-attr]
                sweep_rows = conn.execute(sa_text(
                    "SELECT provider, SUM(calls) c FROM provider_usage "
                    "WHERE ts >= :since GROUP BY provider ORDER BY SUM(calls) DESC LIMIT 40"
                ), {"since": since}).all()
    except Exception:  # noqa: BLE001 — provider_usage not created yet → section stays empty
        sweep_rows = []

    # --- price the LLM rows (cache-discounted; per-call products dollarize via calls) -----------
    total_usd, unknown_models = 0.0, set()
    by_service: dict[str, float] = {}
    llm_tr = []
    for s, m, k, i, o, c, e, ci, th in rows:
        usd = cost_usd(m, int(i or 0), int(o or 0), cached_input_tokens=int(ci or 0), calls=int(c or 0))
        if usd is None:
            unknown_models.add(m)
        else:
            total_usd += usd
            by_service[s] = by_service.get(s, 0.0) + usd
        badge = (" <span class=pill>콜당</span>" if is_per_call(m)
                 else " <span class=pill>추정</span>" if e else "")
        cost_cell = "요율 미설정" if usd is None else f"{'~' if e else ''}${usd:,.4f}"
        llm_tr.append(
            f"<tr><td class=mono>{_esc(s)}</td><td class=mono>{_esc(m)}{badge}</td><td>{_esc(k)}</td>"
            f"<td class=mono style='text-align:right'>{int(i or 0):,}</td>"
            f"<td class=mono style='text-align:right'>{int(ci or 0):,}</td>"
            f"<td class=mono style='text-align:right'>{int(o or 0):,}</td>"
            f"<td class=mono style='text-align:right'>{int(th or 0):,}</td>"
            f"<td class=mono style='text-align:right'>{int(c or 0):,}</td>"
            f"<td class=mono style='text-align:right'>{cost_cell}</td></tr>")
    llm_table = ("<table class=t><tr><th>서비스</th><th>모델</th><th>용도</th><th>입력</th>"
                 "<th>캐시</th><th>출력</th><th>생각</th><th>호출</th><th>비용(USD)</th></tr>"
                 + "".join(llm_tr) + "</table>"
                 ) if llm_tr else "<div class=empty>아직 기록이 없어요 — 서비스 재빌드 후 첫 질문/인제스트부터 쌓여요.</div>"

    # --- daily $ series: fill the window so the sparkline x-axis is time-true --------------------
    _today = _dt.utcnow().date()
    day_keys = [str(_today - _td(days=n)) for n in range(days - 1, -1, -1)]
    day_cost = {dk: 0.0 for dk in day_keys}
    for d, model, i, o, ci, c in daily_rows:
        u = cost_usd(model, int(i or 0), int(o or 0), cached_input_tokens=int(ci or 0), calls=int(c or 0))
        if u:
            day_cost[str(d)] = day_cost.get(str(d), 0.0) + u
    spark = _sparkline([day_cost[dk] for dk in day_keys])
    peak = max(day_cost.values()) if day_cost else 0.0
    svc_chips = " ".join(
        f"<span class=pill>{_esc(s)} <b class=mono>${v:,.2f}</b></span>"
        for s, v in sorted(by_service.items(), key=lambda kv: -kv[1])) or "<span class=sub>—</span>"

    conn_tr = "".join(
        f"<tr><td class=mono>{_esc(cid)}</td><td class=mono style='text-align:right'>{int(n):,}</td>"
        f"<td class=mono style='text-align:right'>{int(cu or 0):,}</td></tr>" for cid, n, cu in conn_rows)
    conn_table = ("<table class=t><tr><th>커넥터</th><th>호출</th><th>내부 코스트 유닛</th></tr>" + conn_tr + "</table>"
                  ) if conn_tr else "<div class=empty>게이트웨이 사용 기록이 없어요.</div>"

    sweep_tr = "".join(
        f"<tr><td class=mono>{_esc(p)}</td><td class=mono style='text-align:right'>{int(sc or 0):,}</td></tr>"
        for p, sc in sweep_rows)
    sweep_table = ("<table class=t><tr><th>상류 제공자</th><th>호출</th></tr>" + sweep_tr + "</table>"
                   ) if sweep_tr else ("<div class=empty>백그라운드 스윕 계측이 꺼져 있어요 — datasets에 "
                                       "<code>PROVIDER_USAGE_TELEMETRY=true</code>로 켜면 여기 쌓여요.</div>")

    fixed = fixed_costs()
    fixed_total = sum(f["usd"] for f in fixed)
    fixed_tr = "".join(
        f"<tr><td>{_esc(f['name'])}</td><td class=mono style='text-align:right'>${f['usd']:,.2f}/월</td>"
        f"<td class=sub>{_esc(f['note'])}</td></tr>" for f in fixed)
    fixed_table = ("<table class=t><tr><th>구독</th><th>월 요금</th><th>메모</th></tr>" + fixed_tr + "</table>"
                   ) if fixed_tr else "<div class=empty>고정 구독이 등록되지 않았어요 — .env FIXED_COSTS_JSON에 선언하면 여기 합산돼요.</div>"

    def _rate_row(r: dict) -> str:
        # request-priced (Vertex Ranking, Document AI): no per-token cols — show the per-call rate so
        # a $0 in the usage table reads as "priced differently," not "free."
        if r.get("per_call") is not None:
            pc = float(r["per_call"])
            return (f"<tr><td class=mono>*{_esc(r.get('match'))}*</td>"
                    "<td class=mono style='text-align:right'>—</td>"
                    "<td class=mono style='text-align:right'>—</td>"
                    "<td class=mono style='text-align:right'>—</td>"
                    f"<td class=sub>콜당 ${pc:,.4f} (≈${pc * 1000:,.2f}/1k)</td></tr>")
        cin = r.get("cached_in")
        note = ">200k 프리미엄 요율 있음" if r.get("premium_over_200k") else ""
        return (f"<tr><td class=mono>*{_esc(r.get('match'))}*</td>"
                f"<td class=mono style='text-align:right'>${float(r.get('in', 0)):,.2f}</td>"
                f"<td class=mono style='text-align:right'>${float(r.get('out', 0)):,.2f}</td>"
                f"<td class=mono style='text-align:right'>{'—' if cin is None else f'${float(cin):,.3f}'}</td>"
                f"<td class=sub>{note}</td></tr>")
    rules_tr = "".join(_rate_row(r) for r in reg.get("rules", []))
    unknown_note = (f"<div class=sub>⚠ 요율 미설정 모델: {', '.join(sorted(_esc(u) for u in unknown_models))} — "
                    "PRICING_JSON에 규칙을 추가하세요 (달러 표시는 절대 지어내지 않아요).</div>") if unknown_models else ""

    sel = " · ".join(
        (f"<b>{d}일</b>" if d == days else f"<a href='/costs?days={d}'>{d}일</a>") for d in _COST_RANGES)
    untracked = " · ".join(_esc(p) for p in _UNTRACKED_PROVIDERS)

    body = (
        (f"<div class=flash>{_esc(err)} — control-plane 재빌드 후 llm_usage 테이블이 생겨요.</div>" if err else "")
        # stale-rate banner — the rate basis is always visible so an out-of-date table is never silent.
        + f"<div class=warn>요율 기준일 <b>{_esc(reg.get('as_of'))}</b>"
        + (f" · 출처 {_esc(reg.get('source_url'))}" if reg.get('source_url') else "")
        + " — 오래되면 <code>.env PRICING_JSON</code>으로 갱신하세요.</div>"
        + f"<p class=hint>기간: {sel} · LLM·임베딩·리랭커·문서AI를 요율표로 달러화(캐시 할인·콜당 과금 반영, "
          "임베딩은 ~추정). 무료·스윕 API는 호출량만 집계돼요. 30초마다 자동 새로고침.</p>"
        + f"<h2>합계 (최근 {days}일)</h2><div class=grid>"
        + f"<div class=card><h3>LLM·임베딩·문서AI 비용</h3><div style='font-size:26px' class=mono>${total_usd:,.2f}</div>"
          f"<div class=sub>서비스별: {svc_chips}</div>{unknown_note}</div>"
        + f"<div class=card><h3>고정 구독</h3><div style='font-size:26px' class=mono>${fixed_total:,.2f}/월</div>"
          f"<div class=sub>.env FIXED_COSTS_JSON 선언분</div></div>"
        + f"<div class=card><h3>추정 총액</h3><div style='font-size:26px' class=mono>${total_usd + fixed_total:,.2f}</div>"
          f"<div class=sub>토큰 {days}일 합산 + 월 구독 (개략)</div></div>"
        + f"<div class=card><h3>미추적(무료·스윕)</h3><div class=sub>달러 원가 미집계 — 호출량만: {untracked}</div></div>"
        + "</div>"
        + f"<h2>일별 LLM 비용 추이 ({days}일)</h2>"
          f"<div class=card>{spark}<div class=sub>최대 일 ${peak:,.4f} · 결정적 데이터, 전망 아님</div></div>"
        + "<h2>LLM · 임베딩 · 리랭커 · 문서AI 사용 (모델 × 용도)</h2>" + llm_table
        + "<h2>게이트웨이 데이터 호출</h2>"
          "<div class=sub>커넥터별 호출량 — 상류 데이터 API는 대부분 무료 티어라 달러 비용은 0, "
          "코스트 유닛은 내부 상대 가중치예요.</div>" + conn_table
        + "<h2>백그라운드 스윕 상류 호출</h2>"
          "<div class=sub>게이트웨이를 우회하는 워커 스윕·폴백의 제공자별 호출량 (COST-3, opt-in). "
          "대부분 무료 API — 호출량 가시성용.</div>" + sweep_table
        + "<h2>고정 구독</h2>" + fixed_table
        + "<h2>요율표 (per 1M tokens · USD)</h2>"
          "<table class=t><tr><th>모델 매칭</th><th>입력</th><th>출력</th><th>캐시 입력</th><th>비고</th></tr>"
          + rules_tr + "</table>"
        + _per_project_costs_section(cost_usd, days)
    )
    return HTMLResponse(page("/costs", "Costs", body, refresh=True))


def _per_project_costs_section(cost_usd, days: int = 30) -> str:
    """METER-2: 유저(프로젝트)별 원가 롤업 — LLM $(캐시 할인) + 커넥터 호출/코스트유닛. 플랜 가격·캡을
    실측으로 조정하는 근거. project_id NULL(피드·인제스트 등 공용 작업)은 '공용/백그라운드'로 접는다."""
    try:
        eng = ENGINES.get("controlplane")
        if eng is None:
            return ""
        from datetime import datetime as _dt, timedelta as _td
        since = _dt.utcnow() - _td(days=days)
        with eng.connect() as conn:  # type: ignore[union-attr]
            rows = conn.execute(sa_text(
                "SELECT lu.project_id, t.name tenant, lu.model, "
                "SUM(lu.input_tokens) i, SUM(lu.output_tokens) o, SUM(lu.cached_input_tokens) ci, SUM(lu.calls) c "
                "FROM llm_usage lu "
                "LEFT JOIN projects p ON p.id = lu.project_id "
                "LEFT JOIN tenants t ON t.id = p.tenant_id "
                "WHERE lu.ts >= :since GROUP BY lu.project_id, t.name, lu.model"
            ), {"since": since}).all()
            conn_rows = conn.execute(sa_text(
                "SELECT ue.project_id, t.name tenant, COUNT(*) n, SUM(ue.cost_units) cu "
                "FROM usage_events ue "
                "LEFT JOIN projects p ON p.id = ue.project_id "
                "LEFT JOIN tenants t ON t.id = p.tenant_id "
                "WHERE ue.ts >= :since GROUP BY ue.project_id, t.name"
            ), {"since": since}).all()
    except Exception:  # noqa: BLE001 — 컬럼 미생성(구버전 DB) 등: 섹션만 생략
        return ""

    def _key(pid, tenant):
        return tenant or ("공용/백그라운드" if pid is None else pid)

    per_user: dict[str, dict] = {}

    def _agg(key: str) -> dict:
        return per_user.setdefault(key, {"usd": 0.0, "calls": 0, "conn_calls": 0, "units": 0, "unknown": False})

    for pid, tenant, model, i, o, ci, c in rows:
        agg = _agg(_key(pid, tenant))
        usd = cost_usd(model, int(i or 0), int(o or 0), cached_input_tokens=int(ci or 0), calls=int(c or 0))
        if usd is None:
            agg["unknown"] = True
        else:
            agg["usd"] += usd
        agg["calls"] += int(c or 0)
    for pid, tenant, n, cu in conn_rows:
        agg = _agg(_key(pid, tenant))
        agg["conn_calls"] += int(n or 0)
        agg["units"] += int(cu or 0)

    if not per_user:
        return (f"<h2>유저별 원가 ({days}일)</h2><div class=empty>귀속 기록이 아직 없어요 — "
                "METER-1/3 배포 후 첫 채팅부터 쌓여요.</div>")
    tr = "".join(
        f"<tr><td class=mono>{_esc(k)}</td>"
        f"<td class=mono style='text-align:right'>${v['usd']:,.4f}{'+?' if v['unknown'] else ''}</td>"
        f"<td class=mono style='text-align:right'>{v['calls']:,}</td>"
        f"<td class=mono style='text-align:right'>{v['conn_calls']:,}</td>"
        f"<td class=mono style='text-align:right'>{v['units']:,}</td></tr>"
        for k, v in sorted(per_user.items(), key=lambda kv: -kv[1]["usd"]))
    return (f"<h2>유저별 원가 ({days}일)</h2>"
            "<div class=sub>METER-1/2/3 — 플랜 가격·캡 조정의 실측 근거. LLM $는 캐시 할인 반영, "
            "'+?'=요율 미설정 모델 포함. 커넥터 코스트 유닛은 내부 상대 가중치.</div>"
            "<table class=t><tr><th>유저(테넌트)</th><th>LLM 비용(USD)</th><th>LLM 호출</th>"
            "<th>커넥터 호출</th><th>코스트 유닛</th></tr>" + tr + "</table>")


# --- Billing (BILL-5) ------------------------------------------------------
@app.get("/billing", response_class=HTMLResponse)
async def billing_view(request: Request, msg: str = ""):
    """BILL-5: 결제 운영 — 구독·인보이스·크레딧 원장·웹훅을 studio DB에서 읽고, 재시도/환불/
    플랜 오버라이드는 studio의 admin 엔드포인트(X-Admin-Token)로 실행한다 (apply_plan 단일 경유)."""
    eng = ENGINES.get("studio")
    if eng is None:
        return HTMLResponse(page("/billing", "Billing", "<div class=warn>studio DB not mounted.</div>"))

    subs: list = []
    invoices: list = []
    err = ""
    try:
        with eng.connect() as conn:  # type: ignore[union-attr]
            subs = conn.execute(sa_text(
                "SELECT id, user_email, plan, status, current_period_end, cancel_at_period_end "
                "FROM subscriptions ORDER BY created_at DESC LIMIT 50")).all()
            invoices = conn.execute(sa_text(
                "SELECT id, user_email, total, status, attempts, period_start, paid_at "
                "FROM invoices ORDER BY created_at DESC LIMIT 50")).all()
    except Exception as exc:  # noqa: BLE001 — 첫 부팅: 테이블 미생성
        err = f"{type(exc).__name__}: {exc}"

    sub_tr = "".join(
        f"<tr><td class=mono>{_esc(sid)}</td><td class=mono>{_esc(em)}</td><td>{_esc(pl)}</td>"
        f"<td>{_esc(st)}</td><td class=mono>{_esc(str(pe)[:10])}</td>"
        f"<td>{'예약됨' if cape else '-'}</td></tr>"
        for sid, em, pl, st, pe, cape in subs)
    inv_tr = "".join(
        f"<tr><td class=mono>{_esc(iid)}</td><td class=mono>{_esc(em)}</td>"
        f"<td class=mono style='text-align:right'>₩{int(tot):,}</td><td>{_esc(st)}</td>"
        f"<td class=mono>{int(att)}</td><td class=mono>{_esc(str(ps)[:10])}</td>"
        f"<td>"
        + (f"<form method=post action=/ops/billing/retry style='display:inline'>"
           f"<input type=hidden name=invoice_id value='{_esc(iid)}'><button>재시도</button></form> "
           if st == "failed" else "")
        + (f"<form method=post action=/ops/billing/refund style='display:inline' "
           f"onsubmit=\"return confirm('환불할까요? 킥백도 회수돼요.')\">"
           f"<input type=hidden name=invoice_id value='{_esc(iid)}'><button>환불</button></form>"
           if st == "paid" else "")
        + "</td></tr>"
        for iid, em, tot, st, att, ps, _paid in invoices)

    body = (
        (f"<div class=flash>{_esc(msg)}</div>" if msg else "")
        + (f"<div class=warn>{_esc(err)} — studio 재빌드 후 결제 테이블이 생겨요.</div>" if err else "")
        + "<p class=hint>구독·인보이스·크레딧 — 액션(재시도/환불/플랜)은 studio admin API를 경유해요 "
          "(플랜 전환은 항상 apply_plan 단일 경로).</p>"
        + "<h2>플랜 오버라이드</h2>"
          "<form method=post action=/ops/billing/plan class=row>"
          "<input name=email placeholder='user@example.com' required> "
          "<select name=plan><option>free</option><option>pro</option></select> "
          "<button>적용</button></form>"
        + "<h2>구독 (최근 50)</h2>"
          "<table class=t><tr><th>id</th><th>유저</th><th>플랜</th><th>상태</th><th>기간 종료</th><th>해지</th></tr>"
        + sub_tr + "</table>"
        + "<h2>인보이스 (최근 50)</h2>"
          "<table class=t><tr><th>id</th><th>유저</th><th>청구액</th><th>상태</th><th>시도</th><th>기간</th><th>액션</th></tr>"
        + inv_tr + "</table>"
        + "<h2>크레딧 원장 (최근 50)</h2>" + _simple_table("studio", "credit_ledger",
            ["user_email", "amount_krw", "kind", "related_user", "created_at"], limit=50)
        + "<h2>웹훅 이벤트 (최근 20)</h2>" + _simple_table("studio", "webhook_events",
            ["event_id", "provider", "processed_at"], limit=20)
    )
    return HTMLResponse(page("/billing", "Billing", body))


async def _studio_admin_post(path: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{settings.studio_url}{path}",
                             headers={"X-Admin-Token": settings.admin_token}, timeout=30)
        return r.status_code == 200, ("" if r.status_code == 200 else f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


@app.post("/ops/billing/retry")
async def ops_billing_retry(request: Request):
    form = await request.form()
    ok, why = await _studio_admin_post(f"/admin/billing/invoices/{form.get('invoice_id')}/retry")
    msg = "재시도 완료" if ok else f"재시도 실패 ({why})"
    return RedirectResponse(f"/billing?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/ops/billing/refund")
async def ops_billing_refund(request: Request):
    form = await request.form()
    ok, why = await _studio_admin_post(f"/admin/billing/invoices/{form.get('invoice_id')}/refund")
    msg = "환불 완료 (킥백 회수 포함)" if ok else f"환불 실패 ({why})"
    return RedirectResponse(f"/billing?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/ops/billing/plan")
async def ops_billing_plan(request: Request):
    form = await request.form()
    email, plan = str(form.get("email") or ""), str(form.get("plan") or "free")
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{settings.studio_url}/admin/users/{email}/plan",
                             headers={"X-Admin-Token": settings.admin_token},
                             json={"plan": plan}, timeout=60)
        msg = f"{email} → {plan} 적용" if r.status_code == 200 else f"플랜 적용 실패 (HTTP {r.status_code})"
    except Exception as exc:  # noqa: BLE001
        msg = f"플랜 적용 실패: {type(exc).__name__}"
    return RedirectResponse(f"/billing?msg={msg.replace(' ', '+')}", status_code=303)


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
                   f"<button class=p>지금 수집(델타) ▶</button></form></td></tr>")
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
        details = ing.get("error_details") or []
        ing_html = (
            "<h2>파이프라인 실행 (IngestionJob)</h2>"
            f"<div class=tablewrap><table><tbody>"
            f"<tr><td class=muted>상태</td><td>{badge(_esc(ing_status), JOB_STATUS_CLASS.get(ing_status, ''))} "
            f"· {_esc(ing.get('done'))}/{_esc(ing.get('total'))} 처리 · {_esc(ing.get('rows'))} chunks</td></tr>"
            f"<tr><td class=muted>시작</td><td class=muted>{_esc((ing.get('started_at') or '')[:19])} "
            f"→ {_esc((ing.get('ended_at') or '—')[:19])}</td></tr>"
            f"</tbody></table></div>"
            + _error_detail_html(ing, details, err))
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


# OPS-1: an IngestionJob's `kind` maps to a runnable pipeline id for the retry-failed action.
# For 재무 backfill the recorded kind is "backfill" but the pipeline registry id is "financials".
_RETRY_PIPELINE = {"backfill": "financials"}


def _error_detail_html(ing: dict, details: list, err: str | None) -> str:
    """OPS-1: render grouped per-cause failures (원인 → 건수 → 종목 목록) + a '실패 종목만 재시도'
    button that re-enqueues just those tickers. Falls back to the legacy single-string logbox for old
    jobs that predate error_details."""
    if not details:
        if err:
            cls = "err" if ing.get("status") == "error" else ""
            return f"<div class='logbox {cls}'>{_esc(err)}</div>"
        return "<p class=muted>기록된 오류 메모가 없습니다.</p>"

    rows = ""
    all_failed: list[str] = []
    for g in details:
        tickers = g.get("tickers") or []
        all_failed += tickers
        chips = ", ".join(_esc(t) for t in tickers)
        rows += (f"<tr><td class=err>{_esc(g.get('error'))}</td>"
                 f"<td class=mono>{_esc(g.get('count'))}</td>"
                 f"<td><details><summary class=muted>종목 {_esc(len(tickers))}</summary>"
                 f"<div class=mono style='white-space:normal'>{chips}</div></details></td></tr>")
    table = ("<div class=tablewrap><table><thead><tr><th>원인</th><th>건수</th><th>실패 종목</th></tr></thead>"
             f"<tbody>{rows}</tbody></table></div>")

    retry = ""
    pid = _RETRY_PIPELINE.get(ing.get("kind"), ing.get("kind"))
    mkt = ing.get("market") or ""
    if all_failed and pid and mkt:
        # de-dup while preserving order, cap so the form/query never blows up
        seen: dict[str, None] = {}
        for t in all_failed:
            seen.setdefault(t, None)
        tick_val = " ".join(list(seen)[:500])
        retry = (f"<form class=ops method=post action='/ops/pipelines/run' style='margin-top:10px'>"
                 f"<input type=hidden name=pipelines value='{_esc(pid)}'>"
                 f"<input type=hidden name=market value='{_esc(mkt)}'>"
                 f"<input type=hidden name=tickers value='{_esc(tick_val)}'>"
                 f"<button class=p>실패 종목만 재시도 ▶ ({len(seen)})</button></form>")
    return table + retry


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
                           tickers: str = Form(""), pipelines: list[str] = Form(default=[]),
                           mode: str = Form("delta")):
    """PH-PIPE unified backfill: run the selected pipelines over a preset (or custom tickers).
    `mode`: delta(기본 — 새로 나온 것만) | full(전체 재수집); 그대로 datasets에 전달해요."""
    mode = mode if mode in ("delta", "full") else "delta"
    tick = [t.strip() for t in tickers.replace(",", " ").split() if t.strip()]
    if tick:
        payload, label = {"market": market, "tickers": tick, "pipelines": pipelines}, f"{market}:{len(tick)}t"
    else:
        payload, label = {"preset": preset, "pipelines": pipelines}, preset
    payload["mode"] = mode
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/pipelines/run", json=payload, timeout=20)
        ok = r.status_code == 200 and r.json().get("started")
    pl = "+".join(pipelines) if pipelines else "default"
    return RedirectResponse(f"/pipelines?msg=수집+{'시작' if ok else '실패'}+{label}+[{pl}]+·+{mode}", status_code=303)


@app.post("/ops/news")
async def ops_news(request: Request, market: str = Form("US"), tickers: str = Form("")):
    tick = [t.strip() for t in tickers.replace(",", " ").split() if t.strip()] or None
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{settings.datasets_url}/admin/news/ingest", json={"market": market, "tickers": tick}, timeout=20)
        ok = r.status_code == 200
    label = f"{market}+{'+'.join(tick) if tick else 'market'}"
    return RedirectResponse(f"/pipelines?msg=news+ingest+{'started' if ok else 'failed'}+{label}", status_code=303)


@app.post("/ops/askfeed/refresh")
async def ops_askfeed_refresh(request: Request):
    """Macro Trends 수동 갱신 — studio-api의 refresh_once를 즉시 1회 실행 (서명 동일 시 LLM 스킵)."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{settings.studio_url}/ask-feed/refresh",
                             headers={"X-Service-Token": settings.service_token}, timeout=90)
            j = r.json() if r.status_code == 200 else {}
        if r.status_code != 200:
            msg = f"Macro Trends 갱신 실패 (HTTP {r.status_code})"
        else:
            msg = (f"Macro Trends {'갱신됨' if j.get('refreshed') else '변화 없음'} · "
                   f"카드 {j.get('cards', '?')}개")
    except Exception as exc:  # noqa: BLE001 — studio 미기동 등
        msg = f"Macro Trends 갱신 실패: {type(exc).__name__}"
    return RedirectResponse(f"/pipelines?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/ops/askfeed/refresh-sections")
async def ops_askfeed_refresh_sections(request: Request):
    """홈 마키 섹션(어닝·거장·히스토리) 수동 갱신 — studio-api의 refresh_sections_once를 즉시 1회
    실행(각 스코프 서명 동일 시 LLM 스킵)."""
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{settings.studio_url}/ask-feed/refresh-sections",
                             headers={"X-Service-Token": settings.service_token}, timeout=200)
            j = r.json() if r.status_code == 200 else {}
        if r.status_code != 200:
            msg = f"섹션 갱신 실패 (HTTP {r.status_code})"
        else:
            by = j.get("cards_by_scope") or {}
            summary = " · ".join(f"{k} {v}장" for k, v in by.items()) or "카드 없음"
            msg = f"섹션 갱신 {j.get('refreshed', '?')}/{j.get('scopes', '?')} · {summary}"
    except Exception as exc:  # noqa: BLE001 — studio 미기동 등
        msg = f"섹션 갱신 실패: {type(exc).__name__}"
    return RedirectResponse(f"/pipelines?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/ops/askfeed/refresh-all")
async def ops_askfeed_refresh_all(request: Request):
    """홈 트렌드 피드 전부 갱신 — studio /ask-feed/refresh(뉴스) → /ask-feed/refresh-sections(섹션)를
    차례로 호출하고 각 결과를 한 줄 메시지로 합쳐 /pipelines로 리다이렉트한다."""
    parts: list[str] = []
    try:
        async with httpx.AsyncClient() as c:
            r1 = await c.post(f"{settings.studio_url}/ask-feed/refresh",
                              headers={"X-Service-Token": settings.service_token}, timeout=90)
            j1 = r1.json() if r1.status_code == 200 else {}
            if r1.status_code != 200:
                parts.append(f"뉴스 실패(HTTP {r1.status_code})")
            else:
                parts.append(f"뉴스 {'갱신됨' if j1.get('refreshed') else '변화 없음'}·카드 {j1.get('cards', '?')}개")
            r2 = await c.post(f"{settings.studio_url}/ask-feed/refresh-sections",
                              headers={"X-Service-Token": settings.service_token}, timeout=200)
            j2 = r2.json() if r2.status_code == 200 else {}
            if r2.status_code != 200:
                parts.append(f"섹션 실패(HTTP {r2.status_code})")
            else:
                by = j2.get("cards_by_scope") or {}
                summary = " ".join(f"{k} {v}장" for k, v in by.items()) or "카드 없음"
                parts.append(f"섹션 {j2.get('refreshed', '?')}/{j2.get('scopes', '?')}·{summary}")
    except Exception as exc:  # noqa: BLE001 — studio 미기동 등
        parts.append(f"전부 갱신 실패: {type(exc).__name__}")
    msg = "전부 갱신 · " + " · ".join(parts)
    return RedirectResponse(f"/pipelines?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/ops/logos/upload")
async def ops_logo_upload(request: Request, market: str = Form("US"),
                          ticker: str = Form(""), logo: UploadFile = File(...)):
    """Manual company-logo upload — fills any ticker the hybrid resolver missed (esp. KR). Forwards
    the image to datasets /logos (base64 JSON) where it's cached and served like an auto-resolved one."""
    import base64 as _b64
    ticker = (ticker or "").strip()
    if not ticker:
        return RedirectResponse("/pipelines?msg=티커를+입력하세요", status_code=303)
    try:
        raw = await logo.read()
        if not raw:
            return RedirectResponse("/pipelines?msg=이미지+파일이+비어있어요", status_code=303)
        data_url = "data:image/png;base64," + _b64.b64encode(raw).decode()
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{settings.datasets_url}/logos",
                             json={"market": market, "ticker": ticker, "data_url": data_url}, timeout=30)
        if r.status_code == 200:
            msg = f"로고 업로드 완료 · {market} {ticker} ({r.json().get('bytes', '?')}B)"
        else:
            detail = (r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else "") or ""
            msg = f"로고 업로드 실패 (HTTP {r.status_code}) {detail}"
    except Exception as exc:  # noqa: BLE001
        msg = f"로고 업로드 실패: {type(exc).__name__}"
    return RedirectResponse(f"/pipelines?msg={msg.replace(' ', '+')}", status_code=303)


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
