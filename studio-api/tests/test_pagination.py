"""HI-10 / SC-3.1: /conversations and /messages return bounded, most-recent-first pages with a
cursor for load-more / load-earlier — never the user's entire history (or a whole 100s-of-KB thread)
in one scan. A conversation under the default is returned in full (backward-compatible)."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from studioapi.config import settings
from studioapi.db import SessionLocal, init_db
from studioapi.main import app
from studioapi.models import Conversation, Message, User

client = TestClient(app)
SVC = "dev-service-token"


def setup_module(_module):
    init_db()


def _hdr(email: str) -> dict:
    return {"X-Service-Token": SVC, "X-User-Email": email}


def _mk_user(email: str) -> None:
    with SessionLocal() as db:
        if db.get(User, email) is None:
            # stamp the reconcile version so current_actor's ensure_user short-circuits (offline)
            db.merge(User(email=email, tenant_id="t", project_id="p", api_key="k",
                          connectors_reconciled_ver=settings.connectors_reconcile_ver))
            db.commit()


def _mk_conv(email: str, n_msgs: int = 0, title: str = "c", created_at: datetime | None = None) -> str:
    with SessionLocal() as db:
        c = Conversation(user_email=email, title=title)
        if created_at is not None:   # distinct timestamps → deterministic newest-first (id is a uuid)
            c.created_at = created_at
        db.add(c)
        db.commit()
        cid = c.id
        for i in range(n_msgs):
            db.add(Message(conversation_id=cid, role="user" if i % 2 == 0 else "assistant", content=f"m{i}"))
        db.commit()
    return cid


def test_conversations_are_paged_newest_first():
    email = "pag@u.com"
    _mk_user(email)
    with SessionLocal() as db:
        db.query(Conversation).filter(Conversation.user_email == email).delete()
        db.commit()
    base = datetime(2026, 1, 1, 0, 0, 0)
    for i in range(5):
        _mk_conv(email, title=f"c{i}", created_at=base + timedelta(seconds=i))   # c0..c4, c4 newest

    r = client.get("/conversations?limit=2", headers=_hdr(email)).json()
    assert len(r["conversations"]) == 2 and r["has_more"] is True and r["next_offset"] == 2
    assert r["conversations"][0]["title"] == "c4"   # most-recent first

    last = client.get("/conversations?limit=2&offset=4", headers=_hdr(email)).json()
    assert len(last["conversations"]) == 1 and last["has_more"] is False


def test_messages_recent_slice_and_load_earlier():
    email = "pagm@u.com"
    _mk_user(email)
    cid = _mk_conv(email, n_msgs=5)   # m0..m4 chronological

    page = client.get(f"/conversations/{cid}/messages?limit=2", headers=_hdr(email)).json()
    assert [m["content"] for m in page["messages"]] == ["m3", "m4"]   # most-recent, chronological
    assert page["has_more"] is True and page["oldest_id"] is not None

    earlier = client.get(f"/conversations/{cid}/messages?limit=2&before={page['oldest_id']}",
                         headers=_hdr(email)).json()
    assert [m["content"] for m in earlier["messages"]] == ["m1", "m2"]
    assert earlier["has_more"] is True   # m0 still older


def test_short_conversation_returns_all_by_default():
    email = "pags@u.com"
    _mk_user(email)
    cid = _mk_conv(email, n_msgs=4)
    r = client.get(f"/conversations/{cid}/messages", headers=_hdr(email)).json()
    assert [m["content"] for m in r["messages"]] == ["m0", "m1", "m2", "m3"]   # backward-compatible
    assert r["has_more"] is False
