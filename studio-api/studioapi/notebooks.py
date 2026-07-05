"""M-NB (NB-1) — 리서치 노트북: vertical block documents of pinned evidence + the user's notes.

Blocks are SNAPSHOTS (a note read later shows what was true then); `note` is the researcher's
one-liner for WHY a pin was kept. Sharing delegates to the existing SH pipeline (kind=note) so
the public page / card image / A4 render need no new infrastructure.
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func as _f, select

from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import Notebook, NoteBlock, User

router = APIRouter(prefix="/notebooks", tags=["Notebooks"], dependencies=[Depends(require_service)])

_KINDS = {"pin_artifact", "pin_citation", "pin_ledger", "text"}
_MAX_BLOCKS = 200


class NotebookIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class BlockIn(BaseModel):
    kind: str
    payload: dict = Field(default_factory=dict)   # snapshot JSON; text blocks: {"md": "..."}
    note: str | None = Field(None, max_length=500)


class BlockPatch(BaseModel):
    note: str | None = None
    md: str | None = None                          # text blocks only
    position: int | None = None


def _nb_out(nb: Notebook, count: int) -> dict:
    return {"id": nb.id, "title": nb.title, "blocks": count,
            "created_at": nb.created_at.isoformat() if nb.created_at else None,
            "updated_at": nb.updated_at.isoformat() if nb.updated_at else None}


def _blk_out(b: NoteBlock) -> dict:
    return {"id": b.id, "kind": b.kind, "position": b.position, "note": b.note,
            "payload": json.loads(b.payload or "{}"),
            "created_at": b.created_at.isoformat() if b.created_at else None}


def _owned(db, notebook_id: str, email: str) -> Notebook:
    nb = db.get(Notebook, notebook_id)
    if nb is None or nb.user_email != email:
        raise HTTPException(404, "notebook not found")
    return nb


@router.get("", summary="내 노트북 목록")
async def list_notebooks(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(Notebook).where(Notebook.user_email == user.email)
                          .order_by(Notebook.updated_at.desc())).scalars().all()
        counts = dict(db.execute(
            select(NoteBlock.notebook_id, _f.count()).group_by(NoteBlock.notebook_id)).all())
        return {"notebooks": [_nb_out(nb, counts.get(nb.id, 0)) for nb in rows]}


@router.post("", summary="노트북 만들기")
async def create_notebook(body: NotebookIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = Notebook(user_email=user.email, title=body.title.strip())
        db.add(nb)
        db.commit()
        return _nb_out(nb, 0)


@router.get("/{notebook_id}", summary="노트북 문서 — 블록 순서대로")
async def get_notebook(notebook_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        blocks = db.execute(select(NoteBlock).where(NoteBlock.notebook_id == nb.id)
                            .order_by(NoteBlock.position, NoteBlock.created_at)).scalars().all()
        return {**_nb_out(nb, len(blocks)), "block_list": [_blk_out(b) for b in blocks]}


@router.patch("/{notebook_id}", summary="제목 변경")
async def rename_notebook(notebook_id: str, body: NotebookIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        nb.title = body.title.strip()
        db.commit()
        return _nb_out(nb, 0)


@router.delete("/{notebook_id}", summary="노트북 삭제 (블록 포함)")
async def delete_notebook(notebook_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        for b in db.execute(select(NoteBlock).where(NoteBlock.notebook_id == nb.id)).scalars():
            db.delete(b)
        db.delete(nb)
        db.commit()
        return {"deleted": notebook_id}


@router.post("/{notebook_id}/blocks", summary="블록 담기 — pin 스냅샷 or 텍스트 메모")
async def add_block(notebook_id: str, body: BlockIn, user: User = Depends(current_user)) -> dict:
    if body.kind not in _KINDS:
        raise HTTPException(422, f"unknown block kind {body.kind!r}")
    if body.kind == "text" and not (body.payload.get("md") or "").strip():
        raise HTTPException(422, "text block needs payload.md")
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        n = db.execute(select(_f.count()).select_from(NoteBlock)
                       .where(NoteBlock.notebook_id == nb.id)).scalar() or 0
        if n >= _MAX_BLOCKS:
            raise HTTPException(429, "노트북 블록 한도에 도달했습니다.")
        last = db.execute(select(_f.max(NoteBlock.position))
                          .where(NoteBlock.notebook_id == nb.id)).scalar()
        blk = NoteBlock(notebook_id=nb.id, position=(last or 0) + 10, kind=body.kind,
                        payload=json.dumps(body.payload, ensure_ascii=False), note=body.note)
        db.add(blk)
        nb.updated_at = datetime.utcnow()
        db.commit()
        return _blk_out(blk)


@router.patch("/{notebook_id}/blocks/{block_id}", summary="블록 수정 — 메모/본문/순서")
async def patch_block(notebook_id: str, block_id: str, body: BlockPatch,
                      user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        blk = db.get(NoteBlock, block_id)
        if blk is None or blk.notebook_id != nb.id:
            raise HTTPException(404, "block not found")
        if body.note is not None:
            blk.note = body.note.strip() or None
        if body.md is not None:
            if blk.kind != "text":
                raise HTTPException(422, "pin 블록의 스냅샷은 수정할 수 없습니다 — 메모(note)만 편집하세요.")
            blk.payload = json.dumps({"md": body.md}, ensure_ascii=False)
        if body.position is not None:
            blk.position = body.position
        nb.updated_at = datetime.utcnow()
        db.commit()
        return _blk_out(blk)


@router.delete("/{notebook_id}/blocks/{block_id}", summary="블록 제거")
async def delete_block(notebook_id: str, block_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        nb = _owned(db, notebook_id, user.email)
        blk = db.get(NoteBlock, block_id)
        if blk is None or blk.notebook_id != nb.id:
            raise HTTPException(404, "block not found")
        db.delete(blk)
        nb.updated_at = datetime.utcnow()
        db.commit()
        return {"deleted": block_id}
