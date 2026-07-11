"""Watchlists / @groups (U1): per-user named groups of companies.

A watchlist's ``name`` is the ``@handle`` the chat composer and the standing-analyst
builder tag (e.g. ``@반도체바스켓``). Companies are added by (market, ticker) — the
same company may live in several groups. All rows are per-user scoped; the web BFF
authenticates with a service token and forwards the user's email.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.deskfeed import invalidate_desk_feed
from studioapi.models import User, Watchlist, WatchlistItem

router = APIRouter(prefix="/watchlists", tags=["Watchlists"], dependencies=[Depends(require_service)])

_MARKETS = {"US", "KR"}


# --- schemas --------------------------------------------------------------
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ItemIn(BaseModel):
    market: str = Field(min_length=1, max_length=8)
    ticker: str = Field(min_length=1, max_length=32)
    name: str | None = None


def _item_out(it: WatchlistItem) -> dict:
    return {"id": it.id, "market": it.market, "ticker": it.ticker, "name": it.name}


def _out(wl: Watchlist, items: list[WatchlistItem]) -> dict:
    return {
        "id": wl.id,
        "name": wl.name,  # also the @handle
        "handle": f"@{wl.name}",
        "count": len(items),
        "items": [_item_out(it) for it in items],
    }


def _load_owned(db, watchlist_id: str, email: str) -> Watchlist:
    wl = db.get(Watchlist, watchlist_id)
    if wl is None or wl.user_email != email:
        raise HTTPException(404, "Watchlist not found.")
    return wl


def _items_of(db, watchlist_id: str) -> list[WatchlistItem]:
    return list(
        db.execute(
            select(WatchlistItem)
            .where(WatchlistItem.watchlist_id == watchlist_id)
            .order_by(WatchlistItem.added_at.asc())
        ).scalars().all()
    )


def _name_taken(db, email: str, name: str, exclude_id: str | None = None) -> bool:
    row = db.execute(
        select(Watchlist).where(Watchlist.user_email == email, Watchlist.name == name)
    ).scalars().first()
    return row is not None and row.id != exclude_id


# --- endpoints ------------------------------------------------------------
@router.get("", summary="List the user's watchlists (with items)")
async def list_watchlists(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Watchlist).where(Watchlist.user_email == user.email)
            .order_by(Watchlist.created_at.asc())
        ).scalars().all()
        return {"watchlists": [_out(wl, _items_of(db, wl.id)) for wl in rows]}


@router.post("", summary="Create a watchlist (@group)")
async def create_watchlist(body: WatchlistIn, user: User = Depends(current_user)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank.")
    with SessionLocal() as db:
        if _name_taken(db, user.email, name):
            raise HTTPException(409, f"You already have a group named '{name}'.")
        wl = Watchlist(user_email=user.email, name=name)
        db.add(wl)
        db.commit()
        invalidate_desk_feed(user.email)  # M-DESK: the feed is watchlist-derived
        return _out(wl, [])


@router.get("/{watchlist_id}", summary="Get one watchlist (own only)")
async def get_watchlist(watchlist_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        wl = _load_owned(db, watchlist_id, user.email)
        return _out(wl, _items_of(db, wl.id))


@router.patch("/{watchlist_id}", summary="Rename a watchlist (changes its @handle)")
async def rename_watchlist(watchlist_id: str, body: WatchlistIn, user: User = Depends(current_user)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank.")
    with SessionLocal() as db:
        wl = _load_owned(db, watchlist_id, user.email)
        if _name_taken(db, user.email, name, exclude_id=wl.id):
            raise HTTPException(409, f"You already have a group named '{name}'.")
        wl.name = name
        db.commit()
        invalidate_desk_feed(user.email)
        return _out(wl, _items_of(db, wl.id))


@router.delete("/{watchlist_id}", summary="Delete a watchlist (and its items)")
async def delete_watchlist(watchlist_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        wl = _load_owned(db, watchlist_id, user.email)
        for it in _items_of(db, wl.id):
            db.delete(it)
        db.delete(wl)
        db.commit()
        invalidate_desk_feed(user.email)
        return {"deleted": watchlist_id}


@router.post("/{watchlist_id}/items", summary="Add a company (idempotent on market+ticker)")
async def add_item(watchlist_id: str, body: ItemIn, user: User = Depends(current_user)) -> dict:
    market = body.market.strip().upper()
    ticker = body.ticker.strip()
    if market not in _MARKETS:
        raise HTTPException(422, "market must be 'US' or 'KR'.")
    with SessionLocal() as db:
        wl = _load_owned(db, watchlist_id, user.email)
        existing = db.execute(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == wl.id,
                WatchlistItem.market == market,
                WatchlistItem.ticker == ticker,
            )
        ).scalars().first()
        if existing is not None:  # idempotent — re-adding returns the same item
            return _item_out(existing)
        it = WatchlistItem(watchlist_id=wl.id, market=market, ticker=ticker, name=body.name)
        db.add(it)
        db.commit()
        invalidate_desk_feed(user.email)
        return _item_out(it)


@router.delete("/{watchlist_id}/items/{item_id}", summary="Remove a company from the watchlist")
async def remove_item(watchlist_id: str, item_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        _load_owned(db, watchlist_id, user.email)  # ownership check
        it = db.get(WatchlistItem, item_id)
        if it is None or it.watchlist_id != watchlist_id:
            raise HTTPException(404, "Item not found in this watchlist.")
        db.delete(it)
        db.commit()
        invalidate_desk_feed(user.email)
        return {"deleted": item_id}
