"""Boards: named Notion-like canvases of pinned assets (charts, sources, text).

A user keeps several boards; pinning offers a board picker (or creates a new one). Each pin
stores its JSON spec (chart → re-fetchable via tool+args; ``kind:'source'`` → an evidence card;
``kind:'text'`` → a writable text block) plus free-canvas layout (x/y/w/h). Per-user scoped.
"""

from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import Board, PinnedArtifact, User
from studioapi.orm_helpers import get_owned

# kinds a pin (dashboard widget) may carry: chart/table artifacts, a source/evidence card, a text
# block, or the dashboard-only feed/calendar widgets.
_ALLOWED_KINDS = {"timeseries", "candlestick", "compare", "table", "kpi", "narrative",
                  "source", "text", "feed", "calendar"}

boards_router = APIRouter(prefix="/boards", tags=["Board"], dependencies=[Depends(require_service)])
router = APIRouter(prefix="/board", tags=["Board"], dependencies=[Depends(require_service)])


# --- boards (multiple named canvases) -------------------------------------
def _default_board(db, email: str) -> Board:
    """The user's first board, created on demand so there's always somewhere to pin."""
    b = db.execute(
        select(Board).where(Board.user_email == email).order_by(Board.created_at)
    ).scalars().first()
    if b is None:
        b = Board(user_email=email, name="내 보드")
        db.add(b)
        db.commit()
        db.refresh(b)
    return b


def _board_dict(b: Board) -> dict:
    return {"id": b.id, "name": b.name, "created_at": b.created_at.isoformat() if b.created_at else None}


class BoardIn(BaseModel):
    name: str = "내 보드"


@boards_router.get("", summary="List the user's boards (creates a default if none)")
async def list_boards(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        _default_board(db, user.email)  # ensure ≥1
        rows = db.execute(
            select(Board).where(Board.user_email == user.email).order_by(Board.created_at)
        ).scalars().all()
        return {"boards": [_board_dict(b) for b in rows]}


@boards_router.post("", summary="Create a board")
async def create_board(body: BoardIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        b = Board(user_email=user.email, name=(body.name or "내 보드")[:120])
        db.add(b)
        db.commit()
        db.refresh(b)
        return _board_dict(b)


@boards_router.patch("/{board_id}", summary="Rename a board")
async def rename_board(board_id: str, body: BoardIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        b = get_owned(db, Board, board_id, user.email, "Board not found.")
        b.name = (body.name or b.name)[:120]
        db.commit()
        db.refresh(b)
        return _board_dict(b)


@boards_router.delete("/{board_id}", summary="Delete a board and its pins")
async def delete_board(board_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        b = get_owned(db, Board, board_id, user.email, "Board not found.")
        for p in db.execute(select(PinnedArtifact).where(PinnedArtifact.board_id == board_id)).scalars().all():
            db.delete(p)
        db.delete(b)
        db.commit()
        return {"deleted": board_id}


# --- pins (assets on a board) ---------------------------------------------
class PinIn(BaseModel):
    spec: dict
    board_ids: list[str] | None = None  # pin to these boards (default board if omitted)
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None


class UpdateIn(BaseModel):
    # canvas layout and/or spec edits (text blocks); any subset.
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    spec: dict | None = None


class AnnotateIn(BaseModel):
    user_annotations: dict | None = None  # PH-VIZ-5 drawings, or null to clear


def _row(p: PinnedArtifact) -> dict:
    return {"id": p.id, "title": p.title, "spec": json.loads(p.spec), "board_id": p.board_id,
            "x": p.x, "y": p.y, "w": p.w, "h": p.h,
            "created_at": p.created_at.isoformat() if p.created_at else None}


@router.get("", summary="List a board's pinned assets")
async def list_pins(board_id: str | None = None, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        default = _default_board(db, user.email)
        bid = board_id or default.id
        rows = db.execute(
            select(PinnedArtifact).where(
                PinnedArtifact.user_email == user.email, PinnedArtifact.board_id == bid)
            .order_by(PinnedArtifact.created_at)
        ).scalars().all()
        # legacy rows (board_id NULL) → surface on the default board so nothing is lost
        if bid == default.id:
            rows += db.execute(
                select(PinnedArtifact).where(
                    PinnedArtifact.user_email == user.email, PinnedArtifact.board_id.is_(None))
            ).scalars().all()
        return {"board_id": bid, "pinned": [_row(p) for p in rows]}


@router.get("/library", summary="All the user's pinned data assets across boards (the 탐색 pin pool)")
async def pin_library(user: User = Depends(current_user)) -> dict:
    """Every data asset the user pinned from 탐색 (chat) — across ALL boards — deduped by content.
    This is the pool the dashboard widget gallery draws from: a widget is always something pinned
    in 탐색. Text memos are excluded (not a datasource); newest first."""
    with SessionLocal() as db:
        rows = db.execute(
            select(PinnedArtifact).where(PinnedArtifact.user_email == user.email)
            .order_by(PinnedArtifact.created_at.desc())
        ).scalars().all()
    seen: set = set()
    out: list[dict] = []
    for p in rows:
        try:
            spec = json.loads(p.spec)
        except (ValueError, TypeError):
            continue
        if spec.get("kind") == "text":  # memos aren't pinned datasources
            continue
        # dedupe: the same asset pinned onto several boards is ONE library entry
        key = (p.title, spec.get("tool"), spec.get("source"),
               json.dumps(spec.get("args") or {}, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(_row(p))
    return {"pins": out}


@router.post("", summary="Pin an asset (chart/source/text) to one or more boards")
async def pin(body: PinIn, user: User = Depends(current_user)) -> dict:
    kind = body.spec.get("kind")
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(422, f"spec.kind must be one of {sorted(_ALLOWED_KINDS)}.")
    title = str(body.spec.get("title") or ("메모" if kind == "text" else "Untitled"))[:200]
    spec_json = json.dumps(body.spec, ensure_ascii=False)
    created: list[dict] = []
    with SessionLocal() as db:
        targets = body.board_ids or [_default_board(db, user.email).id]
        for bid in targets:
            b = db.get(Board, bid)
            if b is None or b.user_email != user.email:
                continue  # skip unknown/forbidden boards silently
            p = PinnedArtifact(user_email=user.email, board_id=bid, title=title, spec=spec_json,
                               x=body.x, y=body.y, w=body.w, h=body.h)
            db.add(p)
            db.commit()
            db.refresh(p)
            created.append(_row(p))
    if not created:
        raise HTTPException(404, "No valid target board.")
    return {"pinned": created}


@router.patch("/{pin_id}", summary="Update a pin's canvas layout and/or text content")
async def update_pin(pin_id: str, body: UpdateIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = get_owned(db, PinnedArtifact, pin_id, user.email, "Pin not found.")
        for f in ("x", "y", "w", "h"):
            v = getattr(body, f)
            if v is not None:
                setattr(p, f, v)
        if body.spec is not None:
            p.spec = json.dumps(body.spec, ensure_ascii=False)
            p.title = str(body.spec.get("title") or p.title)[:200]
        db.commit()
        db.refresh(p)
        return _row(p)


@router.post("/{pin_id}/annotate", summary="Save the user's drawings on a pinned chart (PH-VIZ-5)")
async def annotate_pin(pin_id: str, body: AnnotateIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = get_owned(db, PinnedArtifact, pin_id, user.email, "Pinned artifact not found.")
        spec = json.loads(p.spec)
        if body.user_annotations:
            spec["user_annotations"] = body.user_annotations
        else:
            spec.pop("user_annotations", None)
        p.spec = json.dumps(spec, ensure_ascii=False)
        db.commit()
        db.refresh(p)
        return _row(p)


@router.post("/{pin_id}/refresh", summary="Re-fetch a pinned artifact (live, new as_of)")
async def refresh_pin(pin_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = get_owned(db, PinnedArtifact, pin_id, user.email, "Pinned artifact not found.")
        spec = json.loads(p.spec)
    tool = spec.get("tool")
    if not tool:
        raise HTTPException(400, "This pin has no tool to refresh.")
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.post(
                f"{settings.agent_engine_url}/agent/artifact/refresh",
                json={"tool": tool, "args": spec.get("args") or {}, "title": spec.get("title")},
                headers={"X-API-KEY": user.api_key},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Refresh failed: {exc}")
    if resp.status_code != 200:
        raise HTTPException(resp.status_code, "Refresh failed upstream.")
    fresh = (resp.json() or {}).get("artifact")
    if not fresh:
        raise HTTPException(502, "Refresh returned no artifact.")
    if spec.get("user_annotations"):
        fresh["user_annotations"] = spec["user_annotations"]
    with SessionLocal() as db:
        p = db.get(PinnedArtifact, pin_id)
        p.spec = json.dumps(fresh, ensure_ascii=False)
        p.title = fresh.get("title") or p.title
        db.commit()
        db.refresh(p)
        return _row(p)


@router.delete("/{pin_id}", summary="Remove a pinned asset")
async def unpin(pin_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = get_owned(db, PinnedArtifact, pin_id, user.email, "Pinned artifact not found.")
        db.delete(p)
        db.commit()
        return {"deleted": pin_id}
