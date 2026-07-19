"""Admin DB browser: styled CRUD over every reflected service table (RF-14, split from main.py).

The ``/db/*`` routes — list tables, browse rows, view / edit / create / delete — as their own
APIRouter over the reflected-DB state (``state.py``). No sqladmin: our own panel-themed CRUD.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Table, and_, text

from adminpanel.state import DB_STATUS, ENGINES, TABLES, _table_counts
from adminpanel.views import _cell, _esc, page

router = APIRouter()


# --- DB browser (styled CRUD; no sqladmin) --------------------------------
_PAGE = 50

# Curated domain grouping for the /db index: DB key → ordered [(group title, [name substrings])].
# Instead of one flat alphabetical chip list per DB, tables are bucketed by what they're FOR, so an
# operator finds "the feed cache" or "billing tables" at a glance. A table lands in the FIRST group
# whose substrings match; anything unmatched falls into "기타". Prod schemas drift, so this is a
# best-effort map — new/unknown tables still show up (under 기타), never hidden.
_DOMAIN_GROUPS: dict[str, list[tuple[str, list[str]]]] = {
    "studio": [
        ("피드·캐시", ["ask_feed", "card_tap", "desk_feed", "feed_cache"]),
        ("대화", ["conversation", "message", "run", "agent"]),
        ("관심종목·분석", ["watchlist", "pinned", "standing", "brief", "board"]),
        ("결제·플랜", ["subscription", "invoice", "billing", "credit", "plan",
                    "referral", "turn_usage", "upstream_usage"]),
        ("유저·인증", ["user", "otp", "guest"]),
        ("공유", ["share"]),
    ],
    "controlplane": [
        ("테넌시", ["tenant", "project"]),
        ("키·활성화", ["api_key", "key", "activation", "entitlement"]),
        ("계측·비용", ["usage", "meter", "quota", "provider", "cost"]),
    ],
    "datasets": [
        ("재무·공시", ["financial", "fact", "filing", "disclosure", "fundamental", "xbrl"]),
        ("시세·가격", ["price", "bar", "ohlcv", "corporate_action", "quote"]),
        ("뉴스·문서·로고", ["news", "doc", "evidence", "logo", "article"]),
        ("큐·잡·이력", ["job", "queue", "activity", "sweep", "run"]),
    ],
}


def _group_tables(key: str, tables: list[str]) -> list[tuple[str, list[str]]]:
    """Bucket a DB's tables into its curated domain groups (order preserved); every table lands in
    the FIRST matching group, and anything unmatched goes to '기타'. DBs with no curated map render
    as a single sorted group. Empty groups are dropped so headings never dangle."""
    groups = _DOMAIN_GROUPS.get(key)
    if not groups:
        return [("전체", sorted(tables))] if tables else []
    remaining = set(tables)
    out: list[tuple[str, list[str]]] = []
    for title, subs in groups:
        matched = sorted(t for t in remaining if any(s in t for s in subs))
        if matched:
            out.append((title, matched))
            remaining -= set(matched)
    if remaining:
        out.append(("기타", sorted(remaining)))
    return out


@router.get("/db", response_class=HTMLResponse)
async def db_index(request: Request):
    cards = ""
    for key, info in DB_STATUS.items():
        if info["error"]:
            cards += (f"<div class=card><h3>{_esc(info['title'])} <span class=muted>{key}</span></h3>"
                      f"<div class=err>unavailable: {_esc(info['error'])}</div></div>")
            continue
        counts = _table_counts(key)
        groups = _group_tables(key, list(info["meta"]))
        if groups:
            inner = ""
            for title, tbls in groups:
                chips = "".join(
                    f"<span class=pill><a href='/db/{key}/{t}'>{_esc(t)}</a>"
                    f"<span class=cnt>{_esc(counts.get(t, '?'))}</span></span>"
                    for t in tbls)
                inner += (f"<div class=dbgroup style='margin-top:9px'>"
                          f"<div class=sub style='margin:0 0 4px;font-weight:700'>{_esc(title)}</div>{chips}</div>")
        else:
            inner = "<span class=muted>(no tables)</span>"
        cards += f"<div class=card><h3>{_esc(info['title'])} <span class=muted>{key}</span></h3>{inner}</div>"
    body = ("<p class=hint>Every reflected service table — view, edit, create, delete in the panel theme. "
            "도메인별로 묶어서 보여줘요.</p>"
            "<div class=grid>" + cards + "</div>")
    return HTMLResponse(page("/db", "DB browser", body))


def _check(key: str, table: str):
    info = DB_STATUS.get(key)
    meta = (info or {}).get("meta", {})
    if not info or table not in meta:
        return None, None
    return info, meta[table]


def _crumb(key, table, extra="") -> str:
    info = DB_STATUS.get(key, {})
    return (f"<div class=crumb><a href=/db>DB browser</a> / {_esc(info.get('title', key))} "
            f"<span class=muted>({_esc(key)})</span> / <a href='/db/{key}/{table}'>{_esc(table)}</a>{extra}</div>")


@router.get("/db/{key}/{table}", response_class=HTMLResponse)
async def browse_table(request: Request, key: str, table: str, page_: int = 0):
    info, m = _check(key, table)
    if not info:
        return HTMLResponse(page("/db", "not found", "<div class=warn>unknown table</div>"), status_code=404)
    cols, pk = m["columns"], m["pk"]
    engine = ENGINES[key]
    order = f'"{pk[0]}"' if pk else f'"{cols[0]}"'
    p = max(page_, 0)
    offset = p * _PAGE
    with engine.connect() as conn:
        total = conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar() or 0
        rows = conn.execute(text(f'SELECT * FROM "{table}" ORDER BY {order} LIMIT :l OFFSET :o'),
                            {"l": _PAGE, "o": offset}).fetchall()
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    trs = ""
    for i, row in enumerate(rows):
        cells = "".join(f"<td class=wrap>{_cell(v, 80)}</td>" for v in row)
        trs += f"<tr class=rowlink onclick=\"location='/db/{key}/{table}/row/{offset + i}'\">{cells}</tr>"
    if not rows:
        trs = f"<tr><td colspan={len(cols)} class=muted>no rows</td></tr>"

    last = max((total - 1) // _PAGE, 0)
    nav = ""
    if p > 0:
        nav += f"<a class=pg href='/db/{key}/{table}?page_={p - 1}'>← prev</a>"
    nav += f"<span class=muted>rows {offset + 1 if total else 0}–{min(offset + _PAGE, total)} of {total}</span>"
    if p < last:
        nav += f"<a class=pg href='/db/{key}/{table}?page_={p + 1}'>next →</a>"
    new_btn = (f"<a class='btn p' href='/db/{key}/{table}/new'>+ new row</a>" if pk
               else "<span class=muted>read-only (no PK)</span>")

    body = (_crumb(key, table)
            + "<div class=top style='display:flex;justify-content:space-between;align-items:center;margin-bottom:12px'>"
              f"<div class=pgbar>{nav}</div>{new_btn}</div>"
            + f"<div class=tablewrap><table><thead><tr>{head}</tr></thead><tbody>{trs}</tbody></table></div>"
            + "<p class=hint>Click a row to view / edit.</p>")
    return HTMLResponse(page("/db", f"{table}", body))


def _row_at(key: str, table: str, offset: int):
    info, m = _check(key, table)
    if not info:
        return None, None
    cols, pk = m["columns"], m["pk"]
    engine = ENGINES[key]
    order = f'"{pk[0]}"' if pk else f'"{cols[0]}"'
    with engine.connect() as conn:
        row = conn.execute(text(f'SELECT * FROM "{table}" ORDER BY {order} LIMIT 1 OFFSET :o'),
                           {"o": max(offset, 0)}).fetchone()
    return (dict(zip(cols, row)) if row is not None else None), m


@router.get("/db/{key}/{table}/row/{offset}", response_class=HTMLResponse)
async def row_detail(request: Request, key: str, table: str, offset: int):
    rec, m = _row_at(key, table, offset)
    if rec is None:
        return HTMLResponse(page("/db", "not found", "<div class=warn>row not found</div>"), status_code=404)
    kv = "".join(f"<tr><th class=kvk>{_esc(c)}</th><td class=kvv>{_cell(v)}</td></tr>" for c, v in rec.items())
    back_page = offset // _PAGE
    actions = ""
    if m["pk"]:
        actions = (f"<a class='btn p' href='/db/{key}/{table}/row/{offset}/edit'>✎ edit</a>"
                   f"<form method=post action='/db/{key}/{table}/row/{offset}/delete' "
                   "onsubmit=\"return confirm('Delete this row? This cannot be undone.')\" style='display:inline'>"
                   "<button class=danger>🗑 delete</button></form>")
    body = (_crumb(key, table, f" / row #{offset}")
            + "<div class=top style='margin-bottom:12px'>"
              f"<a class=pg href='/db/{key}/{table}?page_={back_page}'>← back to list</a></div>"
            + f"<div class=tablewrap><table><tbody>{kv}</tbody></table></div>"
            + f"<div class=actions>{actions}</div>")
    return HTMLResponse(page("/db", f"{table} · row", body))


def _coerce(col, raw: str):
    if raw == "":
        return None
    try:
        pyt = col.type.python_type
    except Exception:
        return raw
    try:
        if pyt is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if pyt is int:
            return int(raw)
        if pyt is float:
            return float(raw)
    except (ValueError, TypeError):
        return raw
    return raw


def _form_fields(tbl: Table, rec: dict | None) -> str:
    out = ""
    for col in tbl.columns:
        val = "" if rec is None or rec.get(col.name) is None else rec.get(col.name)
        tname = type(col.type).__name__
        out += (f"<label class=fld><span class=nm>{_esc(col.name)} "
                f"<code>{_esc(tname)}{' · PK' if col.primary_key else ''}</code></span>"
                f"<input name='f_{_esc(col.name)}' value='{_esc(val)}'></label>")
    return out


@router.get("/db/{key}/{table}/row/{offset}/edit", response_class=HTMLResponse)
async def edit_form(request: Request, key: str, table: str, offset: int):
    rec, m = _row_at(key, table, offset)
    if rec is None or not m["pk"]:
        return HTMLResponse(page("/db", "not editable",
                                 "<div class=warn>row not found or table has no primary key</div>"), status_code=404)
    tbl = TABLES[key][table]
    body = (_crumb(key, table, f" / row #{offset} / edit")
            + f"<h2>Edit row</h2><form method=post action='/db/{key}/{table}/row/{offset}/edit'>"
            + _form_fields(tbl, rec)
            + f"<div class=actions><button class=p>Save</button>"
              f"<a class=pg href='/db/{key}/{table}/row/{offset}'>cancel</a></div></form>")
    return HTMLResponse(page("/db", f"{table} · edit", body))


@router.post("/db/{key}/{table}/row/{offset}/edit")
async def edit_save(request: Request, key: str, table: str, offset: int):
    rec, m = _row_at(key, table, offset)
    if rec is None or not m["pk"]:
        return HTMLResponse(page("/db", "not editable", "<div class=warn>row gone</div>"), status_code=404)
    tbl = TABLES[key][table]
    form = await request.form()
    vals = {c.name: _coerce(c, form.get(f"f_{c.name}", "")) for c in tbl.columns}
    where = [tbl.c[pk] == rec[pk] for pk in m["pk"]]
    try:
        with ENGINES[key].begin() as conn:
            conn.execute(tbl.update().where(and_(*where)).values(**vals))
    except Exception as e:
        body = (_crumb(key, table) + f"<div class=warn>update failed: {_esc(str(e)[:200])}</div>"
                f"<a class=pg href='/db/{key}/{table}/row/{offset}/edit'>← back</a>")
        return HTMLResponse(page("/db", "error", body), status_code=400)
    return RedirectResponse(f"/db/{key}/{table}/row/{offset}", status_code=303)


@router.post("/db/{key}/{table}/row/{offset}/delete")
async def delete_row(request: Request, key: str, table: str, offset: int):
    rec, m = _row_at(key, table, offset)
    if rec is None or not m["pk"]:
        return HTMLResponse(page("/db", "not deletable", "<div class=warn>row gone</div>"), status_code=404)
    tbl = TABLES[key][table]
    where = [tbl.c[pk] == rec[pk] for pk in m["pk"]]
    with ENGINES[key].begin() as conn:
        conn.execute(tbl.delete().where(and_(*where)))
    return RedirectResponse(f"/db/{key}/{table}?msg=deleted", status_code=303)


@router.get("/db/{key}/{table}/new", response_class=HTMLResponse)
async def new_form(request: Request, key: str, table: str):
    info, m = _check(key, table)
    if not info or not m["pk"]:
        return HTMLResponse(page("/db", "not creatable",
                                 "<div class=warn>unknown table / no primary key</div>"), status_code=404)
    tbl = TABLES[key][table]
    body = (_crumb(key, table, " / new")
            + "<h2>New row</h2><p class=hint>Leave a field blank to use its default / autoincrement.</p>"
            + f"<form method=post action='/db/{key}/{table}/new'>" + _form_fields(tbl, None)
            + f"<div class=actions><button class=p>Create</button>"
              f"<a class=pg href='/db/{key}/{table}'>cancel</a></div></form>")
    return HTMLResponse(page("/db", f"{table} · new", body))


@router.post("/db/{key}/{table}/new")
async def new_save(request: Request, key: str, table: str):
    info, m = _check(key, table)
    if not info or not m["pk"]:
        return HTMLResponse(page("/db", "not creatable", "<div class=warn>unknown table</div>"), status_code=404)
    tbl = TABLES[key][table]
    form = await request.form()
    vals = {}
    for c in tbl.columns:
        raw = form.get(f"f_{c.name}", "")
        if raw != "":
            vals[c.name] = _coerce(c, raw)
    try:
        with ENGINES[key].begin() as conn:
            conn.execute(tbl.insert().values(**vals))
    except Exception as e:
        body = (_crumb(key, table) + f"<div class=warn>insert failed: {_esc(str(e)[:200])}</div>"
                f"<a class=pg href='/db/{key}/{table}/new'>← back</a>")
        return HTMLResponse(page("/db", "error", body), status_code=400)
    return RedirectResponse(f"/db/{key}/{table}?msg=created", status_code=303)
