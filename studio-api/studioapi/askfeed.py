"""ASK-5 — the 물어보기 entry feed: pre-generated per-ticker questions + global Hot Trend.

Two halves:

* **Refresher** — one asyncio loop (same pattern as ``scheduler.py``) ticking every
  ``ask_feed_refresh_seconds`` (5 min). Each tick collects the UNION of watched tickers across
  all users, and for each scope (``ticker:{MKT}:{TKR}`` + the global ``hot_trend``) calls
  agent-engine ``POST /agent/ask-feed`` with the previous ``signature`` — agent-engine gathers
  the scope's records through the gateway and only spends a Gemini call when the data actually
  changed. Results are upserted into ``ask_feed_cache``. Generation is per TICKER (shared by
  every watcher), never per user — cost scales with the union, not users × tickers.

* **Read path** — ``GET /ask-feed`` assembles the caller's screen from cache in one DB read:
  my watchlist tickers' card pools + hot trend. Scopes not generated yet return as
  ``pending`` tickers (the UI draws a "준비 중" skeleton — a gap, never fabricated content).

The refresher authenticates to agent-engine with a watcher's tenant key (first watcher of the
scope; hot_trend uses the first user) — the single-tenant deployment this targets makes the
metering attribution question moot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user
from studioapi.models import AskFeedCache, User, Watchlist, WatchlistItem

log = logging.getLogger("studioapi.askfeed")

router = APIRouter(tags=["ask-feed"])

_TIMEOUT = 90.0  # one scope's gather+synth can take a while; the loop is serial by design


def _scope_key(market: str, ticker: str) -> str:
    return f"ticker:{(market or 'US').upper()}:{ticker}"


def _watched_scopes(db: Session) -> list[dict]:
    """The union of watched tickers across ALL users → [{scope, market, ticker, name, api_key}].
    The api_key is the first watcher's (used to call agent-engine through the gateway)."""
    rows = db.execute(
        select(WatchlistItem.market, WatchlistItem.ticker, WatchlistItem.name, User.api_key)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .join(User, Watchlist.user_email == User.email)
        .order_by(WatchlistItem.market, WatchlistItem.ticker)
    ).all()
    out: dict[str, dict] = {}
    for market, ticker, name, api_key in rows:
        key = _scope_key(market, ticker)
        if key not in out:
            out[key] = {"scope": key, "market": (market or "US").upper(), "ticker": ticker,
                        "name": name or ticker, "api_key": api_key}
    return list(out.values())


def _any_api_key(db: Session) -> str | None:
    return db.scalar(select(User.api_key).where(User.api_key.is_not(None)).limit(1))


async def _refresh_scope(client: httpx.AsyncClient, db: Session, *, scope: str,
                         api_key: str | None, body: dict) -> bool:
    """Call agent-engine for one scope and upsert the cache. True when new cards landed."""
    if not api_key:
        return False
    row = db.get(AskFeedCache, scope)
    body["prev_signature"] = row.signature if row else None
    try:
        r = await client.post(f"{settings.agent_engine_url}/agent/ask-feed",
                              json=body, headers={"X-API-KEY": api_key}, timeout=_TIMEOUT)
        r.raise_for_status()
        out = r.json()
    except Exception as exc:  # noqa: BLE001 — one dead scope never stalls the loop
        log.warning("ask-feed refresh failed for %s: %s", scope, exc)
        return False
    if out.get("unchanged"):
        return False  # records didn't move → previous cards still stand
    cards = out.get("cards") or []
    if not cards:
        return False  # synthesis unavailable → keep the previous generation (honesty rule)
    if row is None:
        row = AskFeedCache(scope=scope)
        db.add(row)
    row.payload = json.dumps({"cards": cards, "generated_at": out.get("generated_at")},
                             ensure_ascii=False)
    row.signature = out.get("signature")
    row.generated_at = datetime.utcnow()
    db.commit()
    return True


async def refresh_once() -> dict:
    """One refresher pass over every watched scope + hot_trend. Serial on purpose — a tick is
    background work; spreading N gathers over the tick beats hammering the gateway at once."""
    refreshed, total = 0, 0
    async with httpx.AsyncClient() as client:
        with SessionLocal() as db:
            scopes = _watched_scopes(db)
            hot_key = _any_api_key(db)
            for s in scopes:
                total += 1
                if await _refresh_scope(client, db, scope=s["scope"], api_key=s["api_key"],
                                        body={"scope": "ticker", "market": s["market"],
                                              "ticker": s["ticker"], "name": s["name"]}):
                    refreshed += 1
            if hot_key:
                total += 1
                if await _refresh_scope(client, db, scope="hot_trend", api_key=hot_key,
                                        body={"scope": "hot_trend", "limit": 6}):
                    refreshed += 1
    return {"scopes": total, "refreshed": refreshed}


async def _loop() -> None:
    while True:
        try:
            out = await refresh_once()
            if out["refreshed"]:
                log.info("ask-feed refreshed %(refreshed)d/%(scopes)d scope(s)", out)
        except Exception:
            log.exception("ask-feed refresh tick failed")
        await asyncio.sleep(settings.ask_feed_refresh_seconds)


def start(task_holder: list) -> None:
    if not settings.ask_feed_enabled:
        log.info("ask-feed refresher disabled")
        return
    task_holder.append(asyncio.create_task(_loop()))
    log.info("ask-feed refresher started (every %ss)", settings.ask_feed_refresh_seconds)


@router.get("/ask-feed", summary="ASK-5: the 물어보기 entry feed (pre-generated, one DB read)")
async def get_ask_feed(user: User = Depends(current_user)) -> dict:
    """Assemble the caller's 물어보기 entry screen from cache — one DB read, zero LLM calls.
    Tickers whose pool isn't generated yet are listed in ``pending`` (drawn as skeletons)."""
    with SessionLocal() as db:
        return _assemble(db, user.email)


def _assemble(db: Session, email: str) -> dict:
    items = db.execute(
        select(WatchlistItem.market, WatchlistItem.ticker, WatchlistItem.name)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_email == email)
        .order_by(WatchlistItem.market, WatchlistItem.ticker)
    ).all()
    seen: set[str] = set()
    tickers, pending = [], []
    for market, ticker, name in items:
        key = _scope_key(market, ticker)
        if key in seen:
            continue
        seen.add(key)
        row = db.get(AskFeedCache, key)
        if row is None:
            pending.append({"market": market, "ticker": ticker, "name": name or ticker})
            continue
        payload = json.loads(row.payload)
        tickers.append({"market": market, "ticker": ticker, "name": name or ticker,
                        "cards": payload.get("cards") or [],
                        "generated_at": payload.get("generated_at")})
    hot = db.get(AskFeedCache, "hot_trend")
    hot_payload = json.loads(hot.payload) if hot else None
    return {
        "tickers": tickers,
        "pending": pending,
        "hot_trend": (hot_payload or {}).get("cards") or [],
        "hot_trend_generated_at": (hot_payload or {}).get("generated_at"),
    }
