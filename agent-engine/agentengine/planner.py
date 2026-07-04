"""Planner: decide which tool to call (or finalize) given the task + tools.

* GeminiPlanner — real LLM (Gemini function calling), lazily imported. This is the ONLY
  planner: the platform is Gemini-only (invariant #7); the legacy deterministic `stub`
  planner has been removed (answer quality/routing must come from the LLM, never hand-rolled
  keyword rules — invariant #9). Routing here needs a GOOGLE_API_KEY.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import cache

from agentengine.config import settings

# `resolve_ticker` (+ `_user_text`) normalize the ticker the MODEL produces (e.g. "Apple" →
# "AAPL") on the gemini path, so they stay; the rest of routing.py is legacy keyword-routing
# kept only for these utilities. Re-exported so agent.py/chat.py/orchestrator.py resolve them here.
from agentengine.routing import (  # noqa: F401
    _user_text,
    resolve_ticker,
)
from agentengine.gemini_io import (  # noqa: F401
    _get_text_from_response,
    _schema,
    _to_gemini_contents,
)

logger = logging.getLogger(__name__)


# The responder prompt. The whole point: a rich answer that MIXES our sourced evidence with
# the model's own analyst expertise — not a terse restatement of fetched rows. Hard rules keep
# it trustworthy: every NUMBER/specific fact comes from a source and is cited; the model may
# (and should) add qualitative context/definitions/interpretation; no forecast/advice; no
# fabricated figures; no tool names or raw URLs in the prose.
_SYNTHESIS_PROMPT = (
    "당신은 금융 리서치 애널리스트입니다. 사용자의 질문에 같은 언어로, 직접적이고 간결하게 답하세요.\n"
    "분량은 질문에 비례합니다:\n"
    "- 단순한 사실/수치 질문(예: '환율', 'Fed 기준금리 추이')에는 핵심만 1~3문장으로. 묻지 않은 역사 "
    "강의·배경 설명·머리말·반복은 넣지 마세요.\n"
    "- 사용자가 '자세히/분석/이유'를 요청했거나 본질적으로 복잡한 질문일 때만 더 길게, 필요한 만큼만 설명하세요.\n"
    "원칙:\n"
    "- 구체적 수치·날짜·사실은 위에 제공된 자료에서만 가져오고 문장 끝에 [n]으로 인용하세요. 시스템 'Sources' "
    "목록의 정확한 번호만 쓰고, 새 번호를 만들거나 순서를 바꾸지 마세요. 자료에 없는 수치는 절대 지어내지 마세요.\n"
    "- 완결성: 질문이 여러 항목을 요구하면(예: '물가·고용·성장·금리', '매출과 EPS', '연도별') 각 항목을 "
    "빠짐없이 다루세요. 자료에 없는 항목은 건너뛰지 말고 '~은 이번 자료에 없음'이라고 항목별로 명시하세요. "
    "질문에 답하기 전에 요구된 항목 목록을 속으로 체크리스트로 만들어 하나씩 확인하세요.\n"
    "- 인용 밀도: 수치·날짜·고유 사실이 담긴 '모든' 문장에 [n]을 붙이세요 — 문단당 하나가 아니라 문장 단위로. "
    "표를 쓸 경우 표 아래에 근거 [n]을 명시하세요.\n"
    "- 맥락·해석을 덧붙일 때도 간결하게. 수치 나열이 아니라 핵심 의미만 짚으세요.\n"
    "- 자료가 부족하면 솔직히 밝히고, 무엇을 더 보면 되는지 한 줄로 안내하세요.\n"
    "- 가격 예측·목표가·매수/매도 의견 금지. 면책 문구·내부 도구명(예: opendart__income_statements)·"
    "원문 URL은 본문에 쓰지 마세요([n]만 — 링크는 출처 카드에 표시됩니다).\n"
    "- 밸류에이션(DCF/DDM/RIM)·백테스트 결과를 쓸 때: 사용한 가정·보유·기간을 명시하고, 문단 끝에 "
    "'가정 기반 계산 · 예측·목표가 아님'(밸류에이션) 또는 '과거 성과 · 미래 수익 보장 아님'(백테스트)을 "
    "붙이세요. 내재가치·수익률 수치는 계산 근거 자료의 값 그대로 인용하세요.\n"
    "- 과거 통계(낙폭 에피소드·베이스레이트·유사 구간 등 market_history 자료)를 쓸 때: 반드시 "
    "과거형·기술형으로만 서술하고('과거 87건에서 20일 뒤 중앙값 +2.8%였다'), 사건 정의와 n(사례 수)과 "
    "기간을 본문에 명시하세요. '~할 확률', '반등할 것', '앞으로 ~할 수 있다' 같은 미래 주장 표현은 금지 — "
    "'상승 마감 비율(과거)'처럼 기록임을 드러내는 표현만 쓰세요. 해당 단락 끝에 "
    "'과거 기록 · 전망 아님'을 붙이세요.\n"
    "마크다운을 쓰되, 짧은 답에는 헤딩·불릿을 남용하지 말고 자연스러운 문단으로 쓰세요."
)


@dataclass
class Decision:
    tool: str | None = None
    args: dict | None = None
    final: str | None = None
    thought_signature: bytes | None = None
    # The model's RAW response Content for this turn (all function_call parts + their
    # thought_signatures). Replayed verbatim into history so parallel calls keep their
    # signatures — reconstructing them part-by-part drops/desyncs signatures (Gemini 400).
    raw_content: object | None = None


class GeminiPlanner:
    """Real Gemini planner (function calling). Untested without GOOGLE_API_KEY."""

    def __init__(self, model: str) -> None:
        from google import genai

        from agentengine.gemini_io import genai_client

        self._genai = genai
        self._client = genai_client()  # bounded request timeout (no infinite SSE hang)
        self.model = model

    async def plan(self, task: str, tools: dict, history: list, system: str | None = None,
                   conversation: list | None = None, force_final: bool = False,
                   sources: str | None = None) -> Decision:
        # single-decision view (run_agent / callers that don't fan out): the first call.
        decisions = await self._run(task, tools, history, system, conversation, force_final, sources)
        return decisions[0]

    async def plan_batch(self, task: str, tools: dict, history: list, system: str | None = None,
                         conversation: list | None = None, force_final: bool = False,
                         sources: str | None = None) -> list[Decision]:
        # ALL of the model's parallel function calls this step (fanned out concurrently by the
        # caller), or a single final Decision. This is what enables parallel multi-source gather.
        return await self._run(task, tools, history, system, conversation, force_final, sources)

    def _build_system_instruction(self, system: str | None, sources: str | None) -> str:
        from datetime import datetime

        current_date = datetime.now().strftime("%Y-%m-%d")
        base_system = (
            "You are an expert financial-data assistant. Your goal is to answer the user's query using the provided tools.\n\n"
            f"Current Date: {current_date}\n\n"
            "Guidelines for tool selection:\n"
            "1. For stock prices, historical stock prices, EOD prices, charts, or recent market prices, use 'yahoo__prices'.\n"
            "2. For general company search, semantic queries, news, press releases, risk factors, or qualitative information, use the RAG search tool 'rag__search'.\n"
            "3. For official US public company filings, financial reports, or company profile facts, use 'sec_edgar__company_facts'.\n"
            "4. For Korean public company financial statements or reports, use 'opendart__income_statements'.\n"
            "5. For macro economy metrics like interest rates or central bank decisions, use 'fred__interest_rates' (US/global) or 'ecos__interest_rates_snapshot' (Korea).\n"
            "6. For a MULTI-INDICATOR macro OVERVIEW of one country (물가·고용·성장·금리 한눈에 / '거시 패널'), use 'fred__macro_panel' — NOT a series of single-indicator calls.\n"
            "7. For a portfolio BACKTEST (종목+비중의 과거 성과: 누적수익·CAGR·MDD), use 'datasets_store__backtest' — NEVER hand-compute from 'yahoo__prices' closes.\n"
            "8. For factor SCREENING (ROE/PER 등 조건으로 종목 찾기), use 'datasets_store__quant_screen'.\n"
            "9. For historical drawdown/regime/base-rate/analogue questions (과거 낙폭·국면 비교), use the 'market_history__*' tools; index names resolve (S&P500→^GSPC, 코스피→^KS11).\n\n"
            "Important Parameter Instructions:\n"
            "- 'ticker': Stock tickers MUST be official symbols (e.g., 'AAPL' for Apple, '005930' for Samsung Electronics). NEVER pass company names (e.g., 'Apple', '삼성전자') as the ticker parameter.\n"
            "- Always identify the correct market ('US' or 'KR') based on the company or central bank mentioned.\n"
            "- Resolve follow-up references (e.g. 'that company') from the conversation so far.\n"
            "- PARALLEL: when a question needs several INDEPENDENT pieces of data (e.g. price AND news AND "
            "financials, or the same metric for multiple companies), call those tools TOGETHER in one step "
            "(emit multiple function calls at once) so they are fetched concurrently. Only chain calls when a "
            "later call truly depends on an earlier result.\n"
            "When you write the final answer, anchor each claim with an inline [n] marker that refers to "
            "the numbered source list provided below; use ONLY those exact numbers and never renumber.\n"
            "Never predict prices or give buy/sell advice; this is not investment advice."
        )

        system_instruction = f"{base_system}\n\n{system.strip()}" if system and system.strip() else base_system
        if sources:
            # the authoritative numbering — the model must cite with these exact [n].
            system_instruction += (
                "\n\nSources (cite ONLY with these exact bracketed numbers; do not invent or reorder):\n"
                + sources
            )
        return system_instruction

    async def stream_final(self, task: str, tools: dict, history: list, system: str | None = None,
                           conversation: list | None = None, sources: str | None = None):
        """REAL token streaming of the final synthesis (responder model). Yields text deltas
        as Gemini generates them — so the answer appears incrementally, not all at once. Each
        `next()` on the sync stream is offloaded so the event loop stays free."""
        import asyncio
        from google.genai import types

        system_instruction = self._build_system_instruction(system, sources)
        contents = _to_gemini_contents(conversation, history, task)
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=_SYNTHESIS_PROMPT)]))
        config = types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.3)
        model = settings.synthesis_model or self.model
        it = await asyncio.to_thread(self._client.models.generate_content_stream,
                                     model=model, contents=contents, config=config)

        def _next(gen):
            try:
                return next(gen)
            except StopIteration:
                return None

        while True:
            chunk = await asyncio.to_thread(_next, it)
            if chunk is None:
                break
            t = getattr(chunk, "text", "") or ""
            if t:
                yield t

    async def _run(self, task: str, tools: dict, history: list, system: str | None = None,
                   conversation: list | None = None, force_final: bool = False,
                   sources: str | None = None) -> list[Decision]:
        import asyncio
        from google.genai import types

        system_instruction = self._build_system_instruction(system, sources)
        contents = _to_gemini_contents(conversation, history, task)

        if force_final:
            # The rich responder: MIX our sourced evidence (cited, never fabricated) with the
            # model's own analyst context, so the answer is genuinely useful — not a terse
            # data-dump. Numbers stay sourced (invariant #1); qualitative insight is the model's.
            contents.append(types.Content(role="user", parts=[types.Part.from_text(text=_SYNTHESIS_PROMPT)]))
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,   # finance: grounded + accurate over flowery (still natural prose)
            )
            # use the dedicated (light) response model, falling back to the planner model.
            model = settings.synthesis_model or self.model
            resp = await asyncio.to_thread(self._client.models.generate_content, model=model, contents=contents, config=config)
            return [Decision(final=_get_text_from_response(resp))]

        decls = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=_schema(t))
            for t in tools.values()
        ]

        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=decls)],
            system_instruction=system_instruction,
        )

        resp = await asyncio.to_thread(self._client.models.generate_content, model=self.model, contents=contents, config=config)
        calls = getattr(resp, "function_calls", None)
        if calls:
            # Gemini parallel function calling: return EVERY call this step so the caller fans them
            # out concurrently. Carry the model's RAW content (all parts + thought_signatures) on each
            # Decision so history replays it verbatim — every emitted call MUST get a response, so we
            # execute them ALL (no cap; the model bounds the count) to keep calls↔responses aligned.
            model_content = resp.candidates[0].content if resp.candidates else None
            parts = (model_content.parts if (model_content and model_content.parts) else [])
            fc_parts = [p for p in parts if getattr(p, "function_call", None)]
            out: list[Decision] = []
            for i, call in enumerate(calls):
                sig = fc_parts[i].thought_signature if i < len(fc_parts) else None
                out.append(Decision(tool=call.name, args=dict(call.args or {}),
                                    thought_signature=sig, raw_content=model_content))
            return out
        return [Decision(final=_get_text_from_response(resp))]


@cache
def _build_planner(model: str):
    # Gemini-only (invariant #7). Any legacy per-agent backend value (e.g. an old "stub" Agent
    # row) maps here to the single Gemini planner — there is no other backend.
    return GeminiPlanner(model)


def get_planner(backend: str | None = None):
    """Return the Gemini planner. ``backend`` is accepted for call-site compatibility but
    ignored — the platform is Gemini-only (the legacy stub planner has been removed)."""
    return _build_planner(settings.model)
