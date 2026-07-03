"""Shared tenant-scoped ownership checks (RF-11).

Every CRUD router repeated the same guard: load a row by primary key, then 404 unless it belongs to
the calling user. These two helpers centralize that so the check (and its semantics) live in one place.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select


def check_owned(entity, email: str, detail: str, *, allow_global: bool = False):
    """Return ``entity`` iff it exists and belongs to ``email``; otherwise raise 404.

    With ``allow_global=True`` a global/template row (``user_email is None``) is also visible to
    everyone — the read-path semantics for shared templates."""
    if entity is None or (entity.user_email != email and not (allow_global and entity.user_email is None)):
        raise HTTPException(status_code=404, detail=detail)
    return entity


def get_owned(db, model, ident, email: str, detail: str, *, allow_global: bool = False):
    """Load ``model`` by primary key and ownership-check it — the common load + guard + 404 pattern."""
    return check_owned(db.get(model, ident), email, detail, allow_global=allow_global)


def idempotent_clone(db, model, source, user_email: str, *, fields, overrides: dict | None = None):
    """Clone a community/template ``source`` row into ``user_email``'s library, idempotent on
    ``source_id`` — the prompt-import pattern CLAUDE.md says to reuse for analyst/agent cloning.

    If the user already has a copy of this source (same ``source_id``), return ``(existing, False)``
    with no write. Otherwise build a copy carrying ``user_email`` + ``source_id`` + the named
    ``fields`` copied from ``source`` (plus any ``overrides``), add it to the session, and return
    ``(copy, True)``. The CALLER commits — so it owns the transaction and serializes the result."""
    existing = db.execute(
        select(model).where(model.user_email == user_email, model.source_id == source.id)
    ).scalars().first()
    if existing is not None:
        return existing, False
    copy = model(user_email=user_email, source_id=source.id,
                 **{f: getattr(source, f) for f in fields}, **(overrides or {}))
    db.add(copy)
    return copy, True
