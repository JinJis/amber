"""ASK-6 — the 물어보기 entry feed: background NEWS questions + on-demand per-ticker questions.

Two halves, two rhythms:

* **News refresher** — one asyncio loop (same pattern as ``scheduler.py``) ticking every
  ``ask_feed_refresh_seconds`` (10 min). Each tick refreshes ONE scope — ``news_feed`` — by
  calling agent-engine ``POST /agent/ask-feed`` with the previous ``signature``: agent-engine
  gathers the freshest US/KR market headlines through the gateway, picks the important news,
  and only spends a Gemini call when the headlines actually changed. The entry screen then
  reads the cards from cache. (The old per-ticker background sweep over the union of ALL
  watched tickers is gone — it made a newly added ticker wait behind N serial gathers.)

* **On-demand ticker questions** — ``GET /ask-feed/ticker`` is called when the user taps a
  watchlist ticker on the entry screen: serve the cached pool when it's fresher than
  ``ask_feed_ticker_ttl_seconds``, else generate 3~5 curated cards right now through agent-engine
  (signature-gated, so unchanged data never spends an LLM call) and cache per scope
  (``ticker:{MKT}:{TKR}`` — shared by every user tapping the same ticker).

* **Read path** — ``GET /ask-feed`` assembles the caller's screen in one DB read: the
  watchlist tickers (name + groups, no cards) + the news_feed cards.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import ServiceDep, current_user
from studioapi.models import AskFeedCache, User, Watchlist, WatchlistItem

log = logging.getLogger("studioapi.askfeed")

router = APIRouter(tags=["ask-feed"])

_NEWS_SCOPE = "news_feed"


def _scope_key(market: str, ticker: str) -> str:
    return f"ticker:{(market or 'US').upper()}:{ticker}"


def _any_api_key(db: Session) -> str | None:
    return db.scalar(select(User.api_key).where(User.api_key.is_not(None)).limit(1))


def _payload_of(row: AskFeedCache | None) -> dict:
    if row is None:
        return {}
    try:
        return json.loads(row.payload)
    except (TypeError, ValueError):
        return {}


async def _refresh_scope(client: httpx.AsyncClient, db: Session, *, scope: str,
                         api_key: str | None, body: dict, timeout: float) -> bool:
    """Call agent-engine for one scope and upsert the cache. True when new cards landed.
    ``unchanged`` (same data signature) bumps ``generated_at`` — the cards are re-confirmed
    fresh without an LLM spend. Failure keeps the previous generation (honesty rule)."""
    if not api_key:
        return False
    row = db.get(AskFeedCache, scope)
    body["prev_signature"] = row.signature if row else None
    try:
        r = await client.post(f"{settings.agent_engine_url}/agent/ask-feed",
                              json=body, headers={"X-API-KEY": api_key}, timeout=timeout)
        r.raise_for_status()
        out = r.json()
    except Exception as exc:  # noqa: BLE001 — one dead scope never stalls the caller
        log.warning("ask-feed refresh failed for %s: %s", scope, exc)
        return False
    if out.get("unchanged"):
        if row is not None:  # data didn't move → same cards, re-confirmed now
            row.generated_at = datetime.utcnow()
            db.commit()
        return False
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
    """One refresher pass — the single global news_feed scope."""
    async with httpx.AsyncClient() as client:
        with SessionLocal() as db:
            key = _any_api_key(db)
            if not key:
                return {"scopes": 0, "refreshed": 0}
            ok = await _refresh_scope(client, db, scope=_NEWS_SCOPE, api_key=key,
                                      body={"scope": _NEWS_SCOPE, "limit": 6},
                                      timeout=settings.ask_feed_generate_timeout_seconds)
    return {"scopes": 1, "refreshed": 1 if ok else 0}


async def _loop() -> None:
    while True:
        try:
            out = await refresh_once()
            if out["refreshed"]:
                log.info("ask-feed news_feed refreshed")
        except Exception:
            log.exception("ask-feed refresh tick failed")
        await asyncio.sleep(settings.ask_feed_refresh_seconds)


def start(task_holder: list) -> None:
    if not settings.ask_feed_enabled:
        log.info("ask-feed refresher disabled")
        return
    task_holder.append(asyncio.create_task(_loop()))
    log.info("ask-feed news refresher started (every %ss)", settings.ask_feed_refresh_seconds)


# read-through 콜드스타트 가드: 캐시가 없거나 오래됐을 때 접속이 갱신을 킥한다.
# single-flight(_kick_task) + 최소 간격(_kick_min_gap) — 갱신이 계속 실패해도(키 부재 등)
# 접속마다 agent-engine을 두드리지 않는다.
_kick_task: asyncio.Task | None = None
_kick_at: datetime | None = None
_KICK_MIN_GAP = timedelta(seconds=60)


@router.get("/ask-feed", summary="ASK-6: the 물어보기 entry feed (one DB read, zero LLM)")
async def get_ask_feed(user: User = Depends(current_user)) -> dict:
    """Assemble the caller's 물어보기 entry screen from cache — one DB read, zero LLM calls.
    Tickers come WITHOUT cards; tapping one calls ``GET /ask-feed/ticker`` on demand.
    Read-through: a missing/stale Macro Trends cache fires ONE background refresh (never
    blocks the response) — the first visitor after a cold start populates the section."""
    global _kick_task, _kick_at
    with SessionLocal() as db:
        out = _assemble(db, user.email)
        row = db.get(AskFeedCache, _NEWS_SCOPE)
    stale_after = timedelta(seconds=settings.ask_feed_refresh_seconds * 2)
    stale = row is None or not row.generated_at or datetime.utcnow() - row.generated_at > stale_after
    idle = _kick_task is None or _kick_task.done()
    cooled = _kick_at is None or datetime.utcnow() - _kick_at > _KICK_MIN_GAP
    if stale and idle and cooled:
        # refresh_once opens its own httpx client + DB session → safe to fire-and-forget.
        _kick_at = datetime.utcnow()
        _kick_task = asyncio.create_task(refresh_once())
    return out


@router.post("/ask-feed/refresh", dependencies=[ServiceDep],
             summary="Macro Trends 수동 갱신 (admin ops) — refresh_once를 즉시 1회 실행")
async def ask_feed_refresh() -> dict:
    """The admin console's '지금 갱신 ▶' — runs one refresher pass right now (signature-gated:
    unchanged headlines/indicators spend no LLM call) and reports the cache state."""
    out = await refresh_once()
    with SessionLocal() as db:
        p = _payload_of(db.get(AskFeedCache, _NEWS_SCOPE))
    return {**out, "generated_at": p.get("generated_at"), "cards": len(p.get("cards") or [])}


def _assemble(db: Session, email: str) -> dict:
    # Each ticker carries the watchlist GROUP(s) it belongs to — the entry screen filters by group
    # (관심그룹). A ticker in two groups appears under both filters.
    rows = db.execute(
        select(Watchlist.name, WatchlistItem.market, WatchlistItem.ticker, WatchlistItem.name)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_email == email)
        .order_by(Watchlist.name, WatchlistItem.market, WatchlistItem.ticker)
    ).all()
    groups: list[str] = []
    by_ticker: dict[str, dict] = {}
    for group, market, ticker, name in rows:
        if group not in groups:
            groups.append(group)
        key = _scope_key(market, ticker)
        entry = by_ticker.setdefault(key, {"market": (market or "US").upper(), "ticker": ticker,
                                           "name": name or ticker, "groups": []})
        if group not in entry["groups"]:
            entry["groups"].append(group)

    news = _payload_of(db.get(AskFeedCache, _NEWS_SCOPE))
    return {
        "groups": groups,
        "tickers": list(by_ticker.values()),
        "news_feed": news.get("cards") or [],
        "news_generated_at": news.get("generated_at"),
    }


@router.get("/ask-feed/ticker", summary="ASK-6: on-demand deep-dive questions for ONE ticker")
async def get_ticker_feed(market: str, ticker: str, name: str | None = None,
                          user: User = Depends(current_user)) -> dict:
    """The user tapped a watchlist ticker: serve the cached pool when fresh, else gather that
    ticker's latest records + one two-stage Gemini pass right now (소스별 후보 → 3~5개
    큐레이션, ASK-9). Cache is per TICKER —
    shared by every user. Never fabricates: generation failure returns the stale pool if one
    exists, else an empty list the UI draws as an honest gap."""
    scope = _scope_key(market, ticker)
    ttl = timedelta(seconds=settings.ask_feed_ticker_ttl_seconds)
    with SessionLocal() as db:
        row = db.get(AskFeedCache, scope)
        if row is not None and row.generated_at and datetime.utcnow() - row.generated_at < ttl:
            p = _payload_of(row)
            return {"cards": (p.get("cards") or [])[:5], "generated_at": p.get("generated_at"),
                    "cached": True}
        async with httpx.AsyncClient() as client:
            await _refresh_scope(client, db, scope=scope, api_key=user.api_key,
                                 body={"scope": "ticker", "market": (market or "US").upper(),
                                       "ticker": ticker, "name": name or ticker, "limit": 5},
                                 timeout=settings.ask_feed_generate_timeout_seconds)
        p = _payload_of(db.get(AskFeedCache, scope))
        return {"cards": (p.get("cards") or [])[:5], "generated_at": p.get("generated_at"),
                "cached": False}


# --- ONB-LIVE: 온보딩 쇼케이스 캐시 (하루 1회 갱신, read-through) ------------------------------
_ONB_SCOPE = "onboarding_showcase"
_ONB_TTL = timedelta(hours=24)
_onb_task: asyncio.Task | None = None


async def refresh_onboarding_once() -> dict:
    """agent-engine의 라이브 쇼케이스를 받아 캐시(스코프 onboarding_showcase)에 저장."""
    async with httpx.AsyncClient(timeout=120) as client:
        with SessionLocal() as db:
            key = _any_api_key(db)
            if not key:
                return {"refreshed": False}
            try:
                r = await client.post(f"{settings.agent_engine_url}/agent/onboarding-showcase",
                                      headers={"X-API-KEY": key})
                out = r.json() if r.status_code == 200 else {}
            except Exception as exc:  # noqa: BLE001
                log.warning("onboarding showcase refresh failed: %s", exc)
                return {"refreshed": False}
            if not out.get("cards"):
                return {"refreshed": False}   # 실패/빈 응답 → 이전 캐시 유지(정직)
            row = db.get(AskFeedCache, _ONB_SCOPE) or AskFeedCache(scope=_ONB_SCOPE)
            row.payload = json.dumps(out, ensure_ascii=False)
            row.signature = out.get("signature")
            row.generated_at = datetime.utcnow()
            db.merge(row)
            db.commit()
            return {"refreshed": True, "cards": len(out.get("cards") or [])}


@router.get("/ask-feed/onboarding", summary="ONB-LIVE: 온보딩 라이브 쇼케이스 (캐시, 일 1회)")
async def get_onboarding_showcase(user: User = Depends(current_user)) -> dict:
    """캐시만 즉시 반환; 없거나 24h 지났으면 백그라운드로 1회 갱신 킥(응답은 블로킹 없음)."""
    global _onb_task
    with SessionLocal() as db:
        row = db.get(AskFeedCache, _ONB_SCOPE)
    payload = _payload_of(row)
    stale = row is None or row.generated_at is None or \
        (datetime.utcnow() - row.generated_at) > _ONB_TTL
    if stale and (_onb_task is None or _onb_task.done()):
        _onb_task = asyncio.create_task(refresh_onboarding_once())
    return payload or {}
