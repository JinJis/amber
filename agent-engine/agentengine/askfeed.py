"""ASK-6 — ask-feed: on-demand per-TICKER deep questions + a background NEWS question feed.

Two scopes, two rhythms:

* ``news_feed`` — studio-api's refresher calls this every ~10 minutes in the background:
  gather the freshest US/KR market headlines (+ an index snapshot for context) through the
  gateway, pick the IMPORTANT news, and synthesize curiosity-provoking question cards. The
  entry screen shows them instantly (pure cache read).
* ``ticker`` — called on demand when the user taps a watchlist ticker on the entry screen
  (never as a background sweep over every watched ticker): gather that ticker's latest
  records and synthesize ~3 deep-dive cards. studio-api caches per scope with a TTL.

Both reuse the desk-feed machinery: bounded gather, signature check (unchanged data → no
LLM spend), citation-drop, QT-2 hook audit (an invented number never ships). Generation is
per SCOPE, never per user — any user tapping the same ticker shares the pool.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from pydantic import BaseModel

from agentengine.config import settings
from agentengine.deskfeed import (
    DeskCard,
    _gather,
    _now_iso,
    _parse_cards,
    _SNIPPET_CHARS,
    _SYNTH_SCHEMA,
)
from agentengine.client import PlatformClient

logger = logging.getLogger(__name__)

_TICKER_KINDS = {"filing_deep", "price_context", "news_probe", "history_echo", "fundamental_shift"}
_NEWS_KINDS = {"macro", "micro", "market"}


class AskFeedRequest(BaseModel):
    scope: str                       # "ticker" | "news_feed" ("hot_trend" accepted as legacy alias)
    market: str | None = None        # scope=ticker
    ticker: str | None = None        # scope=ticker
    name: str | None = None          # display name for the prompt
    prev_signature: str | None = None  # skip Gemini when the gathered data hasn't changed
    limit: int = 5


def _signature(gathered: list[dict]) -> str:
    """A stable digest of WHAT data exists (not its prose): per source — tool + as_of + the
    provenance ids that change when new records land (accession/url/date lists)."""
    parts = []
    for g in sorted(gathered, key=lambda x: x["tool"]):
        c = g["citation"]
        head = json.dumps(g["data"], ensure_ascii=False, default=str, sort_keys=True)[:400]
        parts.append(f"{g['tool']}|{c.as_of}|{hashlib.sha256(head.encode()).hexdigest()[:16]}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _ticker_plan(tools: dict[str, dict], req: AskFeedRequest) -> list[tuple[str, dict, str]]:
    """Bounded gather for ONE ticker — the freshest primary records a deep question can cite."""
    t, m = req.ticker, (req.market or "US").upper()
    name = req.name or t
    plan: list[tuple[str, dict, str]] = []
    if "yahoo__price_snapshot" in tools:
        plan.append(("yahoo__price_snapshot", {"ticker": t, "market": m}, f"{name} 오늘 가격"))
    filings = "sec_edgar__filings" if m == "US" else "opendart__filings"
    if filings in tools:
        plan.append((filings, {"ticker": t, "market": m}, f"{name} 최근 공시"))
    if "google_news__news" in tools:
        plan.append(("google_news__news", {"ticker": t, "market": m}, f"{name} 헤드라인"))
    if m == "US" and "fmp__earnings_calendar" in tools:
        plan.append(("fmp__earnings_calendar", {"ticker": t, "market": m}, f"{name} 어닝 일정"))
    if "market_history__drawdown_now" in tools:
        plan.append(("market_history__drawdown_now", {"ticker": t, "market": m}, f"{name} 현재 낙폭 맥락"))
    return plan


def _news_plan(tools: dict[str, dict]) -> list[tuple[str, dict, str]]:
    """News-first global gather — broad real-time market headlines (no ticker → the provider
    returns market-wide news) + one index snapshot so the editor has price context."""
    plan: list[tuple[str, dict, str]] = []
    if "google_news__news" in tools:
        plan.append(("google_news__news", {"market": "US", "limit": 8}, "미국 시장 실시간 헤드라인"))
        plan.append(("google_news__news", {"market": "KR", "limit": 8}, "한국 시장 실시간 헤드라인"))
    if "yahoo__asset_classes" in tools:
        plan.append(("yahoo__asset_classes", {}, "지수·금리·원자재·환율 스냅샷"))
    return plan


_TICKER_PROMPT = """당신은 리서치 데스크의 선임 애널리스트입니다. 아래는 {name}의 방금 수집된
최신 기록 스니펫들입니다([n] 인덱스). 이 종목을 지켜보는 사용자에게 "오늘 같이 파볼 만한"
깊이 있는 분석 카드를 {limit}개 이내로 만드세요.

규칙 (모두 필수):
- kind는 다음 중 하나: filing_deep(공시 속으로) | price_context(가격 맥락) | news_probe(뉴스 검증)
  | history_echo(과거 기록 대조) | fundamental_shift(재무 변화)
- hook: 스니펫의 실제 사실 한 줄 (수치·날짜는 스니펫 그대로; 지어내지 말 것) — 오늘 왜 이걸
  볼 만한지. 예: "오늘 삼성전자가 8.2% 뛰었어요", "6/28 새 8-K가 접수됐어요".
- question: **친근한 초대형 문장**으로 쓸 것. 반드시 해요체로, "~할까요?" 또는 "~볼까요?"로
  끝내고, 사용자를 함께 살펴보자고 이끄는 따뜻한 톤. 딱딱한 "~수준인가?", "~있는가?",
  "~무엇인가?" 금지. 얕은 질문("주가 알려줘") 금지 — 스니펫의 구체 사실에서 출발해 파고들 것.
  좋은 예: "오늘 8% 넘게 뛰었는데, 최근 공시랑 뉴스로 왜 그런지 같이 알아볼까요?",
  "이번 낙폭이 과거 급락들과 비교하면 어느 정도인지 함께 살펴볼까요?",
  "새로 올라온 공시에 바뀐 위험요소가 있는지 들여다볼까요?"
  (question은 그 자체로 에이전트가 도구로 답할 수 있는 실제 요청이어야 함)
- sources: hook의 근거 스니펫 인덱스 배열 — 근거 없는 카드 금지.
- 절대 금지: 매수/매도 조언, 전망, 목표가, "기회"·"추천" 류.

데이터 스니펫:
{snippets}
"""

_NEWS_PROMPT = """당신은 리서치 데스크의 시황 에디터입니다. 아래는 방금 수집된 미국·한국 시장의
실시간 뉴스 헤드라인과 시장 스냅샷입니다([n] 인덱스). 이 중 **투자 리서치 관점에서 정말 중요한
뉴스**만 골라, 사용자가 "궁금해서 눌러보고 싶어지는" 질문 카드를 {limit}개 이내로 만드세요.
사소한 잡음(단순 시황 중계, 광고성 기사, 중복 보도)은 버리세요.

규칙 (모두 필수):
- kind는 다음 중 하나: macro(금리·물가·환율·정책) | micro(기업 실적·공시·산업) | market(지수·수급·변동성)
- hook: 스니펫의 실제 사실 한 줄 (수치·날짜는 스니펫 그대로; 지어내지 말 것). 제목 역할 —
  구체적일 것. 예: "미 CPI가 5월에 3.1%로 나왔어요", "코스피가 오늘 5.8% 급등했어요".
- question: **친근한 초대형 문장**으로 쓸 것. 해요체로 "~할까요?"/"~볼까요?"로 끝내고 함께
  살펴보자는 톤. 딱딱한 "~인가?"/"~있는가?" 금지. 그 뉴스를 우리 도구(가격·공시·거시 데이터)로
  더 파볼 수 있는 실제 요청이어야 함. 예: "이 소식이 나온 뒤 주가가 어떻게 움직였는지 같이 볼까요?"
- sources: 근거 스니펫 인덱스 배열 — 근거 없는 카드 금지.
- 절대 금지: 방향 예측, 조언, "기회" 류. "이런 데이터가 나왔다"까지만.

데이터 스니펫:
{snippets}
"""


async def _synthesize(prompt: str) -> list[dict]:
    from google.genai import types

    from agentengine.gemini_io import _get_text_from_response, genai_client

    cfg = types.GenerateContentConfig(
        temperature=0.4, max_output_tokens=2048, response_mime_type="application/json",
        response_schema=_SYNTH_SCHEMA, thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    client = genai_client()
    resp = await asyncio.wait_for(
        asyncio.to_thread(client.models.generate_content,
                          model=settings.budget_model, contents=prompt, config=cfg),
        timeout=settings.gemini_timeout_seconds,
    )
    return _parse_cards(_get_text_from_response(resp) or "")


def _snippets(gathered: list[dict]) -> str:
    return "\n".join(
        f"[{g['idx']}] {g['why']} · 출처 {g['citation'].source} · 도구 {g['tool']}\n"
        f"{json.dumps(g['data'], ensure_ascii=False, default=str)[:_SNIPPET_CHARS]}"
        for g in gathered
    )


async def build_ask_feed(req: AskFeedRequest, api_key: str | None) -> dict:
    """Gather → signature check (skip Gemini when nothing changed) → synthesize → audit → cards."""
    client = PlatformClient(api_key)
    try:
        tools = await client.fetch_tools()
    except Exception as exc:  # noqa: BLE001 — gateway down → empty, refresher retries next tick
        logger.warning("ask-feed catalog unavailable: %s", exc)
        return {"cards": [], "signature": None, "unchanged": False, "generated_at": _now_iso()}

    is_ticker = req.scope == "ticker"
    plan = _ticker_plan(tools, req) if is_ticker else _news_plan(tools)
    gathered = await _gather(client, tools, plan)
    if not gathered:
        return {"cards": [], "signature": None, "unchanged": False, "generated_at": _now_iso()}

    sig = _signature(gathered)
    if req.prev_signature and sig == req.prev_signature:
        # the records didn't change → the previous cards still hold; no LLM spend.
        return {"cards": [], "signature": sig, "unchanged": True, "generated_at": _now_iso()}

    limit = max(3, min(req.limit, 6))
    prompt = (_TICKER_PROMPT.format(name=req.name or req.ticker, limit=limit, snippets=_snippets(gathered))
              if is_ticker
              else _NEWS_PROMPT.format(limit=limit, snippets=_snippets(gathered)))
    raw_cards: list[dict] = []
    try:
        raw_cards = await _synthesize(prompt)
    except Exception as exc:  # noqa: BLE001 — no fabricated fallback here: the entry screen
        # simply keeps the previous generation (honesty over fake data).
        logger.warning("ask-feed synthesis unavailable (%s)", type(exc).__name__)
        return {"cards": [], "signature": None, "unchanged": False, "generated_at": _now_iso()}

    allowed = _TICKER_KINDS if is_ticker else _NEWS_KINDS
    by_idx = {g["idx"]: g for g in gathered}
    from agentengine.audit import audit_answer
    cards: list[DeskCard] = []
    for rc in raw_cards:
        if (rc.get("kind") or "") not in allowed:
            continue
        cites = [by_idx[i]["citation"] for i in rc.get("sources") or [] if i in by_idx]
        if not cites:
            continue  # citation-drop: an unsourced hook never ships
        hook_audit = audit_answer(rc.get("hook") or "",
                                  [by_idx[i]["data"] for i in rc.get("sources") or [] if i in by_idx])
        if hook_audit["unsupported"]:
            logger.warning("ask-feed card dropped (unsupported figures %s): %s",
                           hook_audit["unsupported"], rc.get("hook"))
            continue
        cards.append(DeskCard(
            kind=rc["kind"], question=(rc.get("question") or "").strip(),
            hook=(rc.get("hook") or "").strip(), citations=cites,
            ticker=rc.get("ticker") or (req.ticker if is_ticker else cites[0].ticker),
            market=req.market, deeplink=next((c.url for c in cites if c.url), None),
        ))
        if len(cards) >= limit:
            break

    return {"cards": [c.model_dump() for c in cards], "signature": sig,
            "unchanged": False, "generated_at": _now_iso()}
