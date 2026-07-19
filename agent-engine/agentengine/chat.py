"""Streaming, multi-turn chat over the agent loop.

``stream_chat`` yields typed events for an SSE response:
  {"type":"thinking","phase":..,"text":..}  live reasoning (analyze→fetch→found→synthesize)
  {"type":"token","text":...}        incremental answer text
  {"type":"tool","name":..,"args":..} a tool the agent is calling
  {"type":"tool_result","status":..,"connector":..}
  {"type":"citation","tool":..,"source":..,"url":..}
  {"type":"done","citations":[...],"artifacts":[...],"refused":bool}

Planner-agnostic: the stub and gemini planners both flow through here (the final
answer is streamed in chunks). Tool calls go through the gateway with the tenant
key, so entitlement + metering apply to chat too.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import AsyncIterator

from agentengine import guardrails
from agentengine.agent import (
    _artifacts, _citations, _NARRATIVE_GUIDE, _NEWS_BRIEF_GUIDE, _VALUE_CHAIN_GUIDE, analyze_task,
    anchor_markers, call_sig, fallback_answer, filter_tools, has_anchors,
    number_sources, refine_evidence,
)
from agentengine.client import PlatformClient
from agentengine.config import settings
from agentengine.evidence import evidence_url_for_answer
from agentengine.models import AgentSpec
from agentengine.planner import get_planner
from agentengine.provenance import _canonical_provenance, _market_from_link, _market_hint

logger = logging.getLogger(__name__)


async def sse_heartbeat(events: AsyncIterator[dict], interval: float) -> AsyncIterator[str]:
    """Serialize `events` as SSE lines, injecting a keepalive COMMENT whenever `interval` seconds
    pass with nothing to send.

    A chat turn goes silent for long stretches — a slow/retrying Gemini call, or the post-answer
    tail (evidence passages, live-pulse follow-ups, share hook) that chains several LLM/gateway
    awaits with no event between them. During that silence the downstream consumer (studio-api)
    times its between-chunks read out and aborts the stream mid-work: the ReadTimeout that surfaces
    as "답변 생성 중 문제". The comment (`: hb`) carries no `data:` line, so studio-api skips it and
    it never reaches the browser — it only keeps the socket from idling past `interval`.

    The heartbeat races the *next* event against the timeout WITHOUT cancelling it, so a keepalive
    is emitted even while the generator is blocked deep inside a single long `await`."""
    if interval and interval > 0:
        ait = events.__aiter__()
        pending = asyncio.ensure_future(ait.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait({pending}, timeout=interval)
                if not done:
                    yield ": hb\n\n"        # keepalive comment — no `data:` line, invisible to the UI
                    continue
                try:
                    ev = pending.result()
                except StopAsyncIteration:
                    return
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                pending = asyncio.ensure_future(ait.__anext__())
        finally:
            # Deterministic cleanup on client disconnect / GeneratorExit. If a next-event fetch is
            # still in flight (the common case — we're heartbeating BECAUSE stream_chat is parked in a
            # long await), cancel it and AWAIT the cancel so stream_chat's own finally unwinds — i.e.
            # its httpx stream to the gateway closes — before we return. (A bare `events.aclose()`
            # here would raise "async generator is already running" because that in-flight __anext__
            # still holds the generator; an un-awaited cancel would only be reaped a loop-turn later,
            # which a concurrent loop teardown could drop.) With no fetch in flight (stream exhausted
            # or errored) aclose() is the correct, and safe, way to close it.
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending
            else:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    with contextlib.suppress(Exception):   # noqa: BLE001 — best-effort close
                        await aclose()
    else:
        async for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return messages[-1].get("content", "") if messages else ""


async def _followups_event(task: str, final_text: str, citations: list[dict],
                           bk: str | None, conversation: list | None = None,
                           audit: dict | None = None, artifacts: list[dict] | None = None,
                           client: PlatformClient | None = None, tools: dict | None = None,
                           cite_ctx: list | None = None) -> dict | None:
    """Build the 'suggestions' SSE event for a finished answer. Always non-empty when there's an
    answer — suggest_followups uses the deep LLM on gemini and a deterministic capability-aware
    fallback otherwise — so the chip row renders on EVERY answer path (conceptual + data). The
    recent transcript is passed so the chips DEEPEN the thread instead of restarting it. When the
    turn had a gateway client, a LIVE PULSE (오늘 가격·최신 공시·헤드라인·수급/어닝·RAG 원문) is
    fetched RIGHT NOW so the chips can point at real, current facts the answer didn't contain."""
    if not (final_text or "").strip():
        return None
    from agentengine.agent import _intake_context, suggest_followups
    tickers = sorted({c.get("ticker") for c in citations if c.get("ticker")})
    kinds = sorted({c.get("kind") for c in citations if c.get("kind")})
    ctx_bits = []
    if tickers:
        ctx_bits.append("다룬 종목: " + ", ".join(tickers[:5]))
    if kinds:
        ctx_bits.append("사용한 데이터: " + ", ".join(kinds))
    # RC-2: 이번 답변의 검증된 수치·그린 차트 종류를 문맥으로 — 후속질문이 "그 12%가 왜"처럼
    # 구체 수치를 파고들게 한다 (검증 통과분만 — 날조 수치로 유도하지 않음).
    if audit and audit.get("ledger"):
        nums = [str(r.get("raw")) for r in audit["ledger"] if r.get("supported")][:6]
        if nums:
            ctx_bits.append("검증된 핵심 수치: " + ", ".join(nums))
    if artifacts:
        akinds = sorted({str(a.get("kind")) for a in artifacts if a.get("kind")})[:4]
        if akinds:
            ctx_bits.append("그린 차트·표: " + ", ".join(akinds))
    # recent prior turns → the suggester builds on the conversation (심화), not generic chips
    transcript = _intake_context(conversation) if conversation else ""
    conv_block = f"최근 대화:\n{transcript}\n\n" if transcript and transcript != "(no prior turns)" else ""
    eff_backend = bk or settings.llm_backend
    # 실시간 펄스: 이 턴이 다룬 종목의 "지금"(가격·새 공시·헤드라인·수급/어닝·보유 원문)을 방금
    # 게이트웨이로 조회해 제안 프롬프트에 넣는다 — 칩이 답변에 없던 새 사실을 지목할 수 있게.
    # (ticker, market)은 이 턴의 실제 툴 호출 인자에서 복원; best-effort — 실패해도 칩은 뜬다.
    live = ""
    if client is not None and tools and eff_backend == "gemini":
        from agentengine.enrichment import live_pulse
        targets: list[tuple[str, str | None]] = []
        seen_t: set[str] = set()
        for cit, tool, args, data in (cite_ctx or []):
            tk = (args or {}).get("ticker")
            if tk and tk not in seen_t:
                seen_t.add(tk)
                targets.append((tk, (args or {}).get("market") or _market_hint(tool, data)))
        for tk in tickers:  # citations without a recorded call (e.g. resolved upstream)
            if tk and tk not in seen_t:
                seen_t.add(tk)
                targets.append((tk, None))
        live = await live_pulse(client.call_tool, tools, targets, task)
    logger.info("chat: requesting follow-up chips (backend=%s, answer_len=%d, tickers=%s, kinds=%s, live_len=%d)",
                eff_backend, len(final_text), tickers, kinds, len(live))
    sugg = await suggest_followups(task, final_text, settings.model, bk,
                                   context=" · ".join(ctx_bits) or None, tickers=tickers, kinds=kinds,
                                   conversation=conv_block, live=live)
    logger.info("chat: follow-up chips → %d suggestion(s)", len(sugg))
    return {"type": "suggestions", "items": sugg} if sugg else None


def _chunks(text: str, size: int = 28) -> list[str]:
    """Slice text into fixed-size spans for the fallback/stub stream. CHARACTER-based so it
    preserves newlines + markdown structure verbatim (word-splitting collapsed `\\n` → broke
    headings/lists). Real Gemini answers stream token-by-token via the planner instead."""
    text = text or ""
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]


async def stream_chat(messages: list[dict], api_key: str | None, spec: AgentSpec | None = None) -> AsyncIterator[dict]:
    task = _last_user(messages)
    system = spec.system if spec else None
    bk = spec.backend if spec else None

    # PH-THINK: the first-pass intake is ONE LLM call that both GUARDRAILS the request
    # (judging intent in context — no keyword rules, invariant #9) AND plans it (budget +
    # a short plan shown as live thinking). If it's a forecast/advice/target request, refuse
    # here at the boundary — before we ever touch the data plane.
    yield {"type": "thinking", "phase": "analyze", "text": "요청을 분석하고 있어요…"}
    # pass the conversation so the intake resolves follow-up references (e.g. '배당률은?' inherits
    # the company named in an earlier turn) instead of clarifying or losing the subject.
    intake = await analyze_task(task, bk, conversation=messages)
    if intake.restricted:
        for ch in _chunks(guardrails.REFUSAL):
            yield {"type": "token", "text": ch}
        yield {"type": "done", "citations": [], "artifacts": [], "refused": True}
        return

    # CLARIFY-WITH-OPTIONS (Claude-Code-style plan/ask): a broad/ambiguous request → offer the
    # user concrete choices to scope the work instead of guessing. We stop here; the web renders
    # the options as chips, and the user's pick composes a refined follow-up turn.
    if intake.clarify and intake.options:
        prompt = intake.clarify_prompt or "무엇을 도와드릴까요? 아래에서 골라 주세요."
        for ch in _chunks(prompt):
            yield {"type": "token", "text": ch}
        yield {"type": "clarify", "prompt": prompt, "options": intake.options, "multi": intake.multi}
        yield {"type": "done", "citations": [], "artifacts": [], "refused": False, "clarify": True}
        return

    max_steps = spec.max_steps if (spec and spec.max_steps) else intake.steps
    plan = intake.plan
    if plan:
        yield {"type": "thinking", "phase": "plan", "text": f"계획: {plan}"}
        # the plan guides tool selection + synthesis (quality), without hardcoding logic.
        system = ((system or "") + f"\n\n[연구 계획] {plan}").strip()

    # CE-4: a holistic company-story request → steer the synthesis into a structured, sourced
    # 종목 내러티브 (the gather stays the normal multi-tool flow; we parse a narrative card after).
    if intake.narrative:
        yield {"type": "thinking", "phase": "plan", "text": "종목 내러티브(관전 포인트)로 정리할게요…"}
        system = ((system or "") + _NARRATIVE_GUIDE).strip()
    # CE-10: a news-briefing request → steer synthesis into a structured, sourced news narrative.
    if intake.news_brief:
        yield {"type": "thinking", "phase": "plan", "text": "최신 뉴스 브리핑으로 정리할게요…"}
        system = ((system or "") + _NEWS_BRIEF_GUIDE).strip()
    # CE-14: a value-chain request → structured supply-chain map (derived from filings/news).
    if intake.value_chain:
        yield {"type": "thinking", "phase": "plan", "text": "밸류체인(공급망 구조)으로 정리할게요…"}
        system = ((system or "") + _VALUE_CHAIN_GUIDE).strip()
    planner = get_planner(bk)
    # PLAN-3/CR-6: plan-tier synthesis model (free/guest → flash), from the spec studio-api merged
    # per turn. Passed as a call ARGUMENT to every synthesis call — the planner is a process-wide
    # singleton, so storing it on the instance raced across concurrent users' tiers (a free turn
    # could downgrade a concurrent pro turn's synthesis).
    synth_model = getattr(spec, "synthesis_model", None) if spec else None
    # Forward the override only when set — None means "use the configured default synthesis model",
    # so passing it is redundant. Spread into each synthesis call via **synth_kw.
    synth_kw = {"synthesis_model": synth_model} if synth_model else {}
    from agentengine.planner import resolve_ticker
    history: list = []
    citations: list[dict] = []
    cite_ctx: list[tuple[dict, dict, dict, object]] = []  # (citation, tool, args, data) → post-answer 재앵커/패시지
    probes: list[dict] = []   # SA-1: periodic sources this turn → the standing-question offer
    artifacts: list[dict] = []
    art_objs: list = []          # the Artifact objects → enrich with chart markers post-loop
    seen_artifacts: set = set()
    # dedup with MERGE: a duplicate (same source+url, or same url under another label) fills the
    # survivor's missing fields (evidence anchor, table, computation) instead of being dropped —
    # two tools citing one document must yield one card carrying ALL the evidence.
    from agentengine.citations import citation_key, merge_citation
    seen_cites: dict = {}      # citation_key → the surviving citation dict
    seen_cite_urls: dict = {}  # url → the surviving citation dict (cross-label collapse)

    def _add_citation(cit: dict) -> bool:
        """Register a streamed citation; False → a survivor absorbed it (don't emit/append)."""
        key = citation_key(cit.get("source"), cit.get("url"), cit.get("tool"))
        survivor = seen_cites.get(key) or (seen_cite_urls.get(cit.get("url")) if cit.get("url") else None)
        if survivor is not None:
            merge_citation(survivor, cit)
            return False
        seen_cites[key] = cit
        if cit.get("url"):
            seen_cite_urls[cit["url"]] = cit
        return True

    answered = False
    refined = False              # run the verify/refine pass once, just before synthesis
    final_text = ""
    last_sig = None

    async def _maybe_refine():
        # PH-THINK verify pass: ONE review that grounds the synthesis AND scores each
        # source's confidence (shown on the source card) — the trust brand, not fine print.
        nonlocal refined, system
        if refined or not citations:
            return None
        refined = True
        note, scores = await refine_evidence(task, citations, settings.reasoning_model, bk)
        if note:
            system = ((system or "") + f"\n\n[검증 메모] {note}").strip()
        for c in citations:
            sc = scores.get(c.get("index"))
            if sc:
                c["confidence"] = sc["confidence"]
                c["confidence_why"] = sc.get("why")
        return note

    def _figures_block() -> str:
        # The charts/tables THIS turn rendered, numbered by their position in art_objs — the
        # synthesis model places each inline with {{figure:N}} (the UI swaps it for the card).
        lines = []
        for i, a in enumerate(art_objs, 1):
            bits = [f"{{{{figure:{i}}}}}", getattr(a, "kind", None) or "chart",
                    getattr(a, "title", None) or ""]
            if getattr(a, "source", None):
                bits.append(f"출처 {a.source}")
            lines.append(" · ".join(b for b in bits if b))
        return "\n".join(lines)

    async def _synthesize(tools_arg, history_arg, system_arg):
        # REAL streaming of the final answer (gemini) — yields token events as the responder
        # generates them. Fallback/stub path char-chunks a one-shot result (newline-preserving).
        nonlocal final_text
        sources = number_sources(citations)
        figures = _figures_block()
        if hasattr(planner, "stream_final") and (bk or settings.llm_backend) == "gemini":
            got = False
            # ANCHOR-NORM: 묶음 인용([1,2]·[3-5])을 스트림 중에 개별 [n]으로 정규화 — 클라
            # 링크화·used 마킹·감사 스팬이 전부 단일 마커 규약 위에서 동작한다.
            from agentengine.anchors import AnchorStream
            ns = AnchorStream()
            async for delta in planner.stream_final(task, tools_arg, history_arg, system_arg,
                                                     conversation=messages, sources=sources,
                                                     figures=figures, **synth_kw):
                got = True
                out = ns.feed(delta)
                if out:
                    final_text += out
                    yield {"type": "token", "text": out}
            tail = ns.flush()
            if tail:
                final_text += tail
                yield {"type": "token", "text": tail}
            if not got:
                for ch in _chunks(fallback_answer(citations)):
                    final_text += ch
                    yield {"type": "token", "text": ch}
        else:
            dec = await planner.plan(task, tools_arg, history_arg, system_arg,
                                     conversation=messages, force_final=True, sources=sources,
                                     figures=figures, **synth_kw)
            from agentengine.anchors import normalize_anchor_groups
            for ch in _chunks(normalize_anchor_groups(dec.final or fallback_answer(citations))):
                final_text += ch
                yield {"type": "token", "text": ch}

    async def _emit_synthesis(tools_arg, history_arg, system_arg, note="답변을 작성하는 중…"):
        # the "답변을 작성하는 중…" thinking line + the streamed answer — the exact pair the
        # loop emits at every finalize site (the A2A combiner passes its own `note`).
        yield {"type": "thinking", "phase": "synthesize", "text": note}
        async for ev in _synthesize(tools_arg, history_arg, system_arg):
            yield ev

    async def _emit_verify():
        # the cross-check thinking line shown just before synthesis at the two sites that
        # refine — emitted ONLY there (other finalize sites deliberately skip it).
        if citations and (bk or settings.llm_backend) == "gemini" and not refined:
            yield {"type": "thinking", "phase": "verify", "text": "근거를 교차검증하는 중…"}

    # Conceptual / definitional question → answer from expertise, no tools, streamed.
    if not intake.needs_data:
        async for ev in _emit_synthesis({}, [], system):
            yield ev
        sev = await _followups_event(task, final_text, [], bk, conversation=messages)
        if sev:
            yield sev
        yield {"type": "done", "citations": [], "artifacts": [], "refused": False, "used": []}
        return

    client = PlatformClient(api_key)
    try:
        tools = await client.fetch_tools()
    except Exception:
        yield {"type": "token", "text": "데이터 플랫폼에 지금 연결할 수 없어요."}
        yield {"type": "done", "citations": [], "artifacts": [], "refused": False}
        return
    if spec and spec.allowed_tools:
        tools = filter_tools(tools, spec.allowed_tools)

    try:
        async def _plan_batch(force_final: bool) -> list:
            kw = dict(conversation=messages, force_final=force_final, sources=number_sources(citations))
            if force_final and synth_model:  # CR-6: synthesis step → forward the per-turn tier
                kw["synthesis_model"] = synth_model
            if hasattr(planner, "plan_batch"):
                return await planner.plan_batch(task, tools, history, system, **kw)
            return [await planner.plan(task, tools, history, system, **kw)]

        # A2A: a complex, multi-facet request → dispatch focused sub-agents in PARALLEL (each
        # gathers its own evidence), stream their live cards, then COMBINE into one cited answer.
        # PLAN-3: the plan tier caps the fan-out (guest/free → 0/1 = no decomposition; the
        # request still runs as one normal loop, so the answer never disappears — just narrower).
        max_sub = spec.max_subagents if (spec and spec.max_subagents is not None) else None
        if intake.subtasks and len(intake.subtasks) >= 2 and (max_sub is None or max_sub >= 2):
            from agentengine.orchestrator import run_subagent, SUBAGENT_BUDGET
            subs = intake.subtasks if max_sub is None else intake.subtasks[:max_sub]
            yield {"type": "thinking", "phase": "plan",
                   "text": f"분석을 {len(subs)}개 작업으로 나눠 동시에 진행할게요…"}
            for i, st in enumerate(subs):
                yield {"type": "subagent", "id": i, "title": st["title"], "status": "running"}

            async def _one(idx, st):
                res = await run_subagent(st["title"], st["question"], api_key, tools, bk, SUBAGENT_BUDGET)
                return idx, res

            futs = [asyncio.ensure_future(_one(i, st)) for i, st in enumerate(subs)]
            results: list = [None] * len(subs)
            for fut in asyncio.as_completed(futs):
                i, res = await fut
                results[i] = res
                yield {"type": "subagent", "id": i, "title": res.title, "status": "done",
                       "sources": len({(c.source, c.url) for c in res.citations}), "steps": res.steps}
                # stream this facet's evidence NOW (global de-dup + 1-based [n]) — the 근거 패널
                # fills live as each sub-agent lands, not in one dump after the slowest one.
                for c in res.citations:
                    cit = c.model_dump()
                    if not _add_citation(cit):
                        continue
                    cit["index"] = len(citations) + 1
                    citations.append(cit)
                    yield {"type": "citation", **cit}
                for a in res.artifacts:
                    if a.title in seen_artifacts:
                        continue
                    seen_artifacts.add(a.title)
                    art_objs.append(a)
                    art = a.model_dump()
                    artifacts.append(art)
                    yield {"type": "artifact", "artifact": art}

            for res in results:  # history stays in SUB ORDER (deterministic synthesis grounding)
                if res:
                    history.extend(res.history)

            # combine: ONE rich synthesis weaving every facet, citing the unified sources. Pass the
            # full sub-agent `history` (the actual tool results) so the deep synthesis model grounds
            # on real evidence, not just the per-facet notes.
            notes = "\n".join(f"- [{r.title}] {r.note or '근거 수집 완료'}" for r in results if r)
            system_c = ((system or "") + f"\n\n[하위 분석 결과]\n{notes}").strip()
            async for ev in _emit_synthesis({}, history, system_c, "하위 분석을 종합해 답변을 작성하는 중…"):
                yield ev
            answered = True

        for step in range(0 if answered else max_steps):
            is_last = step == max_steps - 1  # reserve the last step for guaranteed synthesis
            decisions = await _plan_batch(is_last)
            # finalize when forced, or when the model returned prose instead of tool calls
            if is_last or (decisions and decisions[0].final is not None):
                async for ev in _emit_verify():
                    yield ev
                await _maybe_refine()  # grounds the synthesis + scores source confidence
                async for ev in _emit_synthesis(tools, history, system):  # real streaming
                    yield ev
                answered = True
                break

            # the model's independent tool calls for this step → fanned out concurrently below
            batch = [d for d in decisions if d.tool]
            for d in batch:  # auto-resolve company name/alias → ticker
                if d.args and "ticker" in d.args:
                    r = resolve_ticker(d.args["ticker"])
                    if r:
                        d.args["ticker"] = r

            # an identical batch as last step means the model is stuck — synthesize now
            sig = "|".join(sorted(s for s in (call_sig(d) for d in batch) if s))
            if sig and sig == last_sig:
                async for ev in _emit_verify():
                    yield ev
                await _maybe_refine()
                async for ev in _emit_synthesis(tools, history, system):
                    yield ev
                answered = True
                break
            last_sig = sig

            # resolve runnable tools; announce ALL of them before fetching in parallel
            valid = []
            for d in batch:
                tool = tools.get(d.tool)
                if tool is None:
                    continue
                valid.append((d, tool))
                label = tool.get("friendly") or tool.get("connector_name") or d.tool
                yield {"type": "tool", "name": d.tool,
                       "label": tool.get("friendly") or tool.get("connector_name"), "args": d.args or {}}
                yield {"type": "thinking", "phase": "fetch", "text": f"{label} 살펴보는 중…", "tool": d.tool}
            if not valid:  # nothing runnable → synthesize from what we have
                await _maybe_refine()
                async for ev in _emit_synthesis(tools, history, system):
                    yield ev
                answered = True
                break

            # PH-THINK: fetch every independent source for this step CONCURRENTLY (one gather).
            results = await asyncio.gather(
                *[client.call_tool(t, d.args or {}) for (d, t) in valid], return_exceptions=True)

            for (d, tool), result in zip(valid, results):
                label = tool.get("friendly") or tool.get("connector_name") or d.tool
                if isinstance(result, Exception):  # one failed call never sinks the batch
                    yield {"type": "tool_result", "status": 0, "connector": tool.get("connector")}
                    yield {"type": "thinking", "phase": "found", "text": f"· {label} 호출에 실패했어요"}
                    continue
                yield {"type": "tool_result", "status": result["status"], "connector": result.get("connector")}
                # SA-1: a 200 from a PERIODIC source makes this question standing-able —
                # record the call as the subscription's change probe (path+args+cadence).
                if result.get("status") == 200 and tool.get("cadence") not in (None, "one_shot"):
                    probes.append({"path": tool.get("path"), "args": d.args or {},
                                   "cadence": tool.get("cadence"), "source": tool.get("source")})
                before = len(citations)
                for c in _citations(tool, result):
                    cit = c.model_dump()
                    if not _add_citation(cit):  # de-dup (merge) repeated sources across tool calls
                        continue
                    cit["index"] = len(citations) + 1  # 1-based [n] anchor
                    citations.append(cit)
                    cite_ctx.append((cit, tool, d.args or {}, result.get("data")))
                    yield {"type": "citation", **cit}
                for a in _artifacts(tool, result):  # U3: connector-backed figure cards
                    if a.title in seen_artifacts:
                        continue
                    seen_artifacts.add(a.title)
                    a.args = d.args or {}     # so a pinned card can re-fetch (U3-03)
                    art_objs.append(a)
                    art = a.model_dump()
                    artifacts.append(art)
                    yield {"type": "artifact", "artifact": art}
                added = len(citations) - before
                ok = result.get("status") == 200 and added > 0
                yield {"type": "thinking", "phase": "found",
                       "text": (f"✓ {label} · 근거 {added}건 확보" if ok else f"· {label}에서 새 근거를 찾지 못함")}
                history.append((d, result))
        if not answered:
            async for ev in _emit_synthesis(tools, history, system):
                yield ev
    except Exception as e:
        logger.exception("Error in stream_chat loop")
        # A planner/LLM error (e.g. bad model id, missing key, upstream outage)
        # degrades to an honest message instead of breaking the stream. The exception
        # detail rides a separate `debug` event (not the answer text) so the chat UI can
        # surface it behind a "🐞 디버그" action without polluting the reply.
        import traceback as _tb
        yield {"type": "debug", "where": "agent-engine.stream",
               "detail": f"{type(e).__name__}: {e}",
               "traceback": _tb.format_exc().strip()[-2000:]}
        yield {"type": "token", "text": "답변 생성 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."}

    # PH-VIZ: attach sourced event markers + price lines, fold technical overlays onto the price
    # chart, then (bounded) let Gemini annotate it — re-emitted in `done` since the streamed
    # `artifact` events went out before the later tool results existed. Shared with run_agent via
    # enrich_artifacts; the annotate step is capped so it never delays `done` (RF-10).
    from agentengine.artifacts import enrich_artifacts
    await enrich_artifacts(art_objs, history, task, settings.model,
                           spec.backend if spec else settings.llm_backend,
                           annotate_timeout=settings.gemini_enrich_timeout_seconds)

    # (The 종목 내러티브 card was removed — it duplicated the answer + the context panel. The
    # narrative/news-brief/value-chain intake flags still steer the synthesis into a structured,
    # sourced answer; we just no longer emit a separate narrative artifact.)

    if art_objs:
        artifacts = [o.model_dump() for o in art_objs]

    # PH-PROV3d: re-anchor each filing citation's evidence image on the figure the ANSWER
    # actually cites (net income / R&D / assets …), not always the first headline (revenue).
    for cit, tool, _args, data in cite_ctx:
        if not isinstance(data, dict):
            continue
        _url, accn, cik = _canonical_provenance(data)
        market = _market_hint(tool, data) or _market_from_link(_url or cit.get("url"), accn)
        new_url = evidence_url_for_answer(data, cit.get("page") or accn, cik, market, final_text)
        if new_url:
            cit["evidence_image_url"] = new_url

    # Evidence vs consulted: a citation is evidence iff the answer cited its [n] or it
    # backs a rendered artifact. The Live Context shows only evidence; every consulted
    # source still appears in the answer's 도구·출처 list. When the model wrote no inline
    # [n], evidence falls back to the citations that actually returned data.
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", final_text or "")}
    art_tools = {a.get("tool") for a in artifacts if a.get("tool")}
    for c in citations:
        c["used"] = (c.get("index") in cited) or (c.get("tool") in art_tools)
    if citations and not any(c.get("used") for c in citations):
        data_bearing = [c for c in citations if c.get("url") or c.get("snippet") or c.get("table")]
        for c in (data_bearing or citations):
            c["used"] = True

    # EV-PASSAGE: filings-LISTING citations the answer used still carry only the report TITLE
    # (the index tool has no body) — swap in the REAL passage from the ingested filing text
    # (RAG, accession-matched) so the viewer highlights content, not "주요사항보고서".
    rag_tool = tools.get("rag__search") if isinstance(tools, dict) else None
    if rag_tool and cite_ctx and final_text:
        from agentengine.passages import enrich_listing_passages, looks_like_title
        search_tool = tools.get("datasets_store__filing_search") if isinstance(tools, dict) else None
        targets = [(cit,
                    _market_hint(tool, data) or _market_from_link(cit.get("url"), cit.get("page")),
                    (args or {}).get("ticker"))
                   for cit, tool, args, data in cite_ctx
                   if cit.get("kind") == "filing" and cit.get("used") and cit.get("page")
                   and looks_like_title(cit.get("snippet"))]
        if targets:
            await enrich_listing_passages(client.call_tool, rag_tool, targets[:4], final_text,
                                          search_tool=search_tool)

    # PH-4c: if the prose carries no inline [n] markers, stream a trailing anchor group
    # for the EVIDENCE only (don't claim every consulted source produced the figures).
    if citations and final_text and not has_anchors(final_text):
        used_idx = [c.get("index") for c in citations if c.get("used")] or [c.get("index") for c in citations]
        yield {"type": "token", "text": " " + anchor_markers(used_idx)}
    used = [c.get("index") for c in citations if c.get("used")]

    # Inline-figure fallback: the article contract says every rendered chart/table appears in
    # the body. If the model placed no {{figure:N}} at all, append the markers at the end so
    # the figures still land inline (the UI swaps each marker for the real artifact card).
    if art_objs and final_text and "{{figure:" not in final_text:
        tail = "\n\n" + "\n\n".join(f"{{{{figure:{i}}}}}" for i in range(1, len(art_objs) + 1))
        final_text += tail
        yield {"type": "token", "text": tail}

    # QT-2: the number audit (publish trust floor) — every numeral in the prose must trace to a
    # value a tool returned THIS turn (deterministic extraction+matching, no LLM). Skipped for
    # tool-less conceptual answers (no pool to match against). The result rides `done`; M-SHARE
    # gates share-card minting on it, and the verify line makes the check visible (trust brand).
    audit = None
    if cite_ctx and final_text:
        from agentengine.audit import audit_ledger
        # LG-1: attribute each pool to its citation's [n] so the ledger can say WHICH source
        # backs each numeral (artifacts ride unindexed — they derive from the same tool data).
        attributed = [(cit.get("index"), data) for cit, _tool, _args, data in cite_ctx]             + [(None, art) for art in artifacts]
        audit = audit_ledger(final_text, attributed)
        if audit["checked"]:
            ok = not audit["unsupported"]
            yield {"type": "thinking", "phase": "verify",
                   "text": (f"숫자 검증 ✓ {audit['checked']}개 수치 모두 자료와 대조 확인" if ok else
                            f"숫자 검증: {audit['checked']}개 중 {audit['supported']}개 확인 · "
                            f"미확인 {len(audit['unsupported'])}건")}

    # PH-THINK: capability-aware follow-up chips — ALWAYS shown after a real answer (deep LLM when
    # gemini, deterministic capability-aware fallback otherwise), so the chip row is never empty.
    sev = await _followups_event(task, final_text, citations, bk, conversation=messages, audit=audit,
                                 artifacts=artifacts, client=client, tools=tools, cite_ctx=cite_ctx)
    if sev:
        yield sev

    # SA-1: offer "이 질문 계속 지켜보기" when the turn touched a periodic source. The chip is
    # cadence-gated here (never a keyword rule) and shown ONCE by the client; event-y sources
    # (filings/earnings) beat daily prices as the probe (the reader cares about the event).
    standing_offer = None
    if probes:
        rank = {"event": 0, "scheduled": 1, "daily": 2, "intraday": 3, "streaming": 4}
        best = sorted(probes, key=lambda p: rank.get(p.get("cadence"), 9))[0]
        sa_cadence = {"event": "event", "scheduled": "event"}.get(best.get("cadence"), "daily")
        standing_offer = {"cadence": sa_cadence,
                          "ticker": (best.get("args") or {}).get("ticker"),
                          "market": (best.get("args") or {}).get("market"),
                          "probe": {"path": best.get("path"), "args": best.get("args"),
                                    "source": best.get("source")}}

    # V-7: 공유 훅 — 발견 한 줄(수치는 본문 실재분만; 검증 탈락 시 None → 질문 제목 폴백)
    hook = None
    if final_text and citations:
        from agentengine.enrichment import make_hook
        hook = await make_hook(task, final_text, spec.backend if spec else None)

    yield {"type": "done", "citations": citations, "artifacts": artifacts, "refused": False,
           "used": used, "audit": audit, "standing_offer": standing_offer, "hook": hook}
