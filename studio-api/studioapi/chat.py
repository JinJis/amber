"""Chat: persist the turn, drive the agent-engine SSE generation in the BACKGROUND, and tail
it to the browser.

The generation runs as a server-side ``Run`` (see ``runs.py``) so it keeps going even if the
browser leaves; the HTTP response just tails the run's event buffer. Re-entering the
conversation resumes the same tail. Each event is also accumulated so the assistant message is
persisted when the run completes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from studioapi.agents import agent_to_spec, load_agent
from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.groups import expand_text, resolve_messages
from studioapi.models import Conversation, Message, User
from studioapi.runs import Run, manager


def _title(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            return (m.get("content") or "New chat")[:80]
    return "New chat"


def prepare_turn(
    user: User, conversation_id: str | None, messages: list[dict], agent_id: str | None,
) -> tuple[str, dict]:
    """Resolve the agent → spec, ensure the conversation, persist the user message, and build
    the agent-engine payload. Runs synchronously BEFORE the background run so the conversation
    id exists immediately (returned in the run's first event)."""
    spec: dict | None = None
    with SessionLocal() as db:
        if agent_id:
            agent = load_agent(db, agent_id, user.email)
            if agent is not None:
                spec = agent_to_spec(agent)
                if spec.get("system"):  # expand any @handle the analyst's prompt references
                    spec["system"] = expand_text(db, user.email, spec["system"])
            else:
                agent_id = None  # unknown/forbidden agent -> default behaviour
        resolved_messages = resolve_messages(db, user.email, messages)

    with SessionLocal() as db:
        if conversation_id and db.get(Conversation, conversation_id):
            conv_id = conversation_id
        else:
            conv = Conversation(user_email=user.email, title=_title(messages), agent_id=agent_id)
            db.add(conv)
            db.commit()
            conv_id = conv.id
        last = messages[-1] if messages else {"role": "user", "content": ""}
        db.add(Message(conversation_id=conv_id, role=last.get("role", "user"), content=last.get("content", "")))
        db.commit()

    payload: dict = {"messages": resolved_messages}
    if spec is not None:
        payload["spec"] = spec
    return conv_id, payload


async def drive_run(run: Run, user: User, conv_id: str, payload: dict) -> None:
    """Background driver: stream the agent-engine SSE, append every event to the run buffer
    (independent of any client), and persist the assistant message when done. Not tied to the
    browser connection — leaving the chat doesn't stop this."""
    text_parts: list[str] = []
    citations: list[dict] = []
    artifacts: list[dict] = []
    hook: str | None = None
    audit: dict | None = None
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST", f"{settings.agent_engine_url}/agent/chat",
            json=payload, headers={"X-API-KEY": user.api_key},
        ) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                await manager.append(run, ev)
                if ev.get("type") == "token":
                    text_parts.append(ev.get("text", ""))
                elif ev.get("type") == "done":
                    citations = ev.get("citations") or citations
                    artifacts = ev.get("artifacts") or artifacts
                    hook = ev.get("hook") or hook
                    audit = ev.get("audit") or audit

    with SessionLocal() as db:
        db.add(Message(
            conversation_id=conv_id, role="assistant",
            content="".join(text_parts), hook=(hook or None),
            citations=json.dumps(citations, ensure_ascii=False),
            artifacts=json.dumps(artifacts, ensure_ascii=False),
            audit=json.dumps(audit, ensure_ascii=False) if audit else None,
        ))
        db.commit()
    # final marker so a tail learns the (already-known) conversation id and can stop
    await manager.append(run, {"type": "conversation", "id": conv_id})


async def sse_tail(run: Run, from_index: int = 0) -> AsyncIterator[str]:
    """Serialize a run's events (from ``from_index``) as an SSE byte stream. Cancelling this
    (client disconnect) leaves the underlying run generating in the background."""
    async for ev in manager.tail(run, from_index):
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def start_turn(user: User, conversation_id: str | None, messages: list[dict], agent_id: str | None) -> Run:
    """Public entry: prepare the turn and launch its background run. Returns the Run to tail."""
    conv_id, payload = prepare_turn(user, conversation_id, messages, agent_id)
    return manager.start(conv_id, lambda run: drive_run(run, user, conv_id, payload))
