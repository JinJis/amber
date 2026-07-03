"""Task intake: the first-pass LLM analysis of a user turn (RF-09, split from agent.py).

ONE Gemini call both GUARDRAILS the request (judging intent in context — never keyword rules,
invariant #9) and PLANS it (step budget + short plan + clarify/decompose/narrative flags). The
orchestration loop (``agent.run_agent``) consumes the resulting ``TaskIntake``. Re-exported via
``agentengine.agent`` for back-compat.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from agentengine.config import settings

logger = logging.getLogger(__name__)


# --- PH-15 / PH-THINK: LLM-assessed step budget + guardrail, folded into one intake ----
# A light model both judges the request (guardrail) and rates how many tool hops it needs
# (not hardcoded keyword rules): simple single-fact asks finish fast; multi-source asks
# ("공급망·리스크 공시 요약") get headroom. The numbered budget is then strictly honored
# (the loop reserves its last step for synthesis, so it never leaks "Reached the step limit").
@dataclass
class TaskIntake:
    """The first-pass LLM analysis of a user turn: the GUARDRAIL decision (judged from
    intent, in context — no keyword rules, invariant #9) folded together with PLANNING
    (step budget + a short plan). One Gemini call produces all of it."""

    steps: int
    plan: str | None = None
    restricted: bool = False     # the user asked for forecast/advice/target as output → refuse
    score: float = 0.0           # confidence (0..1) it's a restricted REQUEST
    reason: str | None = None    # one-line why (shown for telemetry)
    needs_data: bool = True      # does answering require our tools/data? False = conceptual →
    #                              answer richly from expertise, skip the tool loop.
    # CLARIFY (Claude-Code-style plan/ask): when the request is broad/ambiguous, offer the user
    # 2-4 concrete choices to scope the work instead of guessing. The chat surfaces these as
    # clickable chips; picking composes a refined follow-up. Only set when it genuinely helps.
    clarify: bool = False
    clarify_prompt: str | None = None
    options: list = field(default_factory=list)   # [{"label": str, "description": str|None}]
    multi: bool = False          # may several options be combined (pick-multiple) ?
    # A2A DECOMPOSITION: for a complex, multi-facet request, the orchestrator splits it into
    # 2-4 focused sub-tasks researched by parallel sub-agents, then combined. Empty = single agent.
    subtasks: list = field(default_factory=list)  # [{"title": str, "question": str}]
    # CE-4 NARRATIVE: the user wants a holistic STORY / 관전 포인트 for a specific company →
    # gather across facets and synthesize a structured, sourced narrative (+ a narrative card).
    narrative: bool = False
    # CE-10 NEWS BRIEF: the user wants a news briefing / market pulse (시황·무슨 일·뉴스 정리) →
    # gather recent news and synthesize a structured, sourced news narrative.
    news_brief: bool = False
    # CE-14 VALUE CHAIN: the user asks about a company's 밸류체인/공급망 구조 (공급사·고객·경쟁사) →
    # extract upstream/downstream/competitors from filings+news, labelled derived.
    value_chain: bool = False


_INTAKE_PROMPT = (
    "You are the intake analyst for a financial RESEARCH service that provides ONLY sourced, "
    "historical/descriptive facts (public filings, financials, prices, macro data, news). It must "
    "REFUSE to *produce* forward-looking or advisory content. Read the user's question and reply "
    "with JSON.\n\n"
    "GUARDRAIL — decide whether the user is REQUESTING, as the desired output, any of:\n"
    "- a price/market PREDICTION or FORECAST (future direction, 'will it go up', earnings forecast)\n"
    "- BUY/SELL/HOLD advice, an investment recommendation, or entry/exit timing\n"
    "- a PRICE TARGET (목표가 / 목표주가)\n"
    "Judge INTENT, not vocabulary. A request is NOT restricted merely because it MENTIONS these "
    "words. If the user EXCLUDES or NEGATES them it is ALLOWED — they want facts. ALLOWED examples: "
    "'목표가는 제시하지 말고 가격 흐름만', '전망·매수의견은 넣지 말고 사실 위주로', 'do NOT give a "
    "forecast, just what happened'. Descriptive past/current facts, news summaries, filings, "
    "financials, and macro data are ALWAYS allowed.\n\n"
    "PLAN — when allowed, size the work and outline it (no answer, no numbers).\n"
    "DATA ROUTING — set needs_data=true when answering requires looking up sourced financial DATA "
    "(prices, filings, financial statements, macro indicators, holdings, news, a specific company's "
    "figures). Set needs_data=false for purely CONCEPTUAL/definitional/how-to questions answerable from "
    "general knowledge (e.g. 'PER이 뭐야?', 'how does an ETF work?', 'explain DCF') — those are answered "
    "richly from expertise without a tool call.\n"
    "CLARIFY — if the request is BROAD / open-ended / ambiguous so you can't tell which specific aspect "
    "the user wants (e.g. '엔비디아 분석해줘', '삼성전자 어때?', '반도체 시장 알려줘', 'tell me about Tesla'), "
    "set clarify=true with a short clarify_prompt and 2-4 concrete ASPECT options (each "
    "{{\"label\", \"description\"}}, same language) so the user can steer — Claude-Code style. set multi=true "
    "if several aspects can be combined. Do NOT clarify a clearly specific request (e.g. 'AAPL 최근 종가', "
    "'엔비디아 매출').\n"
    "DECOMPOSE — set subtasks ONLY when the user EXPLICITLY asks for a comprehensive / all-in-one analysis "
    "(e.g. '종합적으로 분석', '전반적으로', 'comprehensive', '다 분석해줘') → 2-4 focused "
    "{{\"title\", \"question\"}} sub-tasks researched in PARALLEL. Otherwise leave subtasks empty.\n"
    "NARRATIVE — set narrative=true when the user wants a holistic STORY / 관전 포인트 / 내러티브 / 투자 "
    "포인트 / overview of a SPECIFIC company (e.g. '엔비디아 관전 포인트', '테슬라 스토리 알려줘', "
    "'tell me the story of Apple', '이 종목 정리해줘'). Then do NOT clarify — gather across the company's "
    "business, recent financials, valuation, recent filings and news, and set steps ≈ 8-12. For a single "
    "narrow fact (e.g. '엔비디아 매출') leave narrative=false.\n"
    "NEWS BRIEF — set news_brief=true when the user wants a NEWS briefing / market pulse / 시황 / 뉴스 "
    "정리 / '무슨 일이야' / what's happening (a company's news flow, or the market's). Then gather RECENT "
    "NEWS (and related context) and do NOT clarify.\n"
    "VALUE CHAIN — set value_chain=true when the user asks about a company's 밸류체인 / 공급망 구조 / "
    "공급사·고객사 / value chain / supply chain (who it buys from, sells to, competes with). Then gather "
    "its filings + news and do NOT clarify.\n\n"
    "Reply JSON ONLY:\n"
    '{{"restricted": <bool — true ONLY if the user truly wants restricted output>, '
    '"category": "forecast|advice|price_target|none", '
    '"score": <number 0..1 — confidence it is a restricted REQUEST>, '
    '"needs_data": <bool — true if sourced data lookup is required, false if conceptual>, '
    '"clarify": <bool — true only if offering choices genuinely helps>, '
    '"clarify_prompt": "<the short question to show the user, SAME LANGUAGE; empty if clarify=false>", '
    '"multi": <bool — may several options be combined>, '
    '"options": [{{"label": "<short choice>", "description": "<one line>"}}],  '
    '"subtasks": [{{"title": "<short facet name, SAME LANGUAGE>", "question": "<self-contained sub-question>"}}],  '
    '"narrative": <bool — true for a holistic company story / 관전 포인트 request>, '
    '"news_brief": <bool — true for a news briefing / 시황 / 뉴스 정리 request>, '
    '"value_chain": <bool — true for a 밸류체인 / 공급망 구조 request>, '
    '"reason": "<one short line, SAME LANGUAGE as the question>", '
    '"steps": <int tool-call budget: one fact about one company ≈ 2-3, a comparison or multi-source '
    'ask ≈ 8-12>, '
    '"plan": "<one short sentence, SAME LANGUAGE as the question, of what data you will look up and '
    'from which kind of source — NOT the answer, no numbers; empty if restricted/conceptual/clarify>"}}\n\n'
    "CONVERSATION SO FAR (for CONTEXT only — the latest question may refer back to it). A follow-up "
    "like '배당률은?', '그 회사 주가는?', 'what about its margins?' INHERITS the company/subject named "
    "earlier. RESOLVE such references and treat the question as if it named that subject explicitly; "
    "do NOT set clarify for something the context already determines. Plan/reason should name the "
    "resolved subject.\n{context}\n\n"
    "Latest question: {task}"
)

_INTAKE_SCHEMA = {
    "type": "object",
    "properties": {
        "restricted": {"type": "boolean"},
        "category": {"type": "string", "enum": ["forecast", "advice", "price_target", "none"]},
        "score": {"type": "number"},
        "reason": {"type": "string"},
        "needs_data": {"type": "boolean"},
        "clarify": {"type": "boolean"},
        "clarify_prompt": {"type": "string"},
        "multi": {"type": "boolean"},
        "options": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "description": {"type": "string"}}, "required": ["label"]}},
        "subtasks": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "question": {"type": "string"}}, "required": ["title", "question"]}},
        "narrative": {"type": "boolean"},
        "news_brief": {"type": "boolean"},
        "value_chain": {"type": "boolean"},
        "steps": {"type": "integer"},
        "plan": {"type": "string"},
    },
    "required": ["restricted", "steps"],
}


def _intake_context(conversation: list | None, limit: int = 6) -> str:
    """A short transcript of the recent turns so the intake can resolve follow-up references
    (a company/topic named earlier). Excludes the latest user turn (that's the question)."""
    msgs = [m for m in (conversation or []) if (m.get("content") or "").strip()]
    if len(msgs) <= 1:
        return "(no prior turns)"
    lines = []
    for m in msgs[:-1][-limit:]:
        role = "사용자" if m.get("role") == "user" else "분석가"
        lines.append(f"{role}: {(m.get('content') or '').strip()[:300]}")
    return "\n".join(lines) or "(no prior turns)"


async def analyze_task(task: str, backend: str | None = None, conversation: list | None = None) -> TaskIntake:
    """PH-THINK: ONE first-pass LLM call that BOTH guardrails the request (judging intent in
    context — never keyword matching) AND plans it (step budget + a short plan to show the user).
    The guardrail lives here, inside the analysis layer. `conversation` (prior turns) lets the
    intake RESOLVE follow-up references (e.g. '배당률은?' inherits the earlier company) instead of
    clarifying. Stub / no-key / error → allow with the default budget and no plan."""
    cap, default, floor = settings.max_steps_cap, settings.max_steps, 3
    if (backend or settings.llm_backend) != "gemini":
        return TaskIntake(steps=default)
    try:
        import asyncio
        from google import genai
        from google.genai import types

        from agentengine.gemini_io import genai_client
        client = genai_client()  # bounded request timeout (no infinite SSE hang)
        resp = await asyncio.to_thread(
            client.models.generate_content, model=settings.budget_model,
            contents=_INTAKE_PROMPT.format(task=(task or "")[:800], context=_intake_context(conversation)),
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               response_schema=_INTAKE_SCHEMA, max_output_tokens=400))
        d = json.loads(getattr(resp, "text", "") or "{}")
        try:
            n = int(d.get("steps"))
        except (TypeError, ValueError):
            n = None
        try:
            score = float(d.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        # refuse only when the model both flags it AND is confident enough (a low-confidence
        # guess never blocks a legitimate question).
        restricted = bool(d.get("restricted")) and score >= settings.guardrail_threshold
        # default to needs_data=True when the model omits it (gathering data is the safe default).
        needs_data = d.get("needs_data")
        needs_data = True if needs_data is None else bool(needs_data)
        # clarify only when not restricted AND there are ≥2 concrete options to offer.
        opts = []
        for o in (d.get("options") or []):
            if isinstance(o, dict) and o.get("label"):
                opts.append({"label": str(o["label"])[:80],
                             "description": (str(o.get("description") or "").strip()[:160] or None)})
        # CE-4: a holistic company-story request → narrative synthesis (and never a clarify prompt;
        # we already know the intent). Requires data + not restricted.
        narrative = bool(d.get("narrative")) and needs_data and not restricted
        news_brief = bool(d.get("news_brief")) and needs_data and not restricted
        value_chain = bool(d.get("value_chain")) and needs_data and not restricted
        clarify = (bool(d.get("clarify")) and not restricted and not narrative
                   and not news_brief and not value_chain and len(opts) >= 2)
        # decompose only for a clear, complex request (not restricted/clarify/conceptual) with ≥2 facets.
        subs = []
        for s in (d.get("subtasks") or []):
            if isinstance(s, dict) and s.get("title") and s.get("question"):
                subs.append({"title": str(s["title"])[:80], "question": str(s["question"])[:400]})
        subtasks = subs[:4] if (not restricted and not clarify and needs_data and len(subs) >= 2) else []
        return TaskIntake(
            steps=(max(floor, min(n, cap)) if n else default),
            plan=(None if (restricted or clarify) else (str(d.get("plan") or "").strip() or None)),
            restricted=restricted, score=score,
            reason=(str(d.get("reason") or "").strip() or None),
            needs_data=needs_data,
            clarify=clarify,
            clarify_prompt=(str(d.get("clarify_prompt") or "").strip() or None) if clarify else None,
            options=(opts if clarify else []),
            multi=bool(d.get("multi")),
            subtasks=subtasks,
            narrative=narrative,
            news_brief=news_brief,
            value_chain=value_chain,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to allow + default budget, never block
        logger.warning("task intake failed (%s); allowing with default budget", exc)
        return TaskIntake(steps=default)
