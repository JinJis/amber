"""ENT — cockpit entry data: the market pulse strip + the user's watch tickers with day moves.

Thin gateway proxies (invariant #2: entitled + metered via the user's tenant key), with a short
per-user TTL cache so the empty-state hero never hammers upstreams on every focus/refresh.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import User, Watchlist, WatchlistItem

router = APIRouter(prefix="/market", tags=["Market (cockpit)"], dependencies=[Depends(require_service)])

_PULSE_TTL = 60.0
_WATCH_TTL = 120.0
_pulse_cache: dict[str, tuple[float, dict]] = {}
_watch_cache: dict[str, tuple[float, dict]] = {}

# the strip shows a curated FEW, not every group — orientation, not a dashboard
_STRIP_LABELS = ("S&P 500", "나스닥", "KOSPI", "USD/KRW", "미 10년")  # 지수·환율·금리 — 방향 잡는 다섯


@router.get("/pulse", summary="시장 오프닝 스트립 — 주요 지수·환율·VIX (60s 캐시)")
async def market_pulse(user: User = Depends(current_user)) -> dict:
    now = time.monotonic()
    hit = _pulse_cache.get(user.email)
    if hit and now - hit[0] < _PULSE_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{settings.control_plane_url}/market/asset-classes",
                                    headers={"X-API-KEY": user.api_key})
        data = resp.json() if resp.status_code == 200 else {}
    except httpx.HTTPError:
        data = {}
    members = [m for g in (data.get("groups") or []) for m in (g.get("members") or [])]
    by_label = {m.get("label"): m for m in members}
    items = [by_label[lbl] for lbl in _STRIP_LABELS if lbl in by_label]
    out = {"items": items, "source": data.get("source"), "as_of": data.get("as_of")}
    if items:
        _pulse_cache[user.email] = (now, out)
    return out


async def _snapshot(client: httpx.AsyncClient, api_key: str, ticker: str, market: str) -> dict | None:
    try:
        resp = await client.get(f"{settings.control_plane_url}/prices/snapshot",
                                params={"ticker": ticker, "market": market},
                                headers={"X-API-KEY": api_key})
        if resp.status_code != 200:
            return None
        snap = (resp.json() or {}).get("snapshot") or {}
        if snap.get("price") is None:
            return None
        return {"ticker": ticker, "market": market, "price": snap.get("price"),
                "change_percent": snap.get("day_change_percent"), "source": snap.get("source")}
    except httpx.HTTPError:
        return None


@router.get("/watch", summary="내 종목 스트립 — 관심그룹 티커 + 등락 (120s 캐시)")
async def market_watch(user: User = Depends(current_user)) -> dict:
    now = time.monotonic()
    hit = _watch_cache.get(user.email)
    if hit and now - hit[0] < _WATCH_TTL:
        return hit[1]
    with SessionLocal() as db:
        rows = db.execute(
            select(WatchlistItem.ticker, WatchlistItem.market, WatchlistItem.name)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
            .where(Watchlist.user_email == user.email)).all()
    seen: dict[str, dict] = {}
    for tk, mk, name in rows:
        base = (tk or "").split(".")[0]
        if base and base not in seen:
            seen[base] = {"ticker": base, "market": (mk or "US").upper(), "name": name}
    picks = list(seen.values())[:8]
    ticks: list[dict] = []
    if picks:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            snaps = await asyncio.gather(*[
                _snapshot(client, user.api_key, p["ticker"], p["market"]) for p in picks])
        for p, s in zip(picks, snaps):
            ticks.append({**p, **(s or {})})
    out = {"tickers": ticks}
    if ticks:
        _watch_cache[user.email] = (now, out)
    return out
