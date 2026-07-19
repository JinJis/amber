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
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import ServiceDep, current_user
from studioapi.models import AskFeedCache, User, Watchlist, WatchlistItem

log = logging.getLogger("studioapi.askfeed")

router = APIRouter(tags=["ask-feed"])

_NEWS_SCOPE = "news_feed"

# 홈 마키 섹션(시장 전체 공유 캐시). 뉴스보다 느리게 도는 스코프들 — 각 에이전트 스코프로
# 생성해 스코프명으로 캐시. 프레젠테이션(제목·이모지·카피)은 프런트가 스코프로 매핑한다.
# 순서 = 화면 노출 순서(Macro Trends 다음 어닝 → 거장·수급 → 히스토리).
_SECTION_SCOPES: list[dict] = [
    {"scope": "earnings_radar", "limit": 14},
    {"scope": "guru_flows", "limit": 14},
    {"scope": "history_lab", "limit": 14},
]


def _scope_key(market: str, ticker: str) -> str:
    return f"ticker:{(market or 'US').upper()}:{ticker}"


def _any_api_key(db: Session) -> str | None:
    return db.scalar(select(User.api_key).where(User.api_key.is_not(None)).limit(1))


def _bg_api_key(db: Session) -> str | None:
    """ME-5: the key that background/system feed generation meters against — prefer the dedicated system
    tenant key (stable, not tied to any user's lifecycle) and only fall back to an arbitrary user's key
    while the system project is still provisioning."""
    from studioapi.provision import system_api_key_cached
    return system_api_key_cached() or _any_api_key(db)


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
    try:
        db.commit()
    except IntegrityError:   # ME-1: another replica inserted this scope PK first → converge onto it
        db.rollback()
        existing = db.get(AskFeedCache, scope)
        if existing is not None:
            existing.payload, existing.signature = row.payload, row.signature
            existing.generated_at = row.generated_at
            db.commit()
    return True


async def refresh_once() -> dict:
    """One refresher pass — the single global news_feed scope."""
    async with httpx.AsyncClient() as client:
        with SessionLocal() as db:
            # CR-3: only ONE replica generates the global news_feed per interval — otherwise every
            # replica runs the same two-stage Gemini generation (duplicate LLM spend). No-op on SQLite.
            is_pg = db.bind.dialect.name == "postgresql"
            if is_pg and not bool(db.execute(func.pg_try_advisory_lock(0x76674132)).scalar()):  # 'vgA2'
                return {"scopes": 0, "refreshed": 0}
            try:
                key = _bg_api_key(db)
                if not key:
                    return {"scopes": 0, "refreshed": 0}
                # Macro Trends 마키(우→좌 흐름)용 — 분야가 서로 겹치지 않는 심층 질문 20개.
                ok = await _refresh_scope(client, db, scope=_NEWS_SCOPE, api_key=key,
                                          body={"scope": _NEWS_SCOPE, "limit": 20},
                                          timeout=settings.ask_feed_generate_timeout_seconds)
                return {"scopes": 1, "refreshed": 1 if ok else 0}
            finally:
                if is_pg:
                    db.execute(func.pg_advisory_unlock(0x76674132))


async def refresh_sections_once() -> dict:
    """One pass over the market-wide marquee section scopes (어닝·거장·히스토리). Each is a SHARED
    scope (not a per-ticker sweep) and signature-gated, so unchanged data spends no LLM call.
    Refreshed on a slower cadence than news — the read-through kick regenerates a stale section
    when someone visits, and the background loop keeps them warm even without visitors."""
    refreshed = 0
    async with httpx.AsyncClient() as client:
        with SessionLocal() as db:
            # CR-3: only ONE replica generates the shared sections per interval (separate lock
            # id from news_feed's). No-op on SQLite.
            is_pg = db.bind.dialect.name == "postgresql"
            if is_pg and not bool(db.execute(func.pg_try_advisory_lock(0x76674133)).scalar()):  # 'vgA3'
                return {"scopes": 0, "refreshed": 0}
            try:
                key = _bg_api_key(db)
                if not key:
                    return {"scopes": 0, "refreshed": 0}
                for s in _SECTION_SCOPES:
                    ok = await _refresh_scope(client, db, scope=s["scope"], api_key=key,
                                              body={"scope": s["scope"], "limit": s["limit"]},
                                              timeout=settings.ask_feed_generate_timeout_seconds)
                    refreshed += 1 if ok else 0
            finally:
                if is_pg:
                    db.execute(func.pg_advisory_unlock(0x76674133))
    return {"scopes": len(_SECTION_SCOPES), "refreshed": refreshed}


async def _loop() -> None:
    while True:
        try:
            out = await refresh_once()
            if out["refreshed"]:
                log.info("ask-feed news_feed refreshed")
        except Exception:
            log.exception("ask-feed refresh tick failed")
        await asyncio.sleep(settings.ask_feed_refresh_seconds)


async def _sections_loop() -> None:
    while True:
        try:
            out = await refresh_sections_once()
            if out["refreshed"]:
                log.info("ask-feed sections refreshed (%d)", out["refreshed"])
        except Exception:
            log.exception("ask-feed sections tick failed")
        await asyncio.sleep(settings.ask_feed_section_refresh_seconds)


def start(task_holder: list) -> None:
    if not settings.ask_feed_enabled:
        log.info("ask-feed refresher disabled")
        return
    task_holder.append(asyncio.create_task(_loop()))
    task_holder.append(asyncio.create_task(_sections_loop()))
    log.info("ask-feed refreshers started (news %ss · sections %ss)",
             settings.ask_feed_refresh_seconds, settings.ask_feed_section_refresh_seconds)


# read-through 콜드스타트 가드: 캐시가 없거나 오래됐을 때 접속이 갱신을 킥한다.
# single-flight(_kick_task) + 최소 간격(_kick_min_gap) — 갱신이 계속 실패해도(키 부재 등)
# 접속마다 agent-engine을 두드리지 않는다.
_kick_task: asyncio.Task | None = None
_kick_at: datetime | None = None
_KICK_MIN_GAP = timedelta(seconds=60)
# 섹션 스코프 read-through 킥 — 뉴스와 독립된 single-flight + 쿨다운.
_sec_kick_task: asyncio.Task | None = None
_sec_kick_at: datetime | None = None


def _is_stale(row: AskFeedCache | None, ttl_seconds: float) -> bool:
    return (row is None or not row.generated_at
            or datetime.utcnow() - row.generated_at > timedelta(seconds=ttl_seconds))


@router.get("/ask-feed", summary="ASK-6: the 물어보기 entry feed (one DB read, zero LLM)")
async def get_ask_feed(user: User = Depends(current_user)) -> dict:
    """Assemble the caller's 물어보기 entry screen from cache — one DB read, zero LLM calls.
    Tickers come WITHOUT cards; tapping one calls ``GET /ask-feed/ticker`` on demand.
    Read-through: a missing/stale Macro Trends cache fires ONE background news refresh, and a
    stale section cache fires ONE background section refresh — neither blocks the response, so
    the first visitor after a cold start populates the sections for the next visit."""
    global _kick_task, _kick_at, _sec_kick_task, _sec_kick_at
    with SessionLocal() as db:
        out = _assemble(db, user.email)
        news_row = db.get(AskFeedCache, _NEWS_SCOPE)
        sec_rows = [db.get(AskFeedCache, s["scope"]) for s in _SECTION_SCOPES]
    now = datetime.utcnow()
    news_stale = _is_stale(news_row, settings.ask_feed_refresh_seconds * 2)
    if news_stale and (_kick_task is None or _kick_task.done()) \
            and (_kick_at is None or now - _kick_at > _KICK_MIN_GAP):
        # refresh_once opens its own httpx client + DB session → safe to fire-and-forget.
        _kick_at = now
        _kick_task = asyncio.create_task(refresh_once())
    # 섹션은 느린 캐시 — enabled일 때만(테스트 격리: ASK_FEED_ENABLED=false면 킥 안 함).
    sec_stale = any(_is_stale(r, settings.ask_feed_section_refresh_seconds) for r in sec_rows)
    if settings.ask_feed_enabled and sec_stale \
            and (_sec_kick_task is None or _sec_kick_task.done()) \
            and (_sec_kick_at is None or now - _sec_kick_at > _KICK_MIN_GAP):
        _sec_kick_at = now
        _sec_kick_task = asyncio.create_task(refresh_sections_once())
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


@router.post("/ask-feed/refresh-sections", dependencies=[ServiceDep],
             summary="어닝·거장·히스토리 섹션 수동 갱신 (admin ops / eval) — refresh_sections_once 즉시 1회")
async def ask_feed_refresh_sections() -> dict:
    """The 마키 섹션(어닝 레이더·투자거장·수급·히스토리 랩)의 지금 갱신 — 각 스코프를 1회 생성
    (signature-gated). ops 콘솔·eval에서 섹션 캐시를 결정적으로 채우는 훅."""
    out = await refresh_sections_once()
    with SessionLocal() as db:
        counts = {s["scope"]: len(_payload_of(db.get(AskFeedCache, s["scope"])).get("cards") or [])
                  for s in _SECTION_SCOPES}
    return {**out, "cards_by_scope": counts}


def _assemble(db: Session, email: str) -> dict:
    # 1차 depth = 관심 @그룹 (id+name — id는 관심 페이지로의 딥링크에 쓰인다). 빈 그룹도
    # 내려보낸다: 엔트리의 그룹 아코디언이 "방금 만든 그룹"을 그대로 보여주고 종목 추가로
    # 이어줘야 하므로. Each ticker carries the group NAME(s) it belongs to — a ticker in two
    # groups appears under both.
    groups = [{"id": wid, "name": wname} for wid, wname in db.execute(
        select(Watchlist.id, Watchlist.name)
        .where(Watchlist.user_email == email).order_by(Watchlist.name)).all()]
    rows = db.execute(
        select(Watchlist.name, WatchlistItem.market, WatchlistItem.ticker, WatchlistItem.name)
        .join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .where(Watchlist.user_email == email)
        .order_by(Watchlist.name, WatchlistItem.market, WatchlistItem.ticker)
    ).all()
    by_ticker: dict[str, dict] = {}
    for group, market, ticker, name in rows:
        key = _scope_key(market, ticker)
        entry = by_ticker.setdefault(key, {"market": (market or "US").upper(), "ticker": ticker,
                                           "name": name or ticker, "groups": []})
        if group not in entry["groups"]:
            entry["groups"].append(group)

    news = _payload_of(db.get(AskFeedCache, _NEWS_SCOPE))
    # 홈 보드 섹션들 — Macro Trends(news_feed)를 맨 앞에 두고, 시장 전체 섹션 캐시를 순서대로.
    # 카드가 있는 섹션만. 각 카드에 실측 인기 수치(taps, 최근 7일 전 유저 탭)를 달고 핫한
    # 순으로 정렬해 내려보낸다(RC-2) — 제목·이모지·카피는 프런트가 scope로 매핑.
    taps = hot_taps(db)
    news_ranked = rank_cards(news.get("cards") or [], taps)
    sections = [{"scope": _NEWS_SCOPE, "cards": news_ranked,
                 "generated_at": news.get("generated_at")}]
    for s in _SECTION_SCOPES:
        p = _payload_of(db.get(AskFeedCache, s["scope"]))
        cards = p.get("cards") or []
        if cards:
            sections.append({"scope": s["scope"], "cards": rank_cards(cards, taps),
                             "generated_at": p.get("generated_at")})
    return {
        "groups": groups,
        "tickers": list(by_ticker.values()),
        "sections": sections,
        # backward-compat: Macro Trends 카드를 예전 필드로도 노출(구버전 프런트/기존 테스트).
        "news_feed": news_ranked,
        "news_generated_at": news.get("generated_at"),
    }


# ME-1: per-scope single-flight for the on-demand ticker generation. In-proc asyncio.Lock collapses
# concurrent taps on ONE replica to a single two-stage Gemini pass; a cross-node advisory lock stops a
# SECOND replica from generating the same scope at the same time (it serves whatever's cached instead).
_ticker_flight: dict[str, asyncio.Lock] = {}


def _scope_lock_id(scope: str) -> int:
    import hashlib
    return int.from_bytes(hashlib.blake2b(scope.encode(), digest_size=8).digest(), "big", signed=True)


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

    def _serve(cached: bool) -> dict:
        with SessionLocal() as db:
            p = _payload_of(db.get(AskFeedCache, scope))
            taste = recent_tap_kinds(db, user.email)
        return {"cards": rerank_by_taste((p.get("cards") or [])[:5], taste),
                "generated_at": p.get("generated_at"), "cached": cached}

    def _fresh() -> bool:
        with SessionLocal() as db:
            row = db.get(AskFeedCache, scope)
            return bool(row and row.generated_at and datetime.utcnow() - row.generated_at < ttl)

    if _fresh():
        return _serve(cached=True)

    # ME-1: single-flight the generation. In-proc lock collapses concurrent taps on this replica;
    # the double-check inside means only the first tap generates and the rest serve its result.
    lock = _ticker_flight.setdefault(scope, asyncio.Lock())
    async with lock:
        if _fresh():
            return _serve(cached=True)
        with SessionLocal() as db:
            # cross-node: if another replica is already generating this scope, don't duplicate the
            # two-stage Gemini pass — serve whatever's cached (stale/empty) instead.
            is_pg = db.bind.dialect.name == "postgresql"
            got = (not is_pg) or bool(db.execute(func.pg_try_advisory_lock(_scope_lock_id(scope))).scalar())
            try:
                if got:
                    async with httpx.AsyncClient() as client:
                        await _refresh_scope(client, db, scope=scope, api_key=user.api_key,
                                             body={"scope": "ticker", "market": (market or "US").upper(),
                                                   "ticker": ticker, "name": name or ticker, "limit": 5},
                                             timeout=settings.ask_feed_generate_timeout_seconds)
            finally:
                if is_pg and got:
                    db.execute(func.pg_advisory_unlock(_scope_lock_id(scope)))
    return _serve(cached=False)


# --- ONB-LIVE: 온보딩 쇼케이스 캐시 (하루 1회 갱신, read-through) ------------------------------
_ONB_SCOPE = "onboarding_showcase"
_ONB_TTL = timedelta(hours=24)
_onb_task: asyncio.Task | None = None


async def refresh_onboarding_once() -> dict:
    """agent-engine의 라이브 쇼케이스를 받아 캐시(스코프 onboarding_showcase)에 저장."""
    async with httpx.AsyncClient(timeout=120) as client:
        with SessionLocal() as db:
            key = _bg_api_key(db)
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


# --- RC-1: 탭 피드백 루프 ---------------------------------------------------------------------
from pydantic import BaseModel as _BM

from studioapi.models import CardTap


class TapIn(_BM):
    kind: str
    ticker: str | None = None
    question: str | None = None   # RC-2: 카드 단위 인기 집계용 (서버는 해시만 저장)


def _qhash(question: str | None) -> str | None:
    """카드 식별 해시 — 질문 원문은 저장하지 않고 16자 해시만 (인기 집계 키)."""
    import hashlib
    q = (question or "").strip()
    return hashlib.sha1(q.encode()).hexdigest()[:16] if q else None


@router.post("/ask-feed/tap", summary="RC-1: 질문 카드 탭 기록 (개인화 신호)")
async def record_tap(body: TapIn, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        db.add(CardTap(user_email=user.email, kind=body.kind[:32],
                       ticker=(body.ticker or None) and body.ticker[:32],
                       qhash=_qhash(body.question)))
        db.commit()
    return {"ok": True}


def hot_taps(db: Session, days: int = 7) -> dict[str, int]:
    """홈 보드 랭킹 수치 — 최근 N일, 전 유저의 카드별(질문 해시) 탭 수 실측. 날조 없음:
    집계가 0이면 0이고, UI는 0을 숨긴다(순위는 큐레이션 순서가 대신한다)."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(CardTap.qhash, func.count()).where(CardTap.ts >= since, CardTap.qhash.is_not(None))
        .group_by(CardTap.qhash)).all()
    return {qh: int(n) for qh, n in rows}


def rank_cards(cards: list[dict], taps: dict[str, int]) -> list[dict]:
    """카드에 실측 `taps`를 달고 핫한 순으로 정렬 — 동률은 큐레이션 순서(LLM 중요도) 유지."""
    annotated = [{**c, "taps": taps.get(_qhash(c.get("question")) or "", 0)} for c in cards]
    return sorted(annotated, key=lambda c: -c["taps"])


def rerank_by_taste(cards: list[dict], kind_counts: dict[str, int]) -> list[dict]:
    """공유 캐시 풀을 유저의 탭 분포로 안정 재정렬 — 콘텐츠 불변(캐시 오염 없음), 순서만
    유저가 실제 반응한 kind 우선. 동률은 원 큐레이션 순서 유지(결정적)."""
    if not kind_counts:
        return cards
    return sorted(cards, key=lambda c: -kind_counts.get(str(c.get("kind") or ""), 0))


def recent_tap_kinds(db: Session, email: str, days: int = 14, cap: int = 200) -> dict[str, int]:
    """최근 N일 유저가 탭한 카드 kind 분포 — 티커 피드 요청에 개인화 힌트로 동봉."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(select(CardTap.kind).where(
        CardTap.user_email == email, CardTap.ts >= since).limit(cap)).scalars().all()
    out: dict[str, int] = {}
    for k in rows:
        out[k] = out.get(k, 0) + 1
    return out
