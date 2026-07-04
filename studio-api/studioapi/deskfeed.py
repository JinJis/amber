"""M-DESK (DK-3) — desk-feed caching + user context assembly.

``GET /desk-feed`` serves the turn-zero briefing: within the TTL the stored payload comes
straight from the cache (no agent-engine call); otherwise the user's context (watchlists,
recent conversation titles, last visit) is assembled here and agent-engine composes fresh
cards through the gateway with the user's tenant key. Any watchlist change deletes the cache
row (the feed is watchlist-derived — an edit makes it wrong, not merely stale).

Serving the feed also advances ``User.last_seen_at``; the *previous* value rides along as
``since`` so agent-engine can scope "since your last visit" windows (새로 들어온 공시).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import Conversation, DeskFeedCache, User, Watchlist, WatchlistItem

log = logging.getLogger("studioapi.deskfeed")

router = APIRouter(tags=["Desk"], dependencies=[Depends(require_service)])


def invalidate_desk_feed(email: str) -> None:
    """Drop the user's cached feed (called on every watchlist mutation). Best-effort."""
    try:
        with SessionLocal() as db:
            row = db.get(DeskFeedCache, email)
            if row is not None:
                db.delete(row)
                db.commit()
    except Exception:  # noqa: BLE001 — cache invalidation must never fail a watchlist edit
        log.exception("desk-feed invalidate failed for %s", email)


def _user_context(db, email: str) -> dict:
    wls = db.execute(
        select(Watchlist).where(Watchlist.user_email == email).order_by(Watchlist.created_at.asc())
    ).scalars().all()
    watchlists = []
    markets: set[str] = set()
    for wl in wls:
        items = db.execute(
            select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
        ).scalars().all()
        watchlists.append({"name": wl.name, "items": [
            {"market": it.market, "ticker": it.ticker, "name": it.name} for it in items]})
        markets.update(it.market for it in items)
    convs = db.execute(
        select(Conversation).where(Conversation.user_email == email)
        .order_by(Conversation.created_at.desc()).limit(5)
    ).scalars().all()
    return {
        "watchlists": watchlists,
        "markets": sorted(markets) or None,
        "recent_conversations": [c.title for c in convs if c.title] or None,
    }


async def _generate(user: User, since: datetime | None) -> dict | None:
    """One agent-engine composition call. (feed, context_nonce) — feed None on failure/timeout
    (caller degrades). The nonce (hash of the watchlist context the feed was generated FROM) lets
    the caller skip the cache write if the watchlists changed mid-generation (IMP-10 race)."""
    with SessionLocal() as db:
        payload = _user_context(db, user.email)
    nonce = _ctx_nonce(payload)
    payload["since"] = since.isoformat() if since else None
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            r = await asyncio.wait_for(   # IMP-10: hard overall cap — a slow engine can't hold the request
                client.post(f"{settings.agent_engine_url}/agent/desk-feed",
                            json=payload, headers={"X-API-KEY": user.api_key}),
                timeout=settings.desk_feed_generate_timeout_seconds)
            r.raise_for_status()
            return r.json(), nonce
    except Exception as exc:  # noqa: BLE001 — a down/slow engine degrades the feed, never 500s it
        log.warning("desk-feed generation failed for %s: %s", user.email, exc)
        return None, nonce


def _ctx_nonce(ctx: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(ctx.get("watchlists"), sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:16]


@router.get("/desk-feed", summary="M-DESK: the turn-zero briefing (cached per user)")
async def get_desk_feed(user: User = Depends(current_user)) -> dict:
    now = datetime.utcnow()
    with SessionLocal() as db:
        row = db.get(DeskFeedCache, user.email)
        if row is not None and row.generated_at and \
                now - row.generated_at < timedelta(seconds=settings.desk_feed_ttl_seconds):
            return {**json.loads(row.payload), "cached": True}
        prev_seen = db.get(User, user.email).last_seen_at

    fresh, nonce = await _generate(user, prev_seen)

    with SessionLocal() as db:
        # advance the visit marker regardless of generation outcome — the user WAS here
        u = db.get(User, user.email)
        if u is not None:
            u.last_seen_at = now
        if fresh is not None:
            # IMP-10: if the watchlists changed WHILE we generated, this feed is already wrong —
            # serve it (best effort) but don't poison the cache with it.
            if _ctx_nonce(_user_context(db, user.email)) == nonce:
                row = db.get(DeskFeedCache, user.email)
                if row is None:
                    row = DeskFeedCache(user_email=user.email, payload="{}")
                    db.add(row)
                row.payload = json.dumps(fresh, ensure_ascii=False)
                row.generated_at = now
            else:
                log.info("desk-feed cache write skipped (watchlists changed mid-generation) %s", user.email)
        db.commit()

    if fresh is not None:
        return {**fresh, "cached": False}
    # engine unreachable and nothing cached → an explicitly degraded, never-fabricated feed
    with SessionLocal() as db:
        row = db.get(DeskFeedCache, user.email)
    if row is not None:  # serve the stale copy rather than nothing
        return {**json.loads(row.payload), "cached": True, "stale": True}
    return {"cards": [], "generated_at": None, "used_tools": [], "degraded": True}
