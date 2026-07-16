"""Admin console presentation: the shared chrome (sidebar + topbar), the design
system, and small HTML helpers. Route logic in main.py builds page bodies and wraps
them with ``page(active, title, body)``.

Bodies are composed in Python (escaped via ``_esc``/``_cell``) — there is no untrusted
templating, so the only loaded files are the static ``style`` (literal CSS braces) and
the standalone ``login`` page.
"""

from __future__ import annotations

import html
from pathlib import Path

_TPL_DIR = Path(__file__).parent / "templates"


def _load(name: str) -> str:
    return (_TPL_DIR / f"{name}.html").read_text(encoding="utf-8")


STYLE = _load("style")        # <style>…</style>
_LOGIN = _load("login")       # standalone login page (its own minimal style)

# left-nav information architecture: 5 sections with depth (was 12 flat tabs). Each route handler
# still calls page('/its-route', ...); page() derives the active child AND its parent section from
# that one string, so sub-routes (/runs/{id}, /queue/job/{id}, /db/{table}) light the right section.
NAV_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("", [("/", "Overview", "▦")]),
    ("OPERATIONS", [("/pipelines", "Pipelines", "⏣"), ("/runs", "Runs", "🗂"), ("/queue", "Queue", "⚙")]),
    ("DATA", [("/catalog", "Catalog", "◈"), ("/data", "Store", "▤"),
              ("/upstream", "Upstream", "📡"), ("/db", "DB browser", "🗄")]),
    ("MONEY", [("/costs", "Costs", "💸"), ("/billing", "Billing", "💳")]),
    ("ACCOUNTS", [("/users", "Users", "⚇"), ("/shares", "Shares", "🔗")]),
]


def _nav_active(href: str, active: str) -> bool:
    """True if `active` (the current page's route) is this nav item or one of its sub-routes."""
    if href == "/":
        return active == "/"
    return active == href or active.startswith(href + "/")


def _esc(v) -> str:
    return html.escape(str(v))


def _cell(v, limit: int = 0) -> str:
    if v is None:
        return "<span class=faint>NULL</span>"
    s = str(v)
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    return _esc(s)


def badge(text: str, kind: str = "") -> str:
    return f"<span class='badge {kind}'>{_esc(text)}</span>"


def sdot(kind: str = "") -> str:
    return f"<span class='sdot {kind}'></span>"


def tile(k, v, ic, href, small=False) -> str:
    """A clickable at-a-glance metric tile (icon · label · value), linking to its section."""
    inner = f"<div class='k'>{ic} {_esc(k)}</div><div class='v {'sm' if small else ''}'>{_esc(v)}</div>"
    return f"<a class=tile href='{href}' style='display:block'>{inner}</a>"


# Status → CSS-class / Korean-label maps, centralized so every view colours a status the same way.
JOB_STATUS_CLASS = {"success": "ok", "error": "err", "running": "run"}      # IngestionJob status
QUEUE_STATUS_CLASS = {"todo": "warn", "doing": "run", "succeeded": "ok", "failed": "err",
                      "cancelled": "", "aborting": "warn", "aborted": ""}   # Procrastinate job status
QUEUE_STATUS_LABEL = {"todo": "대기", "doing": "실행중", "succeeded": "완료", "failed": "실패",
                      "cancelled": "취소됨", "aborting": "중단중", "aborted": "중단됨"}
UPSTREAM_DOT = {"ok": "ok", "degraded": "warn", "key-missing": "warn", "down": "err"}
UPSTREAM_LABEL = {"ok": "정상", "degraded": "불안정", "key-missing": "키 없음", "down": "다운"}


def progress(done, total, kind: str = "") -> str:
    try:
        pct = max(0, min(100, round((done or 0) / total * 100))) if total else 0
    except (TypeError, ZeroDivisionError):
        pct = 0
    return f"<div class='prog {kind}'><i style='width:{pct}%'></i></div>"


def login_page(err: str = "") -> str:
    return _LOGIN.replace("{err}", err)


def page(active: str, title: str, body: str, refresh: bool = False) -> str:
    """Wrap a page body in the console chrome (sidebar nav + topbar)."""
    nav_parts: list[str] = []
    for sec_label, items in NAV_GROUPS:
        if sec_label:
            nav_parts.append(f"<div class=navsec>{_esc(sec_label)}</div>")
        for href, label, ic in items:
            on = " on" if _nav_active(href, active) else ""
            nav_parts.append(
                f"<a class='nav{on}' href='{href}'><span class=i>{ic}</span>{_esc(label)}</a>")
    nav = "".join(nav_parts)
    # Refresh control (replaces the old forced <meta refresh>): a manual ↻ button + an auto
    # interval the operator chooses (Off/5s/10s/1m), persisted in the ?auto= query param so it
    # survives reloads. `refresh` (a page hint that work is live) only sets the DEFAULT interval
    # the first time, when the URL has no explicit choice yet.
    ctl = (
        "<div class=refreshctl>"
        "<button type=button class=rbtn id=rnow title='새로고침'>↻</button>"
        "<select class=rsel id=rauto title='자동 새로고침'>"
        "<option value=0>자동 끔</option><option value=5>5초</option>"
        "<option value=10>10초</option><option value=60>1분</option>"
        "</select></div>"
    )
    default_auto = "10" if refresh else "0"   # live work on the page → default 10s until the operator picks
    script = (
        "<script>(function(){"
        "var u=new URL(location.href);"
        "var a=parseInt(u.searchParams.get('auto')||'" + default_auto + "',10);"
        "var s=document.getElementById('rauto');"
        "if(s){s.value=String([0,5,10,60].indexOf(a)>=0?a:0);"
        "s.onchange=function(){u.searchParams.set('auto',s.value);location.replace(u.toString());};}"
        "var b=document.getElementById('rnow');if(b){b.onclick=function(){location.reload();};}"
        "if(a>0){window.__rt=setTimeout(function(){location.reload();},a*1000);}"
        "})();</script>"
    )
    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>VG Admin · {_esc(title)}</title>{STYLE}</head><body>"
        "<div class=app>"
        "<aside class=side>"
        "<div class=brand><span class=dot></span>VALUE·GRAPH</div>"
        f"{nav}"
        "<div class=sp></div>"
        "<div class=foot>admin · ops console<br>out-of-band · not in request path</div>"
        "</aside>"
        "<main class=content>"
        f"<header class=bar><h1>{_esc(title)}</h1><div class=barright>{ctl}<a href=/logout>logout ↩</a></div></header>"
        f"<div class=page>{body}</div>"
        "</main></div>"
        f"{script}</body></html>"
    )
