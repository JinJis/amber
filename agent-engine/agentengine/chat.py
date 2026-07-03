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
from agentengine.provenance import _canonical_provenance, _market_hint

logger = logging.getLogger(__name__)


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "")
    return messages[-1].get("content", "") if messages else ""


async def _followups_event(task: str, final_text: str, citations: list[dict],
                           bk: str | None, conversation: list | None = None) -> dict | None:
    """Build the 'suggestions' SSE event for a finished answer. Always non-empty when there's an
    answer — suggest_followups uses the deep LLM on gemini and a deterministic capability-aware
    fallback otherwise — so the chip row renders on EVERY answer path (conceptual + data). The
    recent transcript is passed so the chips DEEPEN the thread instead of restarting it."""
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
    # recent prior turns → the suggester builds on the conversation (심화), not generic chips
    transcript = _intake_context(conversation) if conversation else ""
    conv_block = f"최근 대화:\n{transcript}\n\n" if transcript and transcript != "(no prior turns)" else ""
    eff_backend = bk or settings.llm_backend
    logger.info("chat: requesting follow-up chips (backend=%s, answer_len=%d, tickers=%s, kinds=%s)",
                eff_backend, len(final_text), tickers, kinds)
    sugg = await suggest_followups(task, final_text, settings.model, bk,
                                   context=" · ".join(ctx_bits) or None, tickers=tickers, kinds=kinds,
                                   conversation=conv_block)
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
    from agentengine.planner import resolve_ticker
    history: list = []
    citations: list[dict] = []
    cite_ctx: list[tuple[dict, dict, object]] = []  # (citation, tool, data) → re-anchor evidence post-answer
    artifacts: list[dict] = []
    art_objs: list = []          # the Artifact objects → enrich with chart markers post-loop
    seen_artifacts: set = set()
    seen_cites: set = set()
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

    async def _synthesize(tools_arg, history_arg, system_arg):
        # REAL streaming of the final answer (gemini) — yields token events as the responder
        # generates them. Fallback/stub path char-chunks a one-shot result (newline-preserving).
        nonlocal final_text
        sources = number_sources(citations)
        if hasattr(planner, "stream_final") and (bk or settings.llm_backend) == "gemini":
            got = False
            async for delta in planner.stream_final(task, tools_arg, history_arg, system_arg,
                                                     conversation=messages, sources=sources):
                got = True
                final_text += delta
                yield {"type": "token", "text": delta}
            if not got:
                for ch in _chunks(fallback_answer(citations)):
                    final_text += ch
                    yield {"type": "token", "text": ch}
        else:
            dec = await planner.plan(task, tools_arg, history_arg, system_arg,
                                     conversation=messages, force_final=True, sources=sources)
            for ch in _chunks(dec.final or fallback_answer(citations)):
                final_text += ch
                yield {"type": "token", "text": ch}

    # Conceptual / definitional question → answer from expertise, no tools, streamed.
    if not intake.needs_data:
        yield {"type": "thinking", "phase": "synthesize", "text": "답변을 작성하는 중…"}
        async for ev in _synthesize({}, [], system):
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
            if hasattr(planner, "plan_batch"):
                return await planner.plan_batch(task, tools, history, system, **kw)
            return [await planner.plan(task, tools, history, system, **kw)]

        # A2A: a complex, multi-facet request → dispatch focused sub-agents in PARALLEL (each
        # gathers its own evidence), stream their live cards, then COMBINE into one cited answer.
        if intake.subtasks and len(intake.subtasks) >= 2:
            from agentengine.orchestrator import run_subagent, SUBAGENT_BUDGET
            subs = intake.subtasks
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

            for res in results:  # unify evidence (global de-dup + 1-based [n]), artifacts, history
                if not res:
                    continue
                history.extend(res.history)
                for c in res.citations:
                    cit = c.model_dump()
                    key = (cit.get("source"), cit.get("url"))
                    if key in seen_cites:
                        continue
                    seen_cites.add(key)
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

            # combine: ONE rich synthesis weaving every facet, citing the unified sources. Pass the
            # full sub-agent `history` (the actual tool results) so the deep synthesis model grounds
            # on real evidence, not just the per-facet notes.
            yield {"type": "thinking", "phase": "synthesize", "text": "하위 분석을 종합해 답변을 작성하는 중…"}
            notes = "\n".join(f"- [{r.title}] {r.note or '근거 수집 완료'}" for r in results if r)
            system_c = ((system or "") + f"\n\n[하위 분석 결과]\n{notes}").strip()
            async for ev in _synthesize({}, history, system_c):  # streamed combiner
                yield ev
            answered = True

        for step in range(0 if answered else max_steps):
            is_last = step == max_steps - 1  # reserve the last step for guaranteed synthesis
            decisions = await _plan_batch(is_last)
            # finalize when forced, or when the model returned prose instead of tool calls
            if is_last or (decisions and decisions[0].final is not None):
                if citations and (bk or settings.llm_backend) == "gemini" and not refined:
                    yield {"type": "thinking", "phase": "verify", "text": "근거를 교차검증하는 중…"}
                await _maybe_refine()  # grounds the synthesis + scores source confidence
                yield {"type": "thinking", "phase": "synthesize", "text": "답변을 작성하는 중…"}
                async for ev in _synthesize(tools, history, system):  # real streaming
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
                if not refined and citations and (bk or settings.llm_backend) == "gemini":
                    yield {"type": "thinking", "phase": "verify", "text": "근거를 교차검증하는 중…"}
                await _maybe_refine()
                yield {"type": "thinking", "phase": "synthesize", "text": "답변을 작성하는 중…"}
                async for ev in _synthesize(tools, history, system):
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
                yield {"type": "thinking", "phase": "synthesize", "text": "답변을 작성하는 중…"}
                async for ev in _synthesize(tools, history, system):
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
                before = len(citations)
                for c in _citations(tool, result):
                    cit = c.model_dump()
                    key = (cit.get("source"), cit.get("url"))
                    if key in seen_cites:  # de-dup repeated sources across tool calls
                        continue
                    seen_cites.add(key)
                    cit["index"] = len(citations) + 1  # 1-based [n] anchor
                    citations.append(cit)
                    cite_ctx.append((cit, tool, result.get("data")))
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
            yield {"type": "thinking", "phase": "synthesize", "text": "답변을 작성하는 중…"}
            async for ev in _synthesize(tools, history, system):
                yield ev
    except Exception as e:
        logger.exception("Error in stream_chat loop")
        # A planner/LLM error (e.g. bad model id, missing key, upstream outage)
        # degrades to an honest message instead of breaking the stream.
        yield {"type": "token", "text": f"답변 생성 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요. ({type(e).__name__}: {str(e)})"}

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
    for cit, tool, data in cite_ctx:
        if not isinstance(data, dict):
            continue
        market = _market_hint(tool, data)
        _, accn, cik = _canonical_provenance(data)
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

    # PH-4c: if the prose carries no inline [n] markers, stream a trailing anchor group
    # for the EVIDENCE only (don't claim every consulted source produced the figures).
    if citations and final_text and not has_anchors(final_text):
        used_idx = [c.get("index") for c in citations if c.get("used")] or [c.get("index") for c in citations]
        yield {"type": "token", "text": " " + anchor_markers(used_idx)}
    used = [c.get("index") for c in citations if c.get("used")]

    # PH-THINK: capability-aware follow-up chips — ALWAYS shown after a real answer (deep LLM when
    # gemini, deterministic capability-aware fallback otherwise), so the chip row is never empty.
    sev = await _followups_event(task, final_text, citations, bk, conversation=messages)
    if sev:
        yield sev

    yield {"type": "done", "citations": citations, "artifacts": artifacts, "refused": False, "used": used}
