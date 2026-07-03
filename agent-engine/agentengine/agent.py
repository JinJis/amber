"""The agent loop: guardrail → plan → call tool (via gateway) → observe → finalize.

Tools = the tenant's activated connectors + RAG (resolved from the gateway catalog).
Every tool result's provenance is collected into citations, so agent answers are sourced.

The provenance/citation/artifact *shaping* lives in focused sibling modules — this file
is the orchestration only:
  * ``provenance``  — canonical filing links + (url, accession, cik) extraction
  * ``evidence``    — the PH-PROV2 ``/evidence`` link for a figure
  * ``citations``   — tool result → source cards + extracted figures + evidence marking
  * ``artifacts``   — chartable results → live timeseries artifacts + refresh
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from agentengine import guardrails
from agentengine.artifacts import _artifacts
from agentengine.citations import (
    _citations,
    anchor_markers,
    dedup_citations,
    has_anchors,
    mark_evidence,
)
from agentengine.client import PlatformClient
from agentengine.config import settings
from agentengine.models import AgentSpec, Artifact, Citation, RunResult, Step
from agentengine.planner import get_planner

# Re-exported for back-compat: these moved to focused modules, but importers (chat.py,
# main.py) and tests still reference them via ``agentengine.agent``.
from agentengine.artifacts import _num, _timeseries, refresh_artifact  # noqa: E402,F401
from agentengine.citations import (  # noqa: E402,F401
    _BALANCE_COLS,
    _CASHFLOW_COLS,
    _INCOME_COLS,
    _METRIC_COLS,
    _datasets_type,
    _evidence,
    _fmt_amt,
    _fmt_ratio,
    _latest_date,
    _news_citations,
    _rag_citations,
    _rag_type,
    _shape_table,
    text_fragment_url,
)
from agentengine.evidence import (  # noqa: E402,F401
    _FIELD_CONCEPTS,
    _STATEMENT_HEADLINES,
    _ev_qs,
    _evidence_url,
)
from agentengine.freshness import compute_freshness  # noqa: E402,F401
from agentengine.provenance import (  # noqa: E402,F401
    _canonical_provenance,
    _filing_link,
    _market_hint,
    _rag_link,
    _sec_index_url,
)

logger = logging.getLogger(__name__)



# Re-exported for back-compat (RF-09 split the intake + enrichment clusters into focused
# modules; importers — chat.py, main.py, orchestrator.py — and tests still use agentengine.agent).
from agentengine.intake import (  # noqa: E402,F401
    TaskIntake,
    _INTAKE_PROMPT,
    _INTAKE_SCHEMA,
    _intake_context,
    analyze_task,
)
from agentengine.enrichment import (  # noqa: E402,F401
    _CAPABILITY_MENU,
    _FOLLOWUP_PERSONAS,
    _FOLLOWUP_PROMPT,
    _FOLLOWUP_SCHEMA,
    _REFINE_PROMPT,
    _REFINE_SCHEMA,
    _fallback_followups,
    _followups_one,
    _loads_followups,
    _merge_followups,
    refine_evidence,
    suggest_followups,
)

def call_sig(decision) -> str | None:
    """Stable signature of a tool call, to detect an identical consecutive repeat."""
    if not getattr(decision, "tool", None):
        return None
    return decision.tool + "|" + json.dumps(decision.args or {}, sort_keys=True, ensure_ascii=False)


def fallback_answer(cites) -> str:
    """A non-empty, honest answer when the model returns no final text — never leak
    'Reached the step limit.' to the user. Summarizes what was gathered + anchors."""
    cites = list(cites or [])
    if not cites:
        return ("요청하신 내용을 처리했지만 근거가 될 출처를 충분히 찾지 못했어요. "
                "종목·기간·자료 유형(공시/뉴스/재무)을 조금 더 구체적으로 알려주시면 다시 찾아볼게요.")
    srcs = ", ".join(dict.fromkeys(
        ((c.get("source") if isinstance(c, dict) else c.source) or "출처") for c in cites
    ))
    idxs = [(c.get("index") if isinstance(c, dict) else c.index) for c in cites]
    return (f"관련 자료 {len(cites)}건을 수집했어요 (출처: {srcs}). "
            f"핵심 수치·문장은 아래 출처 카드에서 확인하세요. " + anchor_markers(idxs))


def number_sources(cites) -> str:
    """Numbered source block for the final-answer prompt so the model cites with OUR
    indices — keeping inline [n] aligned to the citation list. Accepts Citation
    objects or dicts."""
    lines = []
    for c in cites:
        get = c.get if isinstance(c, dict) else (lambda k, _c=c: getattr(_c, k, None))
        idx, src = get("index"), get("source")
        if not idx or not src:
            continue
        bits = [f"[{idx}] {src}"]
        if get("snippet"):
            bits.append(str(get("snippet"))[:80])
        if get("as_of"):
            bits.append(str(get("as_of")))
        lines.append(" · ".join(bits))
    return "\n".join(lines)


# CE-4: appended to the system prompt for a holistic company-story request, so the synthesis
# produces a structured, sourced 종목 내러티브 (parsed into a narrative artifact afterwards).
_NARRATIVE_GUIDE = (
    "\n\n[종목 내러티브 형식] 이 답변은 한 종목의 '관전 포인트' 내러티브입니다. 아래 다섯 섹션을 정확히 이 "
    "마크다운 제목으로, 순서대로 작성하세요 (각 섹션 2-4문장):\n"
    "## 사업 개요\n## 최근 실적·재무\n## 밸류에이션\n## 최근 이슈 (뉴스·공시)\n## 관전 포인트\n"
    "모든 구체적 수치·사실은 수집한 출처에서 가져와 문장 끝에 [n]으로 인용하세요. '관전 포인트'는 앞으로 "
    "지켜볼 모니터링 항목을 서술형으로만 적고, 가격 예측·목표가·매수/매도 의견은 절대 넣지 마세요."
)

# section headings the guide asks for (used to title the parsed narrative artifact)
_NARRATIVE_HEADINGS = ("사업 개요", "최근 실적·재무", "밸류에이션", "최근 이슈", "관전 포인트")

# CE-14: appended for a value-chain request → a structured, sourced (derived) supply-chain map.
_VALUE_CHAIN_GUIDE = (
    "\n\n[밸류체인 형식] 이 답변은 한 기업의 밸류체인/공급망 구조 정리입니다. 아래 제목으로, 순서대로 "
    "작성하세요:\n## 핵심 사업\n## 주요 공급사 (상류)\n## 주요 고객 (하류)\n## 경쟁사\n## 밸류체인 내 위치\n"
    "공시(사업의 내용·위험요소)와 뉴스에서 언급된 관계만 근거로 삼고 각 항목 끝에 [n]으로 인용하세요. "
    "추측으로 관계를 만들지 말고, 근거가 없으면 '공시상 명시 없음'이라고 적으세요. 이 분석은 '공시·뉴스 "
    "기반 LLM 추출(derived) — 확정된 거래관계가 아님'임을 마지막에 한 줄로 밝히세요. 가격 예측·매수의견 금지."
)

# CE-10: appended for a news-briefing request → a structured, sourced news narrative.
_NEWS_BRIEF_GUIDE = (
    "\n\n[뉴스 브리핑 형식] 이 답변은 최신 뉴스 브리핑입니다. 아래 제목으로, 순서대로 작성하세요:\n"
    "## 핵심 흐름\n## 주요 헤드라인\n## 맥락·배경\n## 지켜볼 점\n"
    "수집한 뉴스에서 사실만 가져와 각 항목 끝에 [n]으로 발행사·날짜와 함께 인용하세요. '주요 헤드라인'은 "
    "최근 3-6개를 발행사와 함께 bullet로. '지켜볼 점'은 앞으로 모니터링할 사안을 서술형으로만 적고, 가격 "
    "예측·목표가·매수/매도 의견은 절대 넣지 마세요."
)


def filter_tools(tools: dict, allowed: list[str] | None) -> dict:
    """Restrict ``tools`` to ``allowed``. Entries match, in order of precedence, a full tool
    name (``sec_edgar__guru_trades`` — the new per-tool selection), a user-facing category id
    (``gurus`` → every tool in that category), or a connector id (``sec_edgar`` → all of its
    tools — legacy/back-compat). Empty/None means no restriction."""
    if not allowed:
        return tools
    sel = set(allowed)
    return {
        name: t for name, t in tools.items()
        if name in sel or t.get("category") in sel or name.split("__")[0] in sel
    }


async def run_agent(task: str, api_key: str | None, spec: AgentSpec | None = None) -> RunResult:
    # PH-THINK: one intake call both guardrails (LLM judges intent — no keyword rules) and
    # sizes the budget. Refuse here at the agent boundary when the request is restricted.
    intake = await analyze_task(task, spec.backend if spec else None)
    if intake.restricted:
        return RunResult(answer=guardrails.REFUSAL, refused=True, usage={"steps": 0})

    max_steps = spec.max_steps if (spec and spec.max_steps) else intake.steps
    system = spec.system if spec else None
    if intake.plan:
        system = ((system or "") + f"\n\n[연구 계획] {intake.plan}").strip()

    # Conceptual question → answer richly from expertise, skip the tool loop (guardrail still
    # applies; the responder won't assert specific unsourced figures).
    if not intake.needs_data:
        planner = get_planner(spec.backend if spec else None)
        dec = await planner.plan(task, {}, [], system, force_final=True, sources=None)
        return RunResult(answer=(dec.final or fallback_answer([])), refused=False, usage={"steps": 0})

    client = PlatformClient(api_key)
    tools = await client.fetch_tools()
    if spec and spec.allowed_tools:
        tools = filter_tools(tools, spec.allowed_tools)

    history: list = []
    steps: list[Step] = []
    citations: list[Citation] = []
    artifacts: list[Artifact] = []
    seen_artifacts: set = set()
    answer = ""
    try:
        planner = get_planner(spec.backend if spec else None)
        last_sig = None
        for step in range(max_steps):
            is_last = step == max_steps - 1  # reserve the last step for guaranteed synthesis
            sources = number_sources(dedup_citations(citations))  # OUR numbering (PH-4e)
            decision = await planner.plan(task, tools, history, system,
                                          force_final=is_last, sources=sources)
            if is_last or decision.final is not None:
                answer = decision.final or fallback_answer(dedup_citations(citations))
                break

            # Auto-resolve ticker from company name/alias
            if decision.tool and decision.args and "ticker" in decision.args:
                from agentengine.planner import resolve_ticker
                resolved = resolve_ticker(decision.args["ticker"])
                if resolved:
                    decision.args["ticker"] = resolved

            # an identical consecutive call means the model is stuck — synthesize now
            sig = call_sig(decision)
            if sig and sig == last_sig:
                final = await planner.plan(task, tools, history, system, force_final=True, sources=sources)
                answer = final.final or fallback_answer(dedup_citations(citations))
                break
            last_sig = sig

            tool = tools.get(decision.tool)
            if tool is None:
                answer = f"Planner selected an unavailable tool '{decision.tool}'."
                break
            result = await client.call_tool(tool, decision.args or {})
            steps.append(Step(tool=decision.tool, args=decision.args or {}, status=result["status"]))
            citations.extend(_citations(tool, result))
            for a in _artifacts(tool, result):
                if a.title not in seen_artifacts:
                    seen_artifacts.add(a.title)
                    a.args = decision.args or {}  # so a pinned card can re-fetch (U3-03)
                    artifacts.append(a)
            history.append((decision, result))
        if not answer:
            answer = fallback_answer(dedup_citations(citations))
    except Exception as e:
        logger.exception("Error in run_agent loop")
        # Honest degrade on a planner/LLM error rather than a 500.
        answer = answer or f"답변 생성 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요. ({type(e).__name__}: {str(e)})"

    # PH-VIZ: attach sourced event markers + price lines, fold technical overlays onto the price
    # chart, and let Gemini annotate it (shared with the chat stream via enrich_artifacts — RF-10).
    from agentengine.artifacts import enrich_artifacts
    await enrich_artifacts(artifacts, history, task, settings.model,
                           spec.backend if spec else settings.llm_backend)

    cites = dedup_citations(citations)
    # Mark which citations are evidence (cited [n] or back an artifact) vs consulted.
    mark_evidence(cites, answer, artifacts)
    # Ensure the answer is source-anchored: if the planner didn't write inline [n]
    # markers, append a trailing anchor group — but only for the *evidence*, so the
    # answer doesn't claim every consulted source produced its figures.
    if cites and answer and not has_anchors(answer):
        used = [c for c in cites if c.used] or cites
        answer = answer.rstrip() + " " + anchor_markers([c.index for c in used])
    return RunResult(answer=answer, steps=steps, citations=cites, artifacts=artifacts, usage={"steps": len(steps)})
