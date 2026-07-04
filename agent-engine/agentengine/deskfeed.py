"""Proactive Desk feed (M-DESK / DK-1) — the turn-zero briefing.

Composes 4–8 suggestion cards ("물어볼 만한 것") for the empty chat: a bounded, parallel tool
gather through the gateway (entitled + metered like every agent call) followed by ONE Gemini
synthesis pass that turns the gathered, sourced facts into cards. Data-hook cards that cite no
gathered source are dropped — no unsourced hooks ever ship (invariant: no number without a
source). Tone is descriptive curiosity ("주목할 변화"), never advice — enforced in the synthesis
prompt and again by the citation-drop rule; the chat guardrail still applies when a card's
question is actually asked.

Two card kinds are *user-state* cards, not data cards, and are built deterministically (the
same accepted pattern as enrichment's fallback follow-ups — UI templating over the user's own
state, not answer logic): ``watchlist_nudge`` (no @groups yet) and ``continue_thread`` (pick up
the latest conversation).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel

from agentengine.client import PlatformClient
from agentengine.config import settings
from agentengine.freshness import compute_freshness
from agentengine.models import Citation
from agentengine.provenance import _canonical_provenance

logger = logging.getLogger(__name__)

# Card kinds that describe the USER's own state (no external datum) — exempt from the
# citation-drop rule. Every other kind must cite at least one gathered source.
_STATE_KINDS = {"watchlist_nudge", "continue_thread"}
_DATA_KINDS = {"price_move", "filing_new", "earnings_upcoming", "econ_calendar", "news_cluster", "market_pulse"}

_MAX_TICKERS = 6          # bounded gather: snapshots for at most this many watchlist tickers
_MAX_FILING_TICKERS = 3   # filings looked up for at most this many
_MAX_NEWS_CALLS = 2
_SNIPPET_CHARS = 1600     # per-source JSON snippet budget in the synthesis prompt


class WatchlistIn(BaseModel):
    name: str
    items: list[dict] = []  # {market, ticker, name?}


class DeskFeedRequest(BaseModel):
    watchlists: list[WatchlistIn] = []
    markets: list[str] | None = None            # user preference, e.g. ["KR","US"]
    since: str | None = None                    # last_seen_at (ISO) — bounds "새로 들어온 공시"
    recent_conversations: list[str] | None = None
    limit: int = 8


class DeskCard(BaseModel):
    kind: str
    question: str                                # tap → composer pre-fill (editable, never auto-send)
    hook: str                                    # one-line sourced fact ("삼성전자 −3.2% · …")
    citations: list[Citation] = []
    deeplink: str | None = None
    ticker: str | None = None
    market: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _pick_tickers(watchlists: list[WatchlistIn], cap: int) -> list[dict]:
    """Round-robin across groups so one giant group doesn't monopolize the gather budget."""
    picked, idx = [], 0
    while len(picked) < cap:
        added = False
        for wl in watchlists:
            if idx < len(wl.items):
                it = wl.items[idx]
                if it.get("ticker") and len(picked) < cap:
                    picked.append({"market": (it.get("market") or "US").upper(),
                                   "ticker": it["ticker"], "name": it.get("name") or it["ticker"],
                                   "group": wl.name})
                    added = True
        if not added:
            break
        idx += 1
    return picked


def _gather_plan(tools: dict[str, dict], req: DeskFeedRequest) -> list[tuple[str, dict, str]]:
    """The bounded tool-call list: (tool_name, args, why). Only tools the tenant is entitled to
    (present in the catalog fetch) are used; anything missing is simply skipped — the feed
    degrades, never errors."""
    plan: list[tuple[str, dict, str]] = []
    tickers = _pick_tickers(req.watchlists, _MAX_TICKERS)

    for t in tickers:
        if "yahoo__price_snapshot" in tools:
            plan.append(("yahoo__price_snapshot", {"ticker": t["ticker"], "market": t["market"]},
                         f"{t['name']} 오늘 가격"))
    for t in tickers[:_MAX_FILING_TICKERS]:
        tool = "sec_edgar__filings" if t["market"] == "US" else "opendart__filings"
        if tool in tools:
            plan.append((tool, {"ticker": t["ticker"], "market": t["market"]}, f"{t['name']} 최근 공시"))
    for t in tickers[:_MAX_NEWS_CALLS]:
        if "google_news__news" in tools:
            plan.append(("google_news__news", {"ticker": t["ticker"], "market": t["market"]},
                         f"{t['name']} 헤드라인"))
    if tickers and "fmp__earnings_calendar" in tools:
        us = next((t for t in tickers if t["market"] == "US"), None)
        if us:
            plan.append(("fmp__earnings_calendar", {"ticker": us["ticker"], "market": "US"},
                         f"{us['name']} 어닝 일정"))
    # market-wide pulse — always useful, and the whole feed for a no-watchlist user
    if "yahoo__asset_classes" in tools:
        plan.append(("yahoo__asset_classes", {}, "지수·금리·원자재 스냅샷"))
    if not tickers and "google_news__news" in tools:
        mkt = (req.markets or ["US"])[0].upper()
        anchor = "005930" if mkt == "KR" else "AAPL"
        plan.append(("google_news__news", {"ticker": anchor, "market": mkt}, "시장 헤드라인"))
    return plan


async def _gather(client: PlatformClient, tools: dict[str, dict],
                  plan: list[tuple[str, dict, str]]) -> list[dict]:
    """Run the plan in parallel; keep only 200s. Each result: {idx, tool, why, data, citation}."""

    async def one(name: str, args: dict, why: str) -> dict | None:
        try:
            res = await client.call_tool(tools[name], args)
        except Exception as exc:  # noqa: BLE001 — a dead upstream never sinks the feed
            logger.warning("desk-feed gather %s failed: %s", name, exc)
            return None
        if res.get("status") != 200:
            return None
        data = res.get("data")
        as_of = data.get("as_of") if isinstance(data, dict) else None
        url, _accn, _cik = _canonical_provenance(data)
        cite = Citation(
            tool=name, source=tools[name].get("source") or tools[name].get("connector_name"),
            url=url, as_of=as_of, freshness=compute_freshness(as_of),
            cadence=tools[name].get("cadence"), category=tools[name].get("category"),
            kind="data", ticker=args.get("ticker"), used=True,
        )
        return {"tool": name, "args": args, "why": why, "data": data, "citation": cite}

    results = await asyncio.gather(*(one(n, a, w) for n, a, w in plan))
    out = []
    for r in results:
        if r is not None:
            r["idx"] = len(out) + 1
            out.append(r)
    return out


_SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "question": {"type": "string"},
                    "hook": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "integer"}},
                    "ticker": {"type": "string"},
                },
                "required": ["kind", "question", "hook", "sources"],
            },
        }
    },
    "required": ["cards"],
}

_SYNTH_PROMPT = """당신은 리서치 데스크의 아침 브리핑 편집자입니다. 아래는 사용자의 관심 그룹과,
방금 실제 데이터 소스에서 가져온 스니펫들입니다([n] 인덱스). 사용자가 채팅에서 "물어볼 만한
질문" 카드를 {limit}개 이내로 만드세요.

규칙 (모두 필수):
- kind는 다음 중 하나: price_move | filing_new | earnings_upcoming | econ_calendar | news_cluster | market_pulse
- hook: 스니펫의 실제 사실 한 줄 (수치·날짜는 스니펫에 있는 그대로; 지어내지 말 것).
- question: 우리 도구로 사실을 조회해 답할 수 있는 구체적 질문 (한국어, 1문장). 반드시
  사실 조회형("무엇/얼마/추이/최근 공시 내용/원문 보기")으로. 해석·의견·전망을 요구하는
  표현("어떻게 해석할까", "어떻게 봐야 할까", "의미는", "전망은", "어떻게 될까")은 금지.
- sources: hook의 근거 스니펫 인덱스 배열 — 근거 없는 카드는 만들지 말 것.
- 톤은 기술적 호기심("주목할 변화", "확인해보기"). 절대 금지: 매수/매도/보유 조언, 전망,
  목표가, "기회", "추천" 류 표현, 그리고 질문에서의 해석·전망 요구.
- 같은 종목·주제로 카드를 중복 생성하지 말 것.

사용자 컨텍스트:
{context}

데이터 스니펫:
{snippets}
"""


def _parse_cards(raw: str) -> list[dict]:
    """Robust JSON pull — flash models don't always honor strict JSON mode (same pattern as
    enrichment._loads_followups)."""
    try:
        obj = json.loads(raw)
        return obj.get("cards", []) if isinstance(obj, dict) else []
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", raw or "", re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj.get("cards", []) if isinstance(obj, dict) else []
            except (ValueError, TypeError):
                return []
    return []


async def _synthesize(req: DeskFeedRequest, gathered: list[dict]) -> list[dict]:
    """One Gemini pass (cheap tier) → raw card dicts. Raises on any failure — caller falls back."""
    from google.genai import types

    from agentengine.gemini_io import _get_text_from_response, genai_client

    context = {
        "관심그룹": [{"name": w.name, "tickers": [i.get("ticker") for i in w.items]} for w in req.watchlists],
        "시장": req.markets or [],
        "마지막 방문": req.since,
    }
    snippets = "\n".join(
        f"[{g['idx']}] {g['why']} · 출처 {g['citation'].source} · 도구 {g['tool']}\n"
        f"{json.dumps(g['data'], ensure_ascii=False, default=str)[:_SNIPPET_CHARS]}"
        for g in gathered
    )
    prompt = _SYNTH_PROMPT.format(limit=max(3, min(req.limit, 8)),
                                  context=json.dumps(context, ensure_ascii=False),
                                  snippets=snippets or "(없음)")
    cfg = types.GenerateContentConfig(
        temperature=0.3, max_output_tokens=2048, response_mime_type="application/json",
        response_schema=_SYNTH_SCHEMA, thinking_config=types.ThinkingConfig(thinking_budget=0),
    )
    client = genai_client()
    resp = await asyncio.wait_for(
        asyncio.to_thread(client.models.generate_content,
                          model=settings.budget_model, contents=prompt, config=cfg),
        timeout=settings.gemini_timeout_seconds,
    )
    return _parse_cards(_get_text_from_response(resp) or "")


def _fallback_cards(gathered: list[dict], limit: int) -> list[dict]:
    """Deterministic degrade when Gemini is unavailable (no key / timeout) — one plain card per
    gathered source, hook = the source's label. Same role as enrichment's fallback follow-ups:
    the feed stays sourced and useful, never empty or fabricated."""
    kind_by_tool = {"price_snapshot": "price_move", "filings": "filing_new", "news": "news_cluster",
                    "earnings_calendar": "earnings_upcoming", "asset_classes": "market_pulse"}
    out = []
    for g in gathered[:limit]:
        resource = g["tool"].split("__", 1)[-1]
        kind = kind_by_tool.get(resource, "market_pulse")
        tick = g["args"].get("ticker")
        subject = tick or "시장"
        question = {
            "price_move": f"{subject} 오늘 가격 흐름 보여줘",
            "filing_new": f"{subject} 최근 공시에 뭐가 들어있어?",
            "news_cluster": f"{subject} 최근 뉴스 정리해줘",
            "earnings_upcoming": f"{subject} 어닝 일정과 컨센서스 알려줘",
            "market_pulse": "오늘 지수·금리·원자재 시황 정리해줘",
        }[kind]
        out.append({"kind": kind, "question": question, "hook": g["why"], "sources": [g["idx"]],
                    "ticker": tick})
    return out


def _state_cards(req: DeskFeedRequest) -> list[DeskCard]:
    """User-state cards (no external datum → no citation required): nudge + continue-thread."""
    cards: list[DeskCard] = []
    if not req.watchlists:
        cards.append(DeskCard(
            kind="watchlist_nudge",
            question="관심그룹 만들기",
            hook="관심그룹을 만들면 데스크가 매일 아침 이 자리를 채워둡니다.",
        ))
    if req.recent_conversations:
        title = (req.recent_conversations[0] or "").strip()
        if title:
            cards.append(DeskCard(
                kind="continue_thread",
                question=f"{title} — 이어서 더 파고들기",
                hook=f"지난 대화: {title}",
            ))
    return cards


async def build_desk_feed(req: DeskFeedRequest, api_key: str | None) -> dict:
    """The DK-1 entrypoint: gather → synthesize → citation-drop → cards."""
    client = PlatformClient(api_key)
    try:
        tools = await client.fetch_tools()
    except Exception as exc:  # noqa: BLE001 — gateway down → state cards only, never a 500
        logger.warning("desk-feed catalog unavailable: %s", exc)
        return {"cards": [c.model_dump() for c in _state_cards(req)],
                "generated_at": _now_iso(), "used_tools": []}

    gathered = await _gather(client, tools, _gather_plan(tools, req))

    raw_cards: list[dict] = []
    if gathered:
        try:
            raw_cards = await _synthesize(req, gathered)
        except Exception as exc:  # noqa: BLE001 — LLM unavailable → deterministic degrade
            logger.warning("desk-feed synthesis unavailable (%s) — fallback cards", type(exc).__name__)
        if not raw_cards:
            raw_cards = _fallback_cards(gathered, req.limit)

    by_idx = {g["idx"]: g for g in gathered}
    state = _state_cards(req)
    data_cards: list[DeskCard] = []
    for rc in raw_cards:
        kind = rc.get("kind") or ""
        if kind not in _DATA_KINDS:
            continue  # the LLM may not mint state kinds (or novel kinds) — those are ours
        cites = [by_idx[i]["citation"] for i in rc.get("sources") or [] if i in by_idx]
        if not cites:
            continue  # citation-drop rule: an unsourced hook never ships
        # QT-2: the hook's numerals must trace to the cited sources' actual data — a card with an
        # invented number never ships (stronger than the citation-drop: right source, wrong figure).
        from agentengine.audit import audit_answer
        hook_audit = audit_answer(rc.get("hook") or "",
                                  [by_idx[i]["data"] for i in rc.get("sources") or [] if i in by_idx])
        if hook_audit["unsupported"]:
            logger.warning("desk-feed card dropped (unsupported figures %s): %s",
                           hook_audit["unsupported"], rc.get("hook"))
            continue
        data_cards.append(DeskCard(
            kind=kind, question=(rc.get("question") or "").strip(), hook=(rc.get("hook") or "").strip(),
            citations=cites, ticker=rc.get("ticker") or cites[0].ticker,
            deeplink=next((c.url for c in cites if c.url), None),
        ))
        if len(data_cards) >= req.limit:
            break

    return {"cards": [c.model_dump() for c in state + data_cards],
            "generated_at": _now_iso(),
            "used_tools": [g["tool"] for g in gathered]}
