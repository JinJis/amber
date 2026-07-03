"""Dashboard templates (F2): seeded widget-sets a user can apply to a board in one click.

Each template is a list of widgets ``{spec, x, y, w, h}`` where ``spec`` is a normal pin spec
(kind + title + source + optional tool/args/viz). ``POST /board/from-template`` materializes them
as :class:`PinnedArtifact` rows on the chosen board; widgets that carry a tool refresh live, and
widgets without one render an honest gap until the user wires data (never fabricated).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from studioapi.db import SessionLocal
from studioapi.demo_template import demo_template
from studioapi.deps import current_user, require_service
from studioapi.models import Board, DashboardTemplate, PinnedArtifact, User

router = APIRouter(tags=["Templates"], dependencies=[Depends(require_service)])

_GRID = 6  # column step used to lay widgets out in a simple flow


def _w(kind: str, title: str, source: str, *, tool=None, args=None, viz=None, cols=1) -> dict:
    """One template widget spec. ``cols`` is a layout hint (1 or 2 grid columns)."""
    spec = {"kind": kind, "title": title, "source": source, "viz": viz}
    if tool:
        spec["tool"] = tool
        spec["args"] = args or {}
    return {"spec": {k: v for k, v in spec.items() if v is not None}, "cols": cols}


# Provided templates. Price/timeseries widgets use yahoo__prices (refreshes live); macro/fundamental
# widgets are sourced placeholders that render a gap until wired (honest, never fabricated).
DASHBOARD_TEMPLATES: list[dict] = [
    # a rich, self-contained live-demo board (embedded mock data → renders without any backend call)
    demo_template(),
    {"id": "dt_semi", "name": "반도체 모니터", "market": None,
     "description": "6 위젯 · Yahoo·DART·뉴스",
     "widgets": [
         _w("timeseries", "삼성전자 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "005930.KS", "market": "KR", "interval": "day"}, viz="line", cols=2),
         _w("timeseries", "SK하이닉스 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "000660.KS", "market": "KR", "interval": "day"}, viz="line", cols=2),
         _w("kpi", "SK하이닉스 PER", "OpenDART", viz="value"),
         _w("table", "메모리 3사 밸류 비교", "OpenDART · SEC", viz="table", cols=2),
         _w("kpi", "TSMC 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "TSM", "market": "US", "interval": "day"}, viz="value"),
         _w("feed", "반도체 뉴스", "Google News", viz="feed"),
     ]},
    {"id": "dt_macro", "name": "거시·금리 모니터", "market": None,
     "description": "5 위젯 · FRED·ECOS",
     "widgets": [
         _w("kpi", "미 기준금리 (FFR)", "FRED", viz="value"),
         _w("kpi", "한국 기준금리", "ECOS", viz="value"),
         _w("timeseries", "미 CPI (YoY)", "FRED", viz="line", cols=2),
         _w("timeseries", "원/달러 환율", "ECOS", viz="line", cols=2),
         _w("kpi", "미 실업률", "FRED", viz="value"),
     ]},
    {"id": "dt_dividend", "name": "배당·인컴", "market": None,
     "description": "5 위젯 · 재무·밸류",
     "widgets": [
         _w("table", "고배당 종목 비교", "OpenDART · SEC", viz="table", cols=2),
         _w("kpi", "배당수익률 평균", "OpenDART", viz="value"),
         _w("timeseries", "배당 추이", "OpenDART", viz="line", cols=2),
         _w("kpi", "배당성향", "OpenDART", viz="value"),
         _w("feed", "배당 공시", "DART", viz="feed"),
     ]},
    {"id": "dt_portfolio", "name": "내 포트폴리오", "market": None,
     "description": "4 위젯 · 시세·수급",
     "widgets": [
         _w("table", "보유 종목 시세", "Yahoo · KIS", viz="table", cols=2),
         _w("kpi", "포트폴리오 평가액", "계산", viz="value"),
         _w("timeseries", "수익률 추이", "계산", viz="line", cols=2),
         _w("feed", "보유 종목 뉴스", "Google News", viz="feed"),
     ]},
    {"id": "dt_bigtech", "name": "미국 빅테크", "market": "US",
     "description": "6 위젯 · Yahoo·SEC",
     "widgets": [
         _w("timeseries", "NVDA 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "NVDA", "market": "US", "interval": "day"}, viz="line", cols=2),
         _w("timeseries", "AAPL 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "AAPL", "market": "US", "interval": "day"}, viz="line", cols=2),
         _w("kpi", "MSFT 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "MSFT", "market": "US", "interval": "day"}, viz="value"),
         _w("table", "빅테크 밸류 비교", "SEC EDGAR", viz="table", cols=2),
         _w("kpi", "GOOGL 종가", "Yahoo Finance", tool="yahoo__prices", args={"ticker": "GOOGL", "market": "US", "interval": "day"}, viz="value"),
         _w("feed", "빅테크 뉴스", "Google News", viz="feed"),
     ]},
    {"id": "dt_energy", "name": "에너지·원자재", "market": None,
     "description": "5 위젯 · 시세·거시",
     "widgets": [
         _w("timeseries", "WTI 유가", "FRED", viz="line", cols=2),
         _w("kpi", "천연가스", "FRED", viz="value"),
         _w("timeseries", "구리 가격", "FRED", viz="line", cols=2),
         _w("kpi", "금 가격", "FRED", viz="value"),
         _w("feed", "원자재 뉴스", "Google News", viz="feed"),
     ]},
    {"id": "dt_fx", "name": "환율·채권", "market": None,
     "description": "4 위젯 · ECOS·FRED",
     "widgets": [
         _w("timeseries", "원/달러", "ECOS", viz="line", cols=2),
         _w("kpi", "미 10년 국채", "FRED", viz="value"),
         _w("timeseries", "한국 국고채 3년", "ECOS", viz="line", cols=2),
         _w("kpi", "달러인덱스", "FRED", viz="value"),
     ]},
]


def seed_dashboard_templates() -> None:
    """UPSERT the provided templates (edits land on redeploy); remove stale ones."""
    ids = {t["id"] for t in DASHBOARD_TEMPLATES}
    with SessionLocal() as db:
        for d in db.query(DashboardTemplate).all():
            if d.id not in ids:
                db.delete(d)
        for t in DASHBOARD_TEMPLATES:
            d = db.get(DashboardTemplate, t["id"])
            payload = dict(name=t["name"], description=t.get("description"),
                           market=t.get("market"), widgets=json.dumps(t["widgets"], ensure_ascii=False))
            if d is None:
                db.add(DashboardTemplate(id=t["id"], **payload))
            else:
                d.name, d.description, d.market, d.widgets = (
                    payload["name"], payload["description"], payload["market"], payload["widgets"])
        db.commit()


def _tpl_out(d: DashboardTemplate) -> dict:
    return {"id": d.id, "name": d.name, "description": d.description, "market": d.market,
            "widgets": json.loads(d.widgets or "[]")}


@router.get("/templates", summary="List provided dashboard templates")
async def list_templates(_: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(DashboardTemplate).order_by(DashboardTemplate.id)).scalars().all()
        return {"templates": [_tpl_out(d) for d in rows]}


class FromTemplateIn(BaseModel):
    template_id: str
    board_id: str | None = None  # default board if omitted


@router.post("/board/from-template", summary="Materialize a template's widgets as pins on a board")
async def board_from_template(body: FromTemplateIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        tpl = db.get(DashboardTemplate, body.template_id)
        if tpl is None:
            raise HTTPException(404, "Template not found.")
        # resolve the target board (default to the user's first board)
        if body.board_id:
            board = db.get(Board, body.board_id)
            if board is None or board.user_email != user.email:
                raise HTTPException(404, "Board not found.")
        else:
            board = db.execute(
                select(Board).where(Board.user_email == user.email).order_by(Board.created_at)
            ).scalars().first()
            if board is None:
                board = Board(user_email=user.email, name=tpl.name)
                db.add(board)
                db.commit()
                db.refresh(board)
        widgets = json.loads(tpl.widgets or "[]")
        # Idempotent: don't re-add a widget already on this board (by title) — applying the same
        # template twice (e.g. onboarding + manual, or a double trigger) must not duplicate widgets.
        existing = {
            p.title for p in db.execute(
                select(PinnedArtifact).where(
                    PinnedArtifact.user_email == user.email, PinnedArtifact.board_id == board.id)
            ).scalars().all()
        }
        # Lay widgets out in GRID UNITS (12-col grid) so the dashboard's react-grid-layout places
        # them cleanly. A widget may carry an EXPLICIT placement (x/y/w/h grid units) for a designed
        # board (the live-demo template); otherwise flow L→R, wrap at 12 (w = 6 wide else 4, h = 7).
        created = []
        x = y = 0
        rowh = 0
        for w in widgets:
            spec = w.get("spec") or {}
            title = str(spec.get("title") or "위젯")[:200]
            if title in existing:
                continue
            existing.add(title)
            if all(k in w for k in ("x", "y", "w", "h")):   # explicit, designed placement
                gx, gy, gw, gh = int(w["x"]), int(w["y"]), int(w["w"]), int(w["h"])
            else:                                            # auto-flow
                gw = 6 if int(w.get("cols", 1)) == 2 else 4
                gh = 7
                if x + gw > 12:
                    x = 0
                    y += rowh
                    rowh = 0
                gx, gy = x, y
                x += gw
                rowh = max(rowh, gh)
            p = PinnedArtifact(
                user_email=user.email, board_id=board.id, title=title,
                spec=json.dumps(spec, ensure_ascii=False),
                x=gx, y=gy, w=gw, h=gh,
            )
            db.add(p)
            created.append(p)
        db.commit()
        return {"board_id": board.id, "created": len(created)}
