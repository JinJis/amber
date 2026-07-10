"""Chat: persist the turn, drive the agent-engine SSE generation in the BACKGROUND, and tail
it to the browser.

The generation runs as a server-side ``Run`` (see ``runs.py``) so it keeps going even if the
browser leaves; the HTTP response just tails the run's event buffer. Re-entering the
conversation resumes the same tail. Each event is also accumulated so the assistant message is
persisted when the run completes.
"""

from __future__ import annotations

import asyncio
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


def _merge_plan_spec(spec: dict | None, user: User, degraded: bool) -> dict | None:
    """PLAN-3: 유저의 플랜 티어(모델·스텝·서브에이전트·커넥터 셋)를 이 턴의 AgentSpec에 병합.
    유저 에이전트의 자체 제한은 '좁히기만' 가능 — 플랜이 스텝 8이면 에이전트가 12를 원해도 8.
    하드 방어는 게이트웨이 엔타이틀먼트(403); 여기서의 allowed_tools 교집합은 플래너가 403날
    툴에 스텝을 낭비하지 않게 하는 최적화다."""
    from studioapi import plans as plans_mod

    ov = plans_mod.spec_overrides(plans_mod.plan_of(user), degraded)
    if not ov:
        return spec
    out = dict(spec or {})
    if ov.get("synthesis_model"):
        out["synthesis_model"] = ov["synthesis_model"]
    if ov.get("max_steps") is not None:
        cur = out.get("max_steps")
        out["max_steps"] = min(int(cur), ov["max_steps"]) if cur else ov["max_steps"]
    if ov.get("max_subagents") is not None:
        out["max_subagents"] = ov["max_subagents"]
    conns = ov.get("allowed_connectors")
    if conns:
        allowed = out.get("allowed_tools")
        if allowed:
            # 에이전트 제한과 플랜 셋의 교집합(툴 이름은 connector__tool, 항목이 커넥터 id일 수도).
            filtered = [t for t in allowed if str(t).split("__")[0] in conns]
            # 에이전트가 전부 플랜 밖 툴만 요구하면 → 플랜 셋으로 폴백(턴이 죽는 것보다 정직한 강등).
            out["allowed_tools"] = filtered or list(conns)
        else:
            out["allowed_tools"] = list(conns)
    return out


def prepare_turn(
    user: User, conversation_id: str | None, messages: list[dict], agent_id: str | None,
    degraded: bool = False,
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
    merged = _merge_plan_spec(payload.get("spec"), user, degraded)  # PLAN-3: 플랜 티어 병합
    if merged is not None:
        payload["spec"] = merged
    return conv_id, payload


async def drive_run(run: Run, user: User, conv_id: str, payload: dict) -> None:
    """Background driver: stream the agent-engine SSE, append every event to the run buffer
    (independent of any client), and persist the assistant message when done. Not tied to the
    browser connection — leaving the chat doesn't stop this."""
    text_parts: list[str] = []
    citations: list[dict] = []
    artifacts: list[dict] = []
    suggestions: list = []
    hook: str | None = None
    audit: dict | None = None
    cancelled = False
    try:
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
                elif ev.get("type") == "suggestions":
                    # 더 파고들기 chips ride their own event (before `done`) — capture them so the
                    # row survives leaving + reopening the conversation, not just the live stream.
                    suggestions = ev.get("items") or suggestions
                elif ev.get("type") == "done":
                    citations = ev.get("citations") or citations
                    artifacts = ev.get("artifacts") or artifacts
                    hook = ev.get("hook") or hook
                    audit = ev.get("audit") or audit
    except asyncio.CancelledError:
        # UXQ-2: 사용자가 중지 — CancelledError를 여기서 흡수하면 이후 저장은 정상 실행.
        # 지금까지의 부분 답변을 그대로 영속(유실·날조 없음)하고 중지 표식을 남긴다.
        cancelled = True

    if cancelled and text_parts:
        text_parts.append("\n\n*⏹ 여기서 중지했어요*")

    with SessionLocal() as db:
        db.add(Message(
            conversation_id=conv_id, role="assistant",
            content="".join(text_parts), hook=(hook or None),
            citations=json.dumps(citations, ensure_ascii=False),
            artifacts=json.dumps(artifacts, ensure_ascii=False),
            audit=json.dumps(audit, ensure_ascii=False) if audit else None,
            suggestions=json.dumps(suggestions, ensure_ascii=False) if suggestions else None,
        ))
        db.commit()
    # final marker so a tail learns the (already-known) conversation id and can stop
    if cancelled:
        await manager.append(run, {"type": "done", "citations": citations, "artifacts": artifacts,
                                   "refused": False, "stopped": True})
    await manager.append(run, {"type": "conversation", "id": conv_id})


async def sse_tail(run: Run, from_index: int = 0) -> AsyncIterator[str]:
    """Serialize a run's events (from ``from_index``) as an SSE byte stream. Cancelling this
    (client disconnect) leaves the underlying run generating in the background."""
    async for ev in manager.tail(run, from_index):
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def start_turn(user: User, conversation_id: str | None, messages: list[dict], agent_id: str | None) -> Run:
    """Public entry: PLAN-2 quota gate → prepare the turn → launch its background run.

    한도 초과(blocked)면 실제 턴을 시작하지 않는다 — 유저 메시지도 저장하지 않고, 합성 Run이
    quota SSE 이벤트 + done만 흘린다(HTTP 4xx가 아니라 SSE라 스트림 배관·재개 tail이 그대로).
    pro fair-use 초과(degraded)는 진행하되 quota 알림 이벤트를 먼저 흘리고, prepare_turn이
    스펙을 flash 티어로 강등한다(PLAN-3)."""
    from studioapi import quotas

    verdict = quotas.check_and_consume(user, conversation_id)
    if verdict.mode == "blocked":
        async def _quota_driver(run: Run) -> None:
            await manager.append(run, verdict.event())
            await manager.append(run, {"type": "done", "citations": [], "artifacts": [],
                                       "refused": False, "quota": True})
        return manager.start(conversation_id or f"quota_{user.email}", _quota_driver)

    conv_id, payload = prepare_turn(user, conversation_id, messages, agent_id,
                                    degraded=(verdict.mode == "degraded"))
    notice = verdict.event() if verdict.mode == "degraded" else None

    async def _drive(run: Run) -> None:
        if notice:
            await manager.append(run, notice)
        await drive_run(run, user, conv_id, payload)

    return manager.start(conv_id, _drive)
