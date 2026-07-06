"""ASK-6 — ask-feed: on-demand per-TICKER deep questions + a background NEWS question feed.

Two scopes, two rhythms:

* ``news_feed`` — studio-api's refresher calls this every ~10 minutes in the background:
  gather the freshest US/KR market headlines (+ an index snapshot for context) through the
  gateway, pick the IMPORTANT news, and synthesize curiosity-provoking question cards. The
  entry screen shows them instantly (pure cache read).
* ``ticker`` — called on demand when the user taps a watchlist ticker on the entry screen
  (never as a background sweep over every watched ticker): a WIDE gather over every angle
  the desk has for that ticker (price·filings·news·valuation·insiders/flows·consensus·
  earnings·drawdown·volatility), then ONE two-stage Gemini pass — per-source candidate
  cards (2–3 each) → curated picks (3–5, kind-diverse, surprise-first). studio-api caches
  per scope with a TTL. (ASK-9: no more same-y 가격/공시/뉴스 triple.)

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
    _SNIPPET_CHARS,
    _SYNTH_SCHEMA,
)
from agentengine.client import PlatformClient

logger = logging.getLogger(__name__)

_TICKER_KINDS = {"filing_deep", "price_context", "news_probe", "history_echo", "fundamental_shift",
                 "valuation", "ownership", "earnings"}
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
    """Wide gather for ONE ticker (ASK-9) — every angle the desk can question from, not just
    price/filings/news: valuation, insiders/flows, consensus, earnings, drawdown, volatility.
    Each entry is tools-guarded, so a missing connector just narrows the pool."""
    t, m = req.ticker, (req.market or "US").upper()
    name = req.name or t
    us = m == "US"
    plan: list[tuple[str, dict, str]] = []
    args = {"ticker": t, "market": m}

    def want(tool: str, why: str) -> None:
        if tool in tools:
            plan.append((tool, dict(args), why))

    want("yahoo__price_snapshot", f"{name} 오늘 가격")
    want("sec_edgar__filings" if us else "opendart__filings", f"{name} 최근 공시")
    want("google_news__news", f"{name} 헤드라인")
    want("sec_edgar__metrics_snapshot" if us else "opendart__metrics_snapshot", f"{name} 밸류에이션 지표")
    want("sec_edgar__insider_trades" if us else "opendart__insider_trades", f"{name} 내부자 거래")
    if not us:
        want("kis__investor_flow", f"{name} 외국인·기관 수급")
    if us:
        want("fmp__consensus_estimates", f"{name} 애널리스트 컨센서스")
        want("fmp__earnings_calendar", f"{name} 어닝 일정·서프라이즈")
    want("market_history__drawdowns", f"{name} 현재 낙폭 맥락")
    want("market_history__vol_context", f"{name} 변동성 위치(과거 대비)")
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
서로 다른 데이터소스 스니펫들입니다([n] 인덱스). 두 단계로 작업하세요.

1단계 — candidates (소스별 후보): 각 스니펫(소스)마다 그 소스의 실제 사실에서 출발하는 분석
카드 후보를 2~3개씩 만드세요. 흥미로운 사실이 없는 소스는 건너뛰어도 됩니다.

2단계 — picks (큐레이션): 후보 전체에서, 이 종목을 지켜보는 사용자가 "오늘 가장 눌러보고
싶을" 카드 {limit}개의 인덱스(0부터, candidates 배열 기준)를 고르세요.
- 다양성 필수: 같은 kind를 2개 이상 뽑지 마세요. 가격·공시·뉴스만 나열하지 말고 밸류에이션·
  수급/내부자·어닝·과거 기록처럼 서로 다른 각도가 섞이게.
- 의외성 우선: 뻔한 것("주가 올랐어요")보다 데이터 속 의외·모순·변화 지점을 고르세요 —
  내부자 매도/매수, 밸류에이션의 과거 대비 위치, 컨센서스와의 괴리, 수급 반전, 새 공시의
  바뀐 위험요소, 변동성의 이례적 위치 같은 것.

카드 규칙 (모두 필수):
- kind는 다음 중 하나: filing_deep(공시 속으로) | price_context(가격 맥락) | news_probe(뉴스 검증)
  | history_echo(과거 기록 대조) | fundamental_shift(재무 변화) | valuation(밸류에이션)
  | ownership(수급·내부자·보유) | earnings(어닝·컨센서스)
- hook: 스니펫의 실제 사실 한 줄 (수치·날짜는 스니펫 그대로; 지어내지 말 것) — 오늘 왜 이걸
  볼 만한지. 예: "오늘 삼성전자가 8.2% 뛰었어요", "임원이 지난주 지분을 줄였어요".
- question: **친근한 초대형 문장**으로 쓸 것. 반드시 해요체로, "~할까요?" 또는 "~볼까요?"로
  끝내고, 사용자를 함께 살펴보자고 이끄는 따뜻한 톤. 딱딱한 "~수준인가?", "~있는가?",
  "~무엇인가?" 금지. 얕은 질문("주가 알려줘") 금지 — 스니펫의 구체 사실에서 출발해 파고들 것.
  좋은 예: "내부자가 지분을 줄였다는데 최근 공시·수급이랑 같이 들여다볼까요?",
  "지금 밸류에이션이 과거 밴드에서 어디쯤인지 함께 살펴볼까요?"
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


# ASK-9: the ticker scope answers in TWO fields — every per-source candidate + the curator's
# picks — so the model provably generates per-source before curating for diversity.
_CARD_ITEM = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "question": {"type": "string"},
        "hook": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "integer"}},
        "ticker": {"type": "string"},
    },
    "required": ["kind", "question", "hook", "sources"],
}
_CURATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": _CARD_ITEM},
        "picks": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["candidates", "picks"],
}


def _loads_obj(raw: str) -> dict:
    """Robust JSON object pull (flash models don't always honor strict JSON mode)."""
    import re as _re
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        m = _re.search(r"\{.*\}", raw or "", _re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else {}
            except (ValueError, TypeError):
                return {}
    return {}


async def _gen_json(prompt: str, schema: dict) -> dict:
    from google.genai import types

    from agentengine.gemini_io import _get_text_from_response, genai_client

    cfg = types.GenerateContentConfig(
        temperature=0.4, max_output_tokens=4096, response_mime_type="application/json",
        response_schema=schema, thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    client = genai_client()
    resp = await asyncio.wait_for(
        asyncio.to_thread(client.models.generate_content,
                          model=settings.budget_model, contents=prompt, config=cfg),
        timeout=settings.gemini_timeout_seconds,
    )
    return _loads_obj(_get_text_from_response(resp) or "")


async def _synthesize(prompt: str) -> list[dict]:
    """news_feed scope — one pass, flat card list."""
    obj = await _gen_json(prompt, _SYNTH_SCHEMA)
    cards = obj.get("cards", [])
    return cards if isinstance(cards, list) else []


async def _synthesize_curated(prompt: str) -> dict:
    """ticker scope (ASK-9) — per-source candidates + the curator's picks."""
    return await _gen_json(prompt, _CURATE_SCHEMA)


def _curate(obj: dict, limit: int) -> list[dict]:
    """Resolve picks → cards with a diversity guard: at most 2 of the same kind, in pick
    order. Falls back to the candidate list when picks are missing/invalid."""
    cands = [c for c in (obj.get("candidates") or []) if isinstance(c, dict)]
    picks = [i for i in (obj.get("picks") or []) if isinstance(i, int) and 0 <= i < len(cands)]
    chosen = [cands[i] for i in picks] if picks else cands
    out: list[dict] = []
    per_kind: dict[str, int] = {}
    for c in chosen:
        k = str(c.get("kind") or "")
        if per_kind.get(k, 0) >= 2:
            continue
        per_kind[k] = per_kind.get(k, 0) + 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


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
        if is_ticker:
            # ASK-9: per-source candidates → curated picks (diverse, surprising angles)
            raw_cards = _curate(await _synthesize_curated(prompt), limit)
        else:
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
