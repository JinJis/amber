"""Studio API app: provisioning, conversations, and the chat BFF (SSE)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from studioapi.agents import connectors_router
from studioapi.agents import router as agents_router
from studioapi.agents import seed_templates
from studioapi.alerts import channels_router, deliveries_router, router as alerts_router
from studioapi.chat import sse_tail, start_turn
from studioapi.config import settings
from studioapi.runs import manager as run_manager
from studioapi.db import SessionLocal, init_db
from studioapi.deps import current_user, require_service
from studioapi.models import Conversation, Message, User
from studioapi.orm_helpers import get_owned
from studioapi.market import router as market_router
from studioapi.standing import router as standing_router
from studioapi.search import router as search_router
from studioapi.templates import router as templates_router, seed_dashboard_templates
from studioapi.askfeed import router as askfeed_router
from studioapi.askfeed import start as askfeed_start
from studioapi.deskfeed import router as deskfeed_router
from studioapi.shares import router as shares_router
from studioapi.watchlists import router as watchlists_router
from studioapi.board import boards_router, router as board_router
from studioapi.evidence import router as evidence_router
from studioapi.logos import router as logos_router
from studioapi.prices import router as prices_router
from studioapi.financials import router as financials_router
from studioapi import scheduler
from studioapi.logging_config import install_request_logging, setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    from studioapi.config import assert_production_secrets
    assert_production_secrets()  # AUTH-1: production은 dev 기본 토큰으로 기동 불가
    init_db()
    seed_templates()
    seed_dashboard_templates()
    tasks: list = []
    scheduler.start(tasks)  # background notification-alert dispatcher
    askfeed_start(tasks)    # ASK-5: 5-minute ask-feed refresher (per-ticker questions + hot trend)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Studio API", version="0.1.0", lifespan=lifespan)
install_request_logging(app)


class ChatIn(BaseModel):
    messages: list[dict]
    conversation_id: str | None = None
    agent_id: str | None = None


@app.get("/health", tags=["Meta"])
async def health() -> dict:
    return {"status": "ok"}


@app.post("/users/ensure", tags=["Users"], dependencies=[Depends(require_service)])
async def users_ensure(user: User = Depends(current_user)) -> dict:
    return {"email": user.email, "tenant_id": user.tenant_id, "project_id": user.project_id,
            "onboarded": bool(user.onboarded)}


def _profile(user: User) -> dict:
    return {"email": user.email, "name": user.name or (user.email.split("@")[0]),
            "image": user.image, "plan": user.plan or "free", "onboarded": bool(user.onboarded)}


@app.get("/users/me", tags=["Users"], dependencies=[Depends(require_service)])
async def users_me(user: User = Depends(current_user)) -> dict:
    return _profile(user)


class ProfileIn(BaseModel):
    name: str | None = None
    image: str | None = None


@app.patch("/users/me", tags=["Users"], dependencies=[Depends(require_service)])
async def update_me(body: ProfileIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        u = db.get(User, user.email)
        if u is None:
            raise HTTPException(404, "user not found")
        if body.name is not None:
            n = body.name.strip()
            if not (1 <= len(n) <= 120):
                raise HTTPException(422, "이름은 1~120자여야 해요.")
            u.name = n
        if body.image is not None:
            u.image = body.image.strip()[:512] or None
        db.commit()
        db.refresh(u)
        return _profile(u)


@app.get("/users/me/usage", tags=["Users"], dependencies=[Depends(require_service)])
async def usage_me(user: User = Depends(current_user)) -> dict:
    """The user's metered tool-call usage + cost, read from the control-plane (admin) for their
    project. Best-effort — an outage returns an empty summary, never a 500 (settings still renders)."""
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            r = await client.get(f"{settings.control_plane_url}/admin/projects/{user.project_id}/usage",
                                 headers={"X-Admin-Token": settings.admin_token})
        data = r.json() if r.status_code == 200 else {}
    except Exception:  # noqa: BLE001
        data = {}
    return {"plan": user.plan or "free", "usage": data}


@app.post("/users/onboarded", tags=["Users"], dependencies=[Depends(require_service)])
async def users_onboarded(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        u = db.get(User, user.email)
        if u is not None:
            u.onboarded = True
            db.commit()
    return {"email": user.email, "onboarded": True}


@app.post("/conversations/{conversation_id}/stop", tags=["Conversations"],
          dependencies=[Depends(require_service)])
async def stop_run(conversation_id: str, user: User = Depends(current_user)) -> dict:
    """UXQ-2: 스트리밍 중지 — 서버측 런 취소(생성은 서버에 사니 클라 이탈만으론 안 멈춤)."""
    from studioapi.runs import manager
    return {"stopped": manager.cancel(conversation_id)}


@app.get("/conversations", tags=["Conversations"], dependencies=[Depends(require_service)])
async def list_conversations(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Conversation).where(Conversation.user_email == user.email).order_by(Conversation.created_at.desc())
        ).scalars().all()
        return {"conversations": [{"id": c.id, "title": c.title, "agent_id": c.agent_id} for c in rows]}


class ConvPatch(BaseModel):
    title: str


@app.patch("/conversations/{conversation_id}", tags=["Conversations"], dependencies=[Depends(require_service)])
async def rename_conversation(conversation_id: str, body: ConvPatch,
                              user: User = Depends(current_user)) -> dict:
    """UXQ-4: 대화 제목 변경 (소유자만)."""
    t = body.title.strip()
    if not (1 <= len(t) <= 120):
        raise HTTPException(422, "제목은 1~120자여야 해요.")
    with SessionLocal() as db:
        c = db.get(Conversation, conversation_id)
        if c is None or c.user_email != user.email:
            raise HTTPException(404, "conversation not found")
        c.title = t
        db.commit()
        return {"id": c.id, "title": c.title}


@app.delete("/conversations/{conversation_id}", tags=["Conversations"], dependencies=[Depends(require_service)])
async def delete_conversation(conversation_id: str, user: User = Depends(current_user)) -> dict:
    """UXQ-4: 대화 삭제 (소유자만, 메시지 포함)."""
    with SessionLocal() as db:
        c = db.get(Conversation, conversation_id)
        if c is None or c.user_email != user.email:
            raise HTTPException(404, "conversation not found")
        db.execute(Message.__table__.delete().where(Message.conversation_id == conversation_id))
        db.delete(c)
        db.commit()
        return {"deleted": conversation_id}


@app.get("/conversations/{conversation_id}/messages", tags=["Conversations"], dependencies=[Depends(require_service)])
async def conversation_messages(conversation_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
        ).scalars().all()
        return {"messages": [
            {"role": m.role, "content": m.content,
             "citations": json.loads(m.citations) if m.citations else [],
             "artifacts": json.loads(m.artifacts) if m.artifacts else [],
             "audit": json.loads(m.audit) if m.audit else None,
             "hook": m.hook,
             "suggestions": json.loads(m.suggestions) if m.suggestions else []}
            for m in rows
        ]}


@app.post("/chat/stream", tags=["Chat"], dependencies=[Depends(require_service)])
async def chat_stream(body: ChatIn, user: User = Depends(current_user)) -> StreamingResponse:
    # generation runs in the BACKGROUND (survives the client leaving); the response tails it
    run = start_turn(user, body.conversation_id, body.messages, body.agent_id)
    return StreamingResponse(sse_tail(run, 0), media_type="text/event-stream")


@app.get("/conversations/{conversation_id}/active-run", tags=["Chat"], dependencies=[Depends(require_service)])
async def conversation_active_run(conversation_id: str, user: User = Depends(current_user)) -> dict:
    """The run still generating for this conversation, if any — so re-entering resumes it live."""
    with SessionLocal() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_email != user.email:
            return {"run_id": None}
    return {"run_id": run_manager.active_run_id(conversation_id)}


@app.get("/runs/{run_id}/stream", tags=["Chat"], dependencies=[Depends(require_service)])
async def run_stream(run_id: str, user: User = Depends(current_user), from_index: int = 0) -> StreamingResponse:
    """Tail (resume) an in-flight or finished run from ``from_index`` — replay buffered events,
    then live ones. Used when the user re-enters a conversation whose answer is still generating."""
    run = run_manager.get(run_id)
    if run is None:
        raise HTTPException(404, "Run not found or already evicted.")
    with SessionLocal() as db:
        get_owned(db, Conversation, run.conversation_id, user.email, "Run not found.")
    return StreamingResponse(sse_tail(run, from_index), media_type="text/event-stream")


app.include_router(agents_router)
app.include_router(connectors_router)
app.include_router(alerts_router)
app.include_router(channels_router)
app.include_router(deliveries_router)
app.include_router(templates_router)
app.include_router(watchlists_router)
app.include_router(deskfeed_router)
app.include_router(askfeed_router)
app.include_router(shares_router)
app.include_router(board_router)
app.include_router(boards_router)
app.include_router(evidence_router)
app.include_router(logos_router)
app.include_router(prices_router)
app.include_router(financials_router)
app.include_router(search_router)
app.include_router(market_router)
app.include_router(standing_router)
