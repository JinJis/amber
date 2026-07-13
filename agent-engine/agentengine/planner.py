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
from agentengine.usage import report as report_usage

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
    generate,
)

logger = logging.getLogger(__name__)


# The responder prompt. The whole point: a rich RESEARCH-NOTE answer (전문 블로그 글) that MIXES
# our sourced evidence with the model's own analyst expertise — not a terse restatement of
# fetched rows. The figures the turn produced ({{figure:N}}, see the Figures block in the system
# instruction) are placed INLINE so charts/tables read as part of the article, not a side panel.
# Hard rules keep it trustworthy: every NUMBER/specific fact comes from a source and is cited;
# no forecast/advice; no fabricated figures; no tool names or raw URLs in the prose.
_SYNTHESIS_PROMPT = (
    "인용 마커는 반드시 개별 대괄호로: [3][5]. [3,5]·[3, 5]·[3-5]처럼 묶어 쓰지 마.\n"
    "당신은 금융 리서치 데스크의 시니어 애널리스트입니다. 사용자의 질문에 같은 언어로, 잘 짜인 "
    "리서치 노트 — 전문 투자 블로그의 글 — 형식으로 답하세요. 당신의 지식과 분석이 글의 뼈대이고, "
    "아래 자료의 검증된 수치가 그 뼈대에 근거를 박습니다. 일반 챗봇만큼 풍부하게 쓰되, 숫자만은 "
    "전부 검증된 것만 쓰는 것 — 그것이 이 데스크의 차별점입니다.\n"
    "\n"
    "글의 구성 (수집된 자료가 여럿일 때):\n"
    "- 리드: 첫 1~2문장에서 질문의 핵심 답을 바로 제시하세요 (두괄식). 인사말·서론·질문 반복 금지.\n"
    "- 본문: '## 소제목'으로 자연스럽게 2~4개 섹션을 나누고, 각 섹션은 데이터 → 그 데이터가 말해주는 것 → "
    "맥락(정의·비교·역사적 크기감) 순으로 문단을 이어가세요. 수치를 나열하지 말고 흐름·비교·규모가 "
    "읽히는 문장으로 풀어내세요. 서로 다른 자료가 같은 방향을 가리키는지/어긋나는지 짚어주면 좋습니다.\n"
    "- 필요하면 비교표(마크다운 표)를 쓰고, 표 아래에 근거 [n]을 명시하세요.\n"
    "- 마무리: '이번에 확인된 것' 2~3줄 요약 + 자료로 더 파볼 만한 다음 질문 방향 한 줄 "
    "(전망이 아니라 '무엇을 더 보면 되는지').\n"
    "- 분량: 자료가 풍부하면 아끼지 마세요 — 깊이 있는 글이 우리 서비스의 가치입니다. 반대로 단순한 "
    "사실/수치 질문(예: '환율 얼마야')에는 헤딩 없이 핵심만 1~3문장으로 짧게 끝내세요.\n"
    "\n"
    "그림 배치 — 시스템의 'Figures' 목록이 있을 때 (이번 턴에 실제로 그려진 차트·표):\n"
    "- 해당 데이터를 다룬 문단 '바로 다음 줄'에 {{figure:N}}을 단독 줄로 넣으세요. 그 자리에 실제 "
    "차트/표가 본문 그림으로 렌더링됩니다. '아래 차트에서 보듯 …'처럼 그림을 본문이 가리키게 쓰세요.\n"
    "- 목록에 있는 번호만, 각 번호는 최대 1번. 글과 무관한 그림은 배치하지 않아도 됩니다.\n"
    "\n"
    "원칙 (모두 필수):\n"
    "- 역할 분담 — 서사는 당신, 수치는 자료: 당신은 이 분야를 깊이 아는 애널리스트입니다. 사업 구조, "
    "산업의 경쟁 구도, 개념 정의, 정책·사건의 배경 같은 정성적 맥락은 당신의 지식으로 자신 있게, 풍부하게 "
    "서술하세요 — 이런 일반 서술에는 [n]을 붙이지 않습니다. 위 자료는 그 서사에 '검증된 수치'를 박아 넣는 "
    "무기입니다: 구체적 수치·날짜·고유 사실은 반드시 자료에서만 가져와 문장 끝에 [n]으로 인용하고, 자료에 "
    "없는 수치를 기억으로 쓰는 것은 금지입니다. 시스템 'Sources' 목록의 정확한 번호만 쓰고 새 번호를 "
    "만들거나 순서를 바꾸지 마세요.\n"
    "- 완결성: 질문이 여러 항목을 요구하면(예: '물가·고용·성장·금리', '매출과 EPS', '연도별') 각 항목을 "
    "빠짐없이 다루세요. 어떤 항목의 수치가 자료에 없으면 그 항목은 구체 수치 없이 정성적 지식으로 서술하면 "
    "됩니다 — 항목을 통째로 건너뛰지도, 데이터 부재를 고백하지도 마세요.\n"
    "- 금지 문구: '조회된 데이터베이스에는 ~이 포함되어 있지 않습니다', '제공된 자료에서는 확인할 수 "
    "없습니다' 같은 시스템 내부가 비치는 데이터-한계 나열은 절대 금지. 독자는 자료 파이프라인의 존재를 "
    "모릅니다 — 없는 것을 사과하는 대신 아는 것을 서술하세요. 질문의 핵심 자체가 자료 없이는 답할 수 없는 "
    "예외적인 경우에만, 글 끝에 무엇을 더 보면 되는지 한 줄로 안내하세요.\n"
    "- 인용 밀도: 자료에서 가져온 수치·날짜·고유 사실이 담긴 '모든' 문장에 [n]을 붙이세요 — 문단당 하나가 "
    "아니라 문장 단위로. 표를 쓸 경우 표 아래에 근거 [n]을 명시하세요.\n"
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
    "마크다운을 쓰되, 짧은 답에는 헤딩·불릿·그림 배치를 남용하지 말고 자연스러운 문단으로 쓰세요."
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
        # CR-6/SC-1.1: the planner is a PROCESS-WIDE singleton (@cache), so per-turn synthesis
        # tier must be passed as a call argument — never stored on self (that raced across
        # concurrent users: a free-tier flash override downgraded a concurrent pro user's synthesis).

    async def plan(self, task: str, tools: dict, history: list, system: str | None = None,
                   conversation: list | None = None, force_final: bool = False,
                   sources: str | None = None, figures: str | None = None,
                   synthesis_model: str | None = None) -> Decision:
        # single-decision view (run_agent / callers that don't fan out): the first call.
        decisions = await self._run(task, tools, history, system, conversation, force_final,
                                    sources, figures, synthesis_model=synthesis_model)
        return decisions[0]

    async def plan_batch(self, task: str, tools: dict, history: list, system: str | None = None,
                         conversation: list | None = None, force_final: bool = False,
                         sources: str | None = None, synthesis_model: str | None = None) -> list[Decision]:
        # ALL of the model's parallel function calls this step (fanned out concurrently by the
        # caller), or a single final Decision. This is what enables parallel multi-source gather.
        return await self._run(task, tools, history, system, conversation, force_final, sources,
                               synthesis_model=synthesis_model)

    def _build_system_instruction(self, system: str | None, sources: str | None,
                                  figures: str | None = None) -> str:
        from datetime import datetime

        current_date = datetime.now().strftime("%Y-%m-%d")
        base_system = (
            "You are an expert financial-data assistant. Your goal is to answer the user's query using the provided tools.\n\n"
            f"Current Date: {current_date}\n\n"
            "Guidelines for tool selection:\n"
            "1. For stock prices, historical stock prices, EOD prices, charts, or recent market prices, use 'yahoo__prices'. EXCEPTION: any '낙폭'/'고점 대비'/drawdown/MDD question is NOT a price question — rule 9 applies ('market_history__drawdowns').\n"
            "2. For general company search, semantic queries, news, press releases, risk factors, or qualitative information, use the RAG search tool 'rag__search'.\n"
            "3. For official US public company filings, financial reports, or company profile facts, use 'sec_edgar__company_facts'.\n"
            "4. For Korean public company financial statements or reports, use 'opendart__income_statements'.\n"
            "5. For macro economy metrics like interest rates or central bank decisions, use 'fred__interest_rates' (US/global) or 'ecos__interest_rates_snapshot' (Korea).\n"
            "6. For a MULTI-INDICATOR macro OVERVIEW of one country (물가·고용·성장·금리 한눈에 / '거시 패널'), use 'fred__macro_panel' — NOT a series of single-indicator calls.\n"
            "7. For a portfolio BACKTEST (종목+비중의 과거 성과: 누적수익·CAGR·MDD), use 'datasets_store__backtest' — NEVER hand-compute from 'yahoo__prices' closes.\n"
            "8. For factor SCREENING (ROE/PER 등 조건으로 종목 찾기), use 'datasets_store__quant_screen'.\n"
            "9. For ANY drawdown question — including the CURRENT one ('현재 낙폭', '고점 대비 몇 %', MDD) — call 'market_history__drawdowns'; NEVER derive a drawdown by hand from prices (the store holds the full peak history, prices calls don't). Same family for regimes/base rates/analogues (과거 낙폭·국면 비교): 'market_history__*'; index names resolve (S&P500→^GSPC, 코스피→^KS11).\n"
            "10. For '어느 섹터가 강하고 약한가 / 섹터 히트맵' use 'yahoo__sector_heatmap' (ALL 11 sectors' moves) — NOT news.\n"
            "11. For a price TREND / CHART / '최근 흐름' use 'yahoo__prices' (a date range of daily bars) — NOT 'yahoo__price_snapshot' (that's one current quote).\n"
            "12. For a MULTI-YEAR ratio TREND (margin/return '추이·몇 년간') use 'datasets_store__metrics_history' — NOT the single-point 'metrics_snapshot'.\n"
            "12b. 어닝 서프라이즈/비트·미스/컨센서스 대비 실적 질문 → fmp__earnings_calendar (분기별 추정 vs 실제 EPS·매출 + 서프라이즈%%, ~50분기).\n"
            "13. For a specific FACT stated in a filing (e.g. 'which supplier fabricates Apple's chips', 위험요소·공급망) use 'rag__search' / 'datasets_store__filing_search' to quote the passage — do NOT answer from general knowledge.\n\n"
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
        if figures:
            # the charts/tables THIS turn actually rendered — the model places each inline in the
            # article with a bare {{figure:N}} line (the UI swaps it for the real artifact).
            system_instruction += (
                "\n\nFigures (place inline with {{figure:N}} on its own line, right after the "
                "paragraph that discusses it; use ONLY these numbers, each at most once):\n"
                + figures
            )
        return system_instruction

    async def stream_final(self, task: str, tools: dict, history: list, system: str | None = None,
                           conversation: list | None = None, sources: str | None = None,
                           figures: str | None = None, synthesis_model: str | None = None):
        """REAL token streaming of the final synthesis (responder model). Yields text deltas
        as Gemini generates them — so the answer appears incrementally, not all at once. Each
        `next()` on the sync stream is offloaded so the event loop stays free."""
        import asyncio
        from google.genai import types

        system_instruction = self._build_system_instruction(system, sources, figures)
        contents = _to_gemini_contents(conversation, history, task)
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=_SYNTHESIS_PROMPT)]))
        config = types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.3)
        model = synthesis_model or settings.synthesis_model or self.model
        # CR-5: the stream OPEN goes through the shared concurrency gate + 429 backoff; per-chunk
        # polling below stays uncapped so streaming isn't serialized.
        it = await generate(model, stream=True, contents=contents, config=config)

        def _next(gen):
            try:
                return next(gen)
            except StopIteration:
                return None

        last_chunk = None
        while True:
            chunk = await asyncio.to_thread(_next, it)
            if chunk is None:
                break
            last_chunk = chunk
            t = getattr(chunk, "text", "") or ""
            if t:
                yield t
        if last_chunk is not None:
            report_usage("synthesis", model, last_chunk)   # usage_metadata rides the final chunk

    async def _run(self, task: str, tools: dict, history: list, system: str | None = None,
                   conversation: list | None = None, force_final: bool = False,
                   sources: str | None = None, figures: str | None = None,
                   synthesis_model: str | None = None) -> list[Decision]:
        import asyncio
        from google.genai import types

        system_instruction = self._build_system_instruction(system, sources, figures)
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
            model = synthesis_model or settings.synthesis_model or self.model
            resp = await generate(model, contents=contents, config=config)
            report_usage("synthesis", model, resp)
            return [Decision(final=_get_text_from_response(resp))]

        decls = [
            types.FunctionDeclaration(name=t["name"], description=t["description"], parameters=_schema(t))
            for t in tools.values()
        ]

        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=decls)],
            system_instruction=system_instruction,
        )

        resp = await generate(self.model, contents=contents, config=config)
        report_usage("plan", self.model, resp)
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
