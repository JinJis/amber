"""@handle resolution (U1-03).

A user types ``@반도체바스켓`` in chat (or an analyst's system prompt references a
group). Before the turn reaches the agent engine we expand each ``@handle`` to the
concrete companies in that user's watchlist, so the planner calls tools for exactly
those tickers. The user's original text (with the bare @handle) is what we persist
and show; only the copy sent to the agent is expanded. Unknown/empty handles get a
graceful inline note instead of silently vanishing.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from studioapi.models import Watchlist, WatchlistItem

# A generic @token — ASCII word chars / Hangul. Used only to flag UNKNOWN handles; KNOWN groups
# are matched by their EXACT stored name (which may contain '·', spaces, etc. — e.g. "AI·빅테크",
# "에너지·원자재" — that a character-class regex would truncate, the original expansion bug).
_TOKEN = r"[0-9A-Za-z_가-힣]+"
_HANDLE_RE = re.compile(r"@(" + _TOKEN + r")")


def expand_text(db, user_email: str, text: str | None) -> str:
    """Replace each ``@handle`` in ``text`` with ``@handle (handle = company list)``. Known groups
    are matched by their exact name (longest first, so '@AI·빅테크' beats a stray '@AI'); an unknown
    handle gets an inline note instead of silently vanishing."""
    if not text or "@" not in text:
        return text or ""

    wls = db.execute(
        select(Watchlist).where(Watchlist.user_email == user_email)
    ).scalars().all()
    by_name = {w.name: w for w in wls}

    if not by_name:
        return _HANDLE_RE.sub(lambda m: f"{m.group(0)} (알 수 없는 관심 그룹)", text)

    # one pass: try an exact known name (longest first) at each '@'; else a generic token → note.
    alt = "|".join(re.escape(n) for n in sorted(by_name, key=len, reverse=True))
    # the known name must end on a token boundary so '@반도체' doesn't fire inside '@반도체바스켓'
    pat = re.compile(r"@(?:(" + alt + r")(?![0-9A-Za-z_가-힣])|(" + _TOKEN + r"))")

    def _sub(match: re.Match) -> str:
        known = match.group(1)
        if not known:
            return f"@{match.group(2)} (알 수 없는 관심 그룹)"
        wl = by_name[known]
        items = db.execute(
            select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
            .order_by(WatchlistItem.added_at.asc())
        ).scalars().all()
        if not items:
            return f"@{known} (빈 그룹)"
        listed = ", ".join(f"{it.name or it.ticker} [{it.ticker}, {it.market}]" for it in items)
        return f"@{known} ({known} = {listed})"

    return pat.sub(_sub, text)


def resolve_messages(db, user_email: str, messages: list[dict]) -> list[dict]:
    """Return a copy of ``messages`` with @handles in user turns expanded."""
    out: list[dict] = []
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            out.append({**m, "content": expand_text(db, user_email, m["content"])})
        else:
            out.append(m)
    return out
