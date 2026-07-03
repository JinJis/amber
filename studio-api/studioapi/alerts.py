"""Notification alerts (F3) — REST + dispatch + channel connections.

An alert is a standing watcher scoped to a whole board or a single widget. When due it renders a
SOURCED message (title/body/source/as_of/deeplink — never advice/forecast) and pushes it to the
user's channels, recording one :class:`NotificationDelivery` per channel. The scheduler
(``scheduler.py``) fires due alerts; the alert sheet (F5) creates them via ``POST /alerts``;
``POST /alerts/{id}/fire`` fires one immediately (nudge-free e2e + the UI "테스트 발송").

Trust by construction: the body is assembled only from sourced facts. :func:`_factual_guard` is a
defensive tripwire (the real guarantee is facts-only construction + the agent-engine guardrail when
the message is enriched live).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from studioapi import channels as channels_mod
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.orm_helpers import get_owned
from studioapi.models import (
    ChannelConnection,
    NotificationAlert,
    NotificationDelivery,
    User,
)

from studioapi.alerts_render import (  # noqa: F401 — re-exported for scheduler.py + tests
    TRIGGER_TYPES,
    _TRIGGER_META,
    compute_next_fire,
    fire_alert,
    render_message,
)

router = APIRouter(prefix="/alerts", tags=["Alerts"], dependencies=[Depends(require_service)])
channels_router = APIRouter(prefix="/channels", tags=["Channels"], dependencies=[Depends(require_service)])
deliveries_router = APIRouter(prefix="/deliveries", tags=["Alerts"], dependencies=[Depends(require_service)])



# --------------------------------------------------------------------------- schemas
class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scope: str = "board"  # board | widget
    board_id: str | None = None
    pin_id: str | None = None
    trigger_type: str
    params: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    channels: list[str] = Field(default_factory=list)
    quiet_hours: dict | None = None
    source_spec: dict | None = None


class AlertPatch(BaseModel):
    name: str | None = None
    trigger_type: str | None = None
    params: dict | None = None
    schedule: dict | None = None
    channels: list[str] | None = None
    quiet_hours: dict | None = None
    status: str | None = None


class ChannelIn(BaseModel):
    channel: str
    config: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- helpers
def _out(a: NotificationAlert) -> dict:
    return {
        "id": a.id, "name": a.name, "scope": a.scope,
        "board_id": a.board_id, "pin_id": a.pin_id,
        "trigger_type": a.trigger_type,
        "trigger_label": _TRIGGER_META.get(a.trigger_type, {}).get("label", a.trigger_type),
        "params": json.loads(a.params) if a.params else {},
        "schedule": json.loads(a.schedule) if a.schedule else {},
        "channels": json.loads(a.channels or "[]"),
        "quiet_hours": json.loads(a.quiet_hours) if a.quiet_hours else None,
        "status": a.status,
        "source_spec": json.loads(a.source_spec) if a.source_spec else None,
        "last_fired_at": a.last_fired_at.isoformat() if a.last_fired_at else None,
        "next_fire_at": a.next_fire_at.isoformat() if a.next_fire_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _delivery_out(d: NotificationDelivery) -> dict:
    return {
        "id": d.id, "alert_id": d.alert_id, "channel": d.channel, "status": d.status,
        "payload": {"title": d.title, "body": d.body, "as_of": d.as_of,
                    "source": d.source, "deeplink": d.deeplink},
        "sent_at": d.sent_at.isoformat() if d.sent_at else None,
    }


@router.get("", summary="List the user's alerts")
async def list_alerts(scope: str | None = None, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        q = select(NotificationAlert).where(NotificationAlert.user_email == user.email)
        if scope in ("board", "widget"):
            q = q.where(NotificationAlert.scope == scope)
        rows = db.execute(q.order_by(NotificationAlert.created_at.desc())).scalars().all()
        return {"alerts": [_out(a) for a in rows]}


@router.post("", summary="Create (enable) an alert")
async def create_alert(body: AlertIn, user: User = Depends(current_user)) -> dict:
    if body.trigger_type not in TRIGGER_TYPES:
        raise HTTPException(422, f"Unknown trigger_type. Use one of {TRIGGER_TYPES}.")
    if body.scope not in ("board", "widget"):
        raise HTTPException(422, "scope must be board|widget.")
    bad = [c for c in body.channels if c not in channels_mod.SUPPORTED]
    if bad:
        raise HTTPException(422, f"Unsupported channels: {bad}")
    a = NotificationAlert(
        user_email=user.email, name=body.name, scope=body.scope,
        board_id=body.board_id, pin_id=body.pin_id, trigger_type=body.trigger_type,
        params=json.dumps(body.params, ensure_ascii=False),
        schedule=json.dumps(body.schedule, ensure_ascii=False),
        channels=json.dumps(body.channels, ensure_ascii=False),
        quiet_hours=json.dumps(body.quiet_hours, ensure_ascii=False) if body.quiet_hours else None,
        source_spec=json.dumps(body.source_spec, ensure_ascii=False) if body.source_spec else None,
        status="active", next_fire_at=compute_next_fire(body.schedule),
    )
    with SessionLocal() as db:
        db.add(a)
        db.commit()
        return _out(a)


def _owned(db, alert_id: str, user: User) -> NotificationAlert:
    return get_owned(db, NotificationAlert, alert_id, user.email, "Alert not found.")


@router.get("/{alert_id}", summary="Get one alert")
async def get_alert(alert_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        return _out(_owned(db, alert_id, user))


@router.patch("/{alert_id}", summary="Update an alert")
async def update_alert(alert_id: str, body: AlertPatch, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = _owned(db, alert_id, user)
        if body.name is not None:
            a.name = body.name
        if body.trigger_type is not None:
            if body.trigger_type not in TRIGGER_TYPES:
                raise HTTPException(422, "Unknown trigger_type.")
            a.trigger_type = body.trigger_type
        if body.params is not None:
            a.params = json.dumps(body.params, ensure_ascii=False)
        if body.schedule is not None:
            a.schedule = json.dumps(body.schedule, ensure_ascii=False)
            a.next_fire_at = compute_next_fire(body.schedule)
        if body.channels is not None:
            bad = [c for c in body.channels if c not in channels_mod.SUPPORTED]
            if bad:
                raise HTTPException(422, f"Unsupported channels: {bad}")
            a.channels = json.dumps(body.channels, ensure_ascii=False)
        if body.quiet_hours is not None:
            a.quiet_hours = json.dumps(body.quiet_hours, ensure_ascii=False)
        if body.status is not None:
            if body.status not in ("active", "paused"):
                raise HTTPException(422, "status must be active|paused.")
            a.status = body.status
        db.commit()
        return _out(a)


@router.delete("/{alert_id}", summary="Delete an alert")
async def delete_alert(alert_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        db.delete(_owned(db, alert_id, user))
        db.commit()
        return {"deleted": alert_id}


@router.post("/{alert_id}/pause", summary="Pause an alert")
async def pause_alert(alert_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = _owned(db, alert_id, user)
        a.status = "paused"
        db.commit()
        return _out(a)


@router.post("/{alert_id}/resume", summary="Resume an alert")
async def resume_alert(alert_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = _owned(db, alert_id, user)
        a.status = "active"
        db.commit()
        return _out(a)


@router.post("/{alert_id}/fire", summary="Fire an alert now (test send)")
async def fire_alert_now(alert_id: str, user: User = Depends(current_user)) -> dict:
    # fire_alert now does a blocking, network-bound data fetch → run it off the event loop in its
    # own session (self-contained so the thread never shares a session with the request).
    def _run() -> dict | None:
        with SessionLocal() as db:
            a = db.get(NotificationAlert, alert_id)
            if a is None or a.user_email != user.email:
                return None
            deliveries = fire_alert(a, db)
            db.commit()
            db.refresh(a)
            return {"alert": _out(a), "deliveries": [_delivery_out(d) for d in deliveries]}

    res = await asyncio.to_thread(_run)
    if res is None:
        raise HTTPException(404, "Alert not found.")
    return res


@router.get("/{alert_id}/deliveries", summary="An alert's recent deliveries")
async def alert_deliveries(alert_id: str, limit: int = 50, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        _owned(db, alert_id, user)
        rows = db.execute(
            select(NotificationDelivery).where(NotificationDelivery.alert_id == alert_id)
            .order_by(NotificationDelivery.sent_at.desc()).limit(min(limit, 200))
        ).scalars().all()
        return {"deliveries": [_delivery_out(d) for d in rows]}


# --------------------------------------------------------------------------- /deliveries
@deliveries_router.get("", summary="Recent deliveries across alerts (optionally one alert)")
async def list_deliveries(alert_id: str | None = None, limit: int = 50, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        q = select(NotificationDelivery).where(NotificationDelivery.user_email == user.email)
        if alert_id:
            q = q.where(NotificationDelivery.alert_id == alert_id)
        rows = db.execute(q.order_by(NotificationDelivery.sent_at.desc()).limit(min(limit, 200))).scalars().all()
        return {"deliveries": [_delivery_out(d) for d in rows]}


# --------------------------------------------------------------------------- /channels
@channels_router.get("", summary="The user's channel connections + which kinds need linking")
async def list_channels(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = {
            c.channel: c for c in db.execute(
                select(ChannelConnection).where(ChannelConnection.user_email == user.email)
            ).scalars().all()
        }
        out = []
        for kind in channels_mod.SUPPORTED:
            c = rows.get(kind)
            out.append({
                "channel": kind,
                "connected": bool(c and json.loads(c.config or "{}")),
                "verified": bool(c and c.verified),
                "id": c.id if c else None,
            })
        return {"channels": out}


@channels_router.post("", summary="Link / update a channel connection (BYO token/webhook)")
async def upsert_channel(body: ChannelIn, user: User = Depends(current_user)) -> dict:
    if body.channel not in channels_mod.SUPPORTED:
        raise HTTPException(422, f"Unsupported channel. Use one of {channels_mod.SUPPORTED}.")
    with SessionLocal() as db:
        c = db.execute(
            select(ChannelConnection).where(
                ChannelConnection.user_email == user.email, ChannelConnection.channel == body.channel)
        ).scalars().first()
        if c is None:
            c = ChannelConnection(user_email=user.email, channel=body.channel)
            db.add(c)
        c.config = json.dumps(body.config, ensure_ascii=False)
        c.verified = False  # re-verify after a credential change
        db.commit()
        return {"id": c.id, "channel": c.channel, "connected": bool(body.config)}


@channels_router.post("/{channel}/verify", summary="Verify a channel by sending a test message")
async def verify_channel(channel: str, user: User = Depends(current_user)) -> dict:
    if channel not in channels_mod.SUPPORTED:
        raise HTTPException(422, "Unsupported channel.")
    with SessionLocal() as db:
        c = db.execute(
            select(ChannelConnection).where(
                ChannelConnection.user_email == user.email, ChannelConnection.channel == channel)
        ).scalars().first()
        if c is None:
            raise HTTPException(404, "Channel not connected.")
        payload = {"title": "🔔 ValueGraph 연결 확인", "body": "이 채널로 알림을 받을 수 있어요.",
                   "source": "ValueGraph", "as_of": datetime.utcnow().date().isoformat(), "deeplink": "/"}
        status = channels_mod.send(channel, json.loads(c.config or "{}"), payload)
        c.verified = status in ("sent", "simulated")
        db.commit()
        return {"channel": channel, "status": status, "verified": c.verified}
