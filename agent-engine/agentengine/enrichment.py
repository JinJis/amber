"""Answer enrichment: follow-up chips + the evidence-refine pass (RF-09, split from agent.py).

Post-answer Gemini passes that run AFTER the orchestration loop produces the answer:
``suggest_followups`` (two parallel personas → diverse, capability-aware chips, with a deterministic
fallback) and ``refine_evidence`` (one reviewer pass → a synthesis brief + per-source confidence).
Both are best-effort and never block the answer. Re-exported via ``agentengine.agent`` for back-compat.
"""

from __future__ import annotations

import json
import logging
import re

from agentengine.config import settings

logger = logging.getLogger(__name__)


# What our platform can uniquely show — the suggester maps follow-ups to these so each click
# experiences a real differentiator (sourced data + the capability), not a generic question.
_CAPABILITY_MENU = (
    "우리 서비스가 특히 잘 보여줄 수 있는 것 (후속 질문이 이걸 자연스럽게 경험시키게):\n"
    "- 출처·증거: 모든 수치에 [n] 출처 + 공시 원문 하이라이트·뉴스 원문 구절 딥링크.\n"
    "- 차트: 가격/캔들·재무 막대·기술지표(SMA/RSI/MACD) 시각화.\n"
    "- 공시 본문 검색: 위험요소·공급망·수요 등 실제 문단 인용.\n"
    "- 밸류에이션 모델: DCF/DDM/RIM (사용자 가정 기반, 예측 아님).\n"
    "- 퀀트 스크리너: 밸류/퀄리티/모멘텀 팩터로 종목 필터·랭킹.\n"
    "- 백테스트: 포트폴리오 과거 성과·CAGR·최대낙폭, '이런 국면 이후 N일 통계'.\n"
    "- 투자거장 13F: 거장 매매·공통 보유.\n"
    "- 한국 수급(KIS 실시간): 외국인/기관 순매수·거래량/등락률/시총 순위·ETF NAV.\n"
    "- 거시: 국가 경제 패널·금리/물가·반도체 생산자물가.\n"
    "- 자산군/원자재/반도체 사이클 프록시.\n"
    "- 컨센서스 추정치·실적 캘린더(FMP, 제3자 데이터).\n"
    "- 종목 내러티브·밸류체인·뉴스 브리핑(구조화 합성).\n"
)

# Two complementary personas, run in PARALLEL (deep model), then merged → diverse, itch-scratching,
# capability-showcasing follow-ups that span beginner→expert.
_FOLLOWUP_PERSONAS = {
    "deepen": (
        "당신은 날카로운 리서치 애널리스트입니다. 방금 답변을 보고 사용자가 '오, 그거 마침 궁금했는데' 하며 바로 누르고 "
        "싶을 심화 질문 3개를 만드세요. 답변에 실제로 등장한 '구체적 수치·기업·사건'을 직접 짚고 그 이면을 파고드세요: "
        "그 수치를 만든 핵심 드라이버('왜 그렇게 됐나'), 데이터 속 의외/모순/긴장 지점, '무엇이 바뀌면 이 그림이 달라지나', "
        "과거 비슷한 국면과의 비교, 한 단계 더 나간 2차 효과. 분석가가 진짜 다음에 던질 법한, 답이 궁금해지는 질문으로."
    ),
    "connect": (
        "당신은 통찰 있는 리서치 멘토입니다. 방금 주제를 '한 발 더 넓혀' 새로운 각을 여는 질문 3개를 만드세요. 답변의 구체 "
        "대상과 연결하되, 관점을 재구성하는 비교(동종업계·과거 사이클), 그 수치를 설명해줄 수급·공시·뉴스·거시 연결고리, "
        "또는 밸류에이션 각도처럼 우리가 출처로 보여줄 수 있는 방향을 쓰세요 — 단, 기능 안내가 아니라 '구체적이고 흥미로운 "
        "질문' 그 자체로. 아래 능력 중 서로 다른 것을 자극하세요.\n"
        + _CAPABILITY_MENU
    ),
}

_FOLLOWUP_PROMPT = (
    "{persona}\n"
    "규칙: 각 질문은 (1) 한국어면 친근한 반말로 — '~보여줘', '~어때?', '~비교해줘' 같은 말투"
    "(사용자가 존댓말을 써도 칩은 항상 반말); 그 외 언어는 사용자 언어로, "
    "(2) 우리 출처 데이터로 답 가능(가격 예측·목표가·매수/매도 의견 요청 금지), "
    "(3) 답변에 실제 등장한 종목·수치·사건을 구체적으로 지목해 자기완결적, (4) 직전 대화 맥락을 이어 더 깊이 들어갈 것"
    "(이미 답한 내용을 처음부터 다시 묻지 말 것), (5) 서로 다른 각도. "
    "'관련 지표/뉴스/섹터를 보여줘', '최근 동향 알려줘' 같은 일반적이고 뻔한 문구는 절대 쓰지 마세요 — 그 답변을 읽은 "
    "사람만 떠올릴 수 있는, 구체적이고 호기심을 자극하는 질문이어야 합니다.\n"
    'JSON만: {{"followups": ["…", "…", "…"]}}\n\n'
    "{conversation}최근 사용자 질문: {task}\n{context}\n답변:\n{answer}"
)


def _merge_followups(lists: list[list[str]], limit: int = 4) -> list[str]:
    """Interleave persona candidate lists, dedup near-duplicates (normalized), cap to `limit` —
    so the final chips are DIVERSE across personas, not three variants of one idea."""
    def norm(s: str) -> str:
        return re.sub(r"[\s\W]+", "", (s or "").lower())[:60]

    out, seen = [], set()
    for i in range(max((len(c) for c in lists), default=0)):
        for cand in lists:
            if i < len(cand):
                s = cand[i].strip()
                k = norm(s)
                if s and k and k not in seen:
                    seen.add(k)
                    out.append(s)
                    if len(out) >= limit:
                        return out
    return out
_FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {"followups": {"type": "array", "items": {"type": "string"}}},
    "required": ["followups"],
}


def _loads_followups(raw: str) -> list[str]:
    """Robustly pull the follow-up list out of a model response — flash models don't reliably honor
    strict JSON mode, so they sometimes wrap it in prose ('Here is the JSON…'), markdown fences, or
    emit a near-JSON variant. Never trust a bare json.loads. Returns [] if nothing parseable."""
    s = re.sub(r"```(?:json)?", "", (raw or "").strip()).strip().strip("`").strip()
    if not s:
        return []

    def _arr(items) -> list[str]:
        return [str(x).strip() for x in items if str(x).strip()] if isinstance(items, list) else []

    try:  # 1) direct: an object with "followups", or a bare array
        d = json.loads(s)
        return _arr(d.get("followups")) if isinstance(d, dict) else _arr(d)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r'"followups"\s*:\s*(\[[^\]]*\])', s, re.S)  # 2) the followups slice
    if m:
        try:
            return _arr(json.loads(m.group(1)))
        except Exception:  # noqa: BLE001
            pass
    m = re.search(r'\[\s*"(?:[^"\\]|\\.)*"(?:\s*,\s*"(?:[^"\\]|\\.)*")*\s*\]', s, re.S)  # 3) any string array
    if m:
        try:
            return _arr(json.loads(m.group(0)))
        except Exception:  # noqa: BLE001
            pass
    return []


async def _followups_one(client, model: str, persona: str, task: str, answer: str, context: str,
                         conversation: str = "", retries: int = 3) -> list[str]:
    """One persona's follow-ups with exponential backoff — rides out transient 429/503/timeouts
    (the two personas fire in parallel, so a paid pro key can momentarily hit per-minute RPM)."""
    import asyncio
    import random

    from google.genai import types

    # thinking_budget=0: gemini-flash-latest is a THINKING model — left on, its reasoning eats the
    # output-token budget and the JSON comes back truncated/empty (→ every chip fell back to the
    # generic set). Off + a roomy budget makes the structured JSON come through cleanly.
    cfg = types.GenerateContentConfig(temperature=0.3, max_output_tokens=1024,
                                      thinking_config=types.ThinkingConfig(thinking_budget=0),
                                      response_mime_type="application/json",
                                      response_schema=_FOLLOWUP_SCHEMA)
    contents = _FOLLOWUP_PROMPT.format(persona=persona, task=(task or "")[:400],
                                       context=context, conversation=conversation,
                                       answer=(answer or "")[:2500])
    last: Exception | None = None
    for i in range(retries):
        try:
            logger.debug("followups[%s/%s]: attempt %d/%d", model, persona, i + 1, retries)
            resp = await asyncio.to_thread(client.models.generate_content, model=model,
                                           contents=contents, config=cfg)
            raw = getattr(resp, "text", "") or ""
            logger.debug("followups[%s/%s]: raw response = %s", model, persona, raw[:500])
            out = _loads_followups(raw)[:3]
            if not out:
                raise ValueError(f"no parseable followups in response: {raw[:160]!r}")
            logger.debug("followups[%s/%s]: parsed %d item(s): %s", model, persona, len(out), out)
            return out
        except Exception as exc:  # noqa: BLE001 — retry transient errors with backoff
            last = exc
            logger.warning("followups[%s/%s]: attempt %d/%d failed: %s: %s",
                           model, persona, i + 1, retries, type(exc).__name__, exc)
            if i < retries - 1:
                delay = 0.7 * (2 ** i) + random.uniform(0, 0.5)
                logger.debug("followups[%s/%s]: backing off %.2fs", model, persona, delay)
                await asyncio.sleep(delay)
    logger.error("followups[%s/%s]: exhausted %d retries", model, persona, retries, exc_info=last)
    raise last if last else RuntimeError("followups: no result")


def _fallback_followups(task: str, tickers: list[str] | None = None,
                        kinds: list[str] | None = None) -> list[str]:
    """Deterministic, capability-aware follow-up chips — used when the LLM suggester can't run
    (stub backend / no key) or returns nothing. GUARANTEES the chips always render: each one still
    leads into a real differentiator (공시·출처·수급·밸류에이션·거시·차트), so the UX never degrades to
    an empty footer. Not hardcoded *reasoning* (invariant #9) — just a safety net for the chip row
    when the deep suggester is unavailable; the gemini path stays primary for quality."""
    tickers = [t for t in (tickers or []) if t]
    out: list[str] = []
    if tickers:
        tk = tickers[0]
        if len(tickers) > 1:
            out.append(f"{tickers[0]}와 {tickers[1]}를 핵심 지표로 비교해줘")
        out += [
            f"{tk}의 최근 공시에서 핵심 내용을 출처와 함께 보여줘",
            f"{tk}의 매출·영업이익 추이를 차트로 보여줘",
            f"{tk}의 외국인·기관 수급 동향은 어때?",
            f"{tk}와 같은 업종 종목들과 밸류에이션을 비교해줘",
        ]
    else:
        out += [
            "관련 거시지표(금리·물가·고용)의 최신값을 출처와 함께 보여줘",
            "이 주제와 관련된 ETF·섹터 동향을 보여줘",
            "관련 기업들의 최근 공시·뉴스를 출처와 함께 정리해줘",
            "방금 답변의 근거 출처를 더 자세히 보여줘",
        ]
    seen, res = set(), []
    for s in out:
        k = re.sub(r"[\s\W]+", "", s.lower())
        if s.strip() and k not in seen:
            seen.add(k)
            res.append(s.strip())
    return res[:4]


async def suggest_followups(task: str, answer: str, model: str, backend: str | None = None,
                            context: str | None = None, tickers: list[str] | None = None,
                            kinds: list[str] | None = None, conversation: str | None = None) -> list[str]:
    """PH-THINK / 고도화: capability-aware follow-up chips. On gemini, runs two personas in PARALLEL —
    one DEEPENS (the sharp analyst's next question, grounded in the answer's specifics), one CONNECTS
    (broadens via comparison / our differentiated data) — then merges to 3-4 DIVERSE suggestions.
    ``conversation`` is a short recent transcript so the chips build ON the thread (심화), not restart.
    ALWAYS returns chips when there's an answer: if the client init fails or every model yields
    nothing, it falls back to deterministic capability-aware chips so the row is never empty."""
    import asyncio

    if not (answer or "").strip():
        return []
    conv = conversation or ""
    eff_backend = backend or settings.llm_backend
    fallback = _fallback_followups(task, tickers, kinds)
    if eff_backend != "gemini":
        logger.info("followups: backend=%s → deterministic fallback (%d chips)", eff_backend, len(fallback))
        return fallback
    ctx = f"맥락: {context}" if context else ""
    # model chain — FLASH only: follow-up chips are short + low-stakes, and using the deep (pro)
    # model here competes with the answer's pro synthesis for the same low pro RPM, which causes
    # rate-limit backoff that stalls the turn. Flash has ample headroom + is plenty for chips.
    chain: list[str] = []
    for m in (model, settings.model, settings.reasoning_model):
        if m and m not in chain:
            chain.append(m)
    try:
        from google import genai

        from agentengine.gemini_io import genai_client
        client = genai_client()  # bounded request timeout (no infinite SSE hang)
    except Exception as exc:  # noqa: BLE001
        logger.warning("followups: genai client init failed (%s: %s) → deterministic fallback",
                       type(exc).__name__, exc, exc_info=True)
        return fallback
    logger.info("followups: generating (chain=%s, personas=%s, answer_len=%d, ctx=%r)",
                chain, list(_FOLLOWUP_PERSONAS), len(answer), context)

    async def _run_chain() -> list[str]:
        for m in chain:
            try:
                results = await asyncio.gather(
                    *[_followups_one(client, m, p, task, answer, ctx, conv) for p in _FOLLOWUP_PERSONAS.values()],
                    return_exceptions=True)
                lists = [r for r in results if isinstance(r, list) and r]
                errs = [r for r in results if isinstance(r, Exception)]
                merged = _merge_followups(lists, limit=4)
                logger.info("followups: model %s → %d/%d personas produced items, %d error(s), merged=%d",
                            m, len(lists), len(results), len(errs), len(merged))
                if merged:
                    logger.info("followups: returning %d chip(s): %s", len(merged), merged)
                    return merged
                if errs:
                    logger.warning("followups: model %s yielded nothing usable; first error: %s: %s; trying next",
                                   m, type(errs[0]).__name__, errs[0])
            except Exception as exc:  # noqa: BLE001 — never block on suggestions
                logger.warning("followups: model %s errored (%s: %s); trying next",
                               m, type(exc).__name__, exc, exc_info=True)
        return []

    # Best-effort + tightly bounded: the answer already streamed, so cap the whole model-chain
    # (incl. backoff/retries) — on timeout, degrade to deterministic chips so `done` fires fast.
    try:
        merged = await asyncio.wait_for(_run_chain(), timeout=settings.gemini_enrich_timeout_seconds)
        if merged:
            return merged
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("followups: exceeded %.0fs cap → deterministic fallback (%d chips)",
                       settings.gemini_enrich_timeout_seconds, len(fallback))
    logger.warning("followups: chain %s produced nothing → deterministic fallback (%d chips)",
                   chain, len(fallback))
    return fallback


_REFINE_PROMPT = (
    "You are a meticulous research reviewer. Given the user's question and the evidence the "
    "agent gathered (each item: [index] source + snippet/figures), return JSON with:\n"
    "- \"brief\": a SHORT synthesis brief (2-4 lines, SAME LANGUAGE as the question) that names "
    "which sources actually answer the question + the key figures to use, flags conflicts/gaps, "
    "and gives a one-line outline for the final answer. Do NOT write the answer, add numbers not "
    "in the evidence, or forecast.\n"
    "- \"sources\": for EACH evidence item, its [index] and a confidence of how well it supports "
    "answering THIS question — \"high\" (direct, specific, on-topic), \"medium\" (partial/indirect), "
    "\"low\" (tangential/weak) — plus a one-line \"why\" in the question's language. This is a "
    "descriptive judgment of evidentiary support, NOT a market prediction.\n\n"
    "Question: {task}\n\nEvidence:\n{ev}"
)

_REFINE_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "object", "properties": {
            "index": {"type": "integer"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "why": {"type": "string"},
        }, "required": ["index", "confidence"]}},
    },
    "required": ["brief"],
}


async def refine_evidence(task: str, citations: list[dict], model: str,
                          backend: str | None = None) -> tuple[str | None, dict[int, dict]]:
    """PH-THINK (verify/refine): ONE reviewer pass over the gathered evidence that both
    (a) writes a short synthesis brief grounding the final answer, and (b) scores each
    source's confidence (how well it supports the question). Returns (brief, {index: {
    confidence, why}}). Gemini-only, best-effort; ('', {}) when stub / no evidence / error."""
    if (backend or settings.llm_backend) != "gemini" or not citations:
        return None, {}
    lines = []
    for c in citations[:12]:
        bit = c.get("snippet") or ""
        if not bit and c.get("table"):
            bit = " · ".join(" ".join(map(str, row)) for row in (c.get("table") or [])[:3])
        lines.append(f"- [{c.get('index')}] {c.get('source') or '?'}: {str(bit)[:220]}")
    ev = "\n".join(lines)
    try:
        import asyncio
        from google import genai
        from google.genai import types

        from agentengine.gemini_io import genai_client
        client = genai_client()  # bounded request timeout (no infinite SSE hang)
        # best-effort verify pass — cap it so it can't delay `done` (TimeoutError → except below)
        resp = await asyncio.wait_for(asyncio.to_thread(
            client.models.generate_content, model=model,
            contents=_REFINE_PROMPT.format(task=(task or "")[:400], ev=ev[:4000]),
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=1024,
                                               thinking_config=types.ThinkingConfig(thinking_budget=0),
                                               response_mime_type="application/json",
                                               response_schema=_REFINE_SCHEMA)),
            timeout=settings.gemini_enrich_timeout_seconds)
        d = json.loads(getattr(resp, "text", "") or "{}")
        brief = (str(d.get("brief") or "")).strip() or None
        scores: dict[int, dict] = {}
        for s in (d.get("sources") or []):
            try:
                idx = int(s.get("index"))
            except (TypeError, ValueError):
                continue
            conf = str(s.get("confidence") or "").lower()
            if conf in ("high", "medium", "low"):
                scores[idx] = {"confidence": conf, "why": (str(s.get("why") or "").strip() or None)}
        return brief, scores
    except Exception as exc:  # noqa: BLE001 — never block the answer on the review pass
        logger.warning("evidence refine failed (%s); skipping", exc)
        return None, {}
