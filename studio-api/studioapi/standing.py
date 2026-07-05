"""M-SA (SA-2/SA-3) — standing questions: 질문 구독 CRUD + the desk-feed signature check.

A standing question is NOT an alert rule: no thresholds, no channels. One tap subscribes the
QUESTION; the desk-feed generation calls ``check_standing`` which re-runs each subscription's
recorded probe (one gateway call), derives a cheap SIGNATURE (freshest as_of / accession /
date / bar identifier in the payload), and emits a ``standing_update`` desk card when it
changed. Deterministic — no LLM in the check path; card copy is factual ("새 공시 접수").
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func as _f, select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import StandingQuestion, User

log = logging.getLogger(__name__)
router = APIRouter(prefix="/standing", tags=["Standing Questions"], dependencies=[Depends(require_service)])

_CAP = 10
_CADENCES = {"daily", "event", "weekly"}


class StandingIn(BaseModel):
    question: str = Field(min_length=4, max_length=400)
    ticker: str | None = None
    market: str | None = None
    cadence: str = "daily"
    probe: dict = Field(default_factory=dict)   # {"tool": "...", "path": "/filings", "args": {...}}


def _out(sq: StandingQuestion) -> dict:
    return {"id": sq.id, "question": sq.question, "ticker": sq.ticker, "market": sq.market,
            "cadence": sq.cadence, "active": sq.active,
            "last_checked_at": sq.last_checked_at.isoformat() if sq.last_checked_at else None,
            "created_at": sq.created_at.isoformat() if sq.created_at else None}


@router.get("", summary="지켜보는 질문 목록")
async def list_standing(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(StandingQuestion)
                          .where(StandingQuestion.user_email == user.email,
                                 StandingQuestion.active.is_(True))
                          .order_by(StandingQuestion.created_at.desc())).scalars().all()
        return {"standing": [_out(sq) for sq in rows]}


@router.post("", summary="이 질문 계속 지켜보기 (구독)")
async def create_standing(body: StandingIn, user: User = Depends(current_user)) -> dict:
    if body.cadence not in _CADENCES:
        raise HTTPException(422, f"cadence must be one of {sorted(_CADENCES)}")
    with SessionLocal() as db:
        n = db.execute(select(_f.count()).select_from(StandingQuestion)
                       .where(StandingQuestion.user_email == user.email,
                              StandingQuestion.active.is_(True))).scalar() or 0
        if n >= _CAP:
            raise HTTPException(429, f"지켜보는 질문은 최대 {_CAP}개입니다 — 기존 구독을 해제해주세요.")
        # idempotent on the exact question — re-tapping the chip never duplicates
        dup = db.execute(select(StandingQuestion)
                         .where(StandingQuestion.user_email == user.email,
                                StandingQuestion.question == body.question.strip(),
                                StandingQuestion.active.is_(True))).scalar_one_or_none()
        if dup:
            return _out(dup)
        sq = StandingQuestion(user_email=user.email, question=body.question.strip(),
                              ticker=body.ticker, market=body.market, cadence=body.cadence,
                              probe=json.dumps(body.probe, ensure_ascii=False))
        db.add(sq)
        db.commit()
        return _out(sq)


@router.delete("/{sq_id}", summary="구독 해제")
async def delete_standing(sq_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        sq = db.get(StandingQuestion, sq_id)
        if sq is None or sq.user_email != user.email:
            raise HTTPException(404, "standing question not found")
        sq.active = False
        db.commit()
        return {"deleted": sq_id}


# ── SA-3: the deterministic signature check (piggybacked by desk-feed generation) ──────────

_SIG_KEYS = ("accession_number", "as_of", "filing_date", "filed", "report_period", "date", "time")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def signature_of(payload) -> str | None:
    """A cheap change fingerprint: the LEXICALLY LATEST date-like value + latest accession in
    the payload. Deterministic, no LLM; identical payloads → identical signature."""
    dates: list[str] = []
    accns: list[str] = []

    def walk(obj, depth=0):
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in _SIG_KEYS and isinstance(v, str):
                    if k == "accession_number":
                        accns.append(v)
                    else:
                        m = _DATE_RE.search(v)
                        if m:
                            dates.append(m.group(0))
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:100]:
                walk(v, depth + 1)

    walk(payload)
    if not dates and not accns:
        return None
    raw = f"{max(dates) if dates else ''}|{max(accns) if accns else ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16] + ":" + raw[:80]


async def check_standing(user: User) -> list[dict]:
    """Re-probe each active subscription (one gateway call each); a changed signature emits a
    `standing_update` desk card {kind, hook, question, citations}. First check only BASELINES
    the signature (no card — nothing 'changed' yet from the user's perspective)."""
    with SessionLocal() as db:
        rows = db.execute(select(StandingQuestion)
                          .where(StandingQuestion.user_email == user.email,
                                 StandingQuestion.active.is_(True))).scalars().all()
        cards: list[dict] = []
        if not rows:
            return cards
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            for sq in rows:
                probe = json.loads(sq.probe or "{}")
                path, args = probe.get("path"), probe.get("args") or {}
                if not path:
                    continue
                try:
                    resp = await client.get(f"{settings.control_plane_url}{path}", params=args,
                                            headers={"X-API-KEY": user.api_key})
                    if resp.status_code != 200:
                        continue
                    sig = signature_of(resp.json())
                except (httpx.HTTPError, ValueError):
                    continue
                changed = bool(sig and sq.last_signature and sig != sq.last_signature)
                if sig and (changed or not sq.last_signature):
                    sq.last_signature = sig
                sq.last_checked_at = datetime.utcnow()
                if changed:
                    label = sq.ticker or "지켜보던 데이터"
                    cards.append({
                        "kind": "standing_update",
                        "hook": f"🔔 {label} — 지켜보던 데이터가 갱신되었습니다",
                        "question": sq.question,
                        "citations": [{"source": probe.get("source") or "gateway",
                                       "as_of": (sig.split(":", 1)[1].split("|")[0] or None)}],
                        "ticker": sq.ticker,
                    })
        db.commit()
        return cards
