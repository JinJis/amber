"""HI-9 follow-up: tailing agent-engine's chat SSE must not DROP a fully-streamed answer when the
upstream stream breaks in a trailing silent phase (a slow post-answer enrichment tail, or a proxy
closing a quiet connection). If the answer already streamed, drive_run persists it + finalizes the
run; only a break with NOTHING streamed is a genuine failure that surfaces the error."""

from __future__ import annotations

import httpx
import pytest

from studioapi import chat
from studioapi.chat import drive_run
from studioapi.db import SessionLocal, init_db
from studioapi.models import Conversation, User
from studioapi.runs import Run


def setup_module(_module):
    init_db()


def _fake_client_factory(lines, exc):
    """A stand-in httpx.AsyncClient whose stream yields `lines` (as aiter_lines would) then raises
    `exc` — simulating an upstream SSE that drops mid/after the answer."""

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_lines(self):
            for ln in lines:
                yield ln
            if exc is not None:
                raise exc

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return _Resp()

    return _Client


def _seed_user_and_conv(email: str) -> str:
    with SessionLocal() as db:
        if not db.get(User, email):
            db.add(User(email=email, project_id="prj_test", api_key="vgk_test"))
        conv = Conversation(user_email=email, title="t")
        db.add(conv)
        db.commit()
        return conv.id


async def test_streamed_answer_survives_a_mid_stream_transport_drop(monkeypatch):
    email = "drop@u.com"
    conv_id = _seed_user_and_conv(email)
    user = User(email=email, project_id="prj_test", api_key="vgk_test")

    lines = [
        'data: {"type":"token","text":"부분 답변입니다."}',
        'data: {"type":"citation","tool":"yahoo__prices","source":"Yahoo Finance","index":1}',
    ]
    monkeypatch.setattr(chat.httpx, "AsyncClient",
                        _fake_client_factory(lines, httpx.ReadTimeout("idle read")))

    run = Run(id="r_drop", conversation_id=conv_id)
    await drive_run(run, user, conv_id, {"messages": [{"role": "user", "content": "q"}]})

    # the assistant message is PERSISTED with the streamed text (not lost to the drop)
    with SessionLocal() as db:
        msgs = [m for m in db.query(chat.Message).filter_by(conversation_id=conv_id).all()]
    asst = [m for m in msgs if m.role == "assistant"]
    assert asst and "부분 답변입니다." in asst[0].content
    # and its SOURCES survive too — the streamed citation is persisted even without a `done`
    # (else the reloaded answer keeps its [n] markers but shows no source cards — invariant #1)
    assert "Yahoo Finance" in (asst[0].citations or "")
    # and the run is finalized with a synthesized `done` so the UI stops spinning
    assert any(e.get("type") == "done" for e in run.events)
    # the drop did NOT surface as a user-facing failure token
    assert not any("문제가 발생했어요" in (e.get("text") or "") for e in run.events)


async def test_drop_with_no_answer_yet_is_a_real_failure(monkeypatch):
    email = "drop2@u.com"
    conv_id = _seed_user_and_conv(email)
    user = User(email=email, project_id="prj_test", api_key="vgk_test")

    # breaks before any token → nothing to salvage → drive_run re-raises for the error path
    monkeypatch.setattr(chat.httpx, "AsyncClient",
                        _fake_client_factory([], httpx.ReadTimeout("idle read")))

    run = Run(id="r_drop2", conversation_id=conv_id)
    with pytest.raises(httpx.TransportError):
        await drive_run(run, user, conv_id, {"messages": [{"role": "user", "content": "q"}]})
