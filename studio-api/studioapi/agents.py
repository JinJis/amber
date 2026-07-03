"""Configurable agents (F1): provided templates + per-user CRUD.

An agent bundles a model (``stub``/``gemini``), a system prompt, and a set of
data sources (connector ids). When a chat runs with an agent, these become the
``AgentSpec`` sent to the agent engine, so the agent only uses the sources it was
given and follows its prompt.
"""

from __future__ import annotations

import json
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from studioapi.config import settings
from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.orm_helpers import get_owned
from studioapi.models import Agent, User

router = APIRouter(prefix="/agents", tags=["Agents"], dependencies=[Depends(require_service)])


# The single, fully-loaded default agent — Gemini (never stub), every connector activated, and a
# rich system prompt that exercises the whole platform: provenance-first sourcing, live evidence,
# trader charts, news, financials, macro, and the multi-agent reasoning flow. This is what a new
# user lands on so they can test EVERYTHING from one agent.
_DEFAULT_SYSTEM = """\
당신은 **ValueGraph 리서치 데스크**의 수석 애널리스트입니다. 라이선스된 실데이터만으로, 모든 수치에
출처를 달아 답하는 '신뢰가 기본값'인 투자 리서치 도구입니다. 챗봇이 아니라 애널리스트 데스크처럼 동작하세요.

[당신이 쓸 수 있는 데이터 — 전부 게이트웨이로 출처·시점이 보장됨]
- 미국 공시·재무: SEC EDGAR (10-K/10-Q, 재무제표, 회사 정보, as-reported XBRL).
- 한국 공시·재무: OpenDART (사업보고서, 손익/재무/현금흐름, 실적).
- 가격: Yahoo Finance — 미국·한국 일별 OHLCV, 스냅샷, 배당·분할(코퍼레이트 액션).
- 거시: FRED/DBnomics(미국·글로벌 금리·물가·고용), 한국은행 ECOS(한국 금리).
- 뉴스: Google News — 최신 헤드라인(발행사·날짜·링크).
- 의미 검색·증거: RAG — 공시 본문/뉴스 전문에서 인용 문장을 찾아 하이라이트.
- 비교·지표: 동종업계 비교, 재무비율 추이, 기술적 지표(SMA/RSI/MACD·서술적), 슈퍼투자자 13F, ETF 보유.

[일하는 방식 — 멀티에이전트 추론]
1. 질문을 먼저 분석한다(무엇을·어떤 출처가 필요한지). 의도가 모호하면 선택지를 제시해 좁힌다.
2. 단순 개념 질문은 도구 없이 전문 지식으로 간결히 답한다.
3. 복잡·다면 질문은 하위 작업으로 나눠 **병렬로** 각 출처를 동시에 조회한다(가격+재무+공시+뉴스 등).
4. 모은 근거를 교차검증하고, 출처별 신뢰도를 매긴 뒤 하나의 답으로 종합한다.

[답변 원칙]
- 모든 구체적 수치·날짜·사실은 **반드시 위 데이터 출처에서** 가져오고 문장 끝에 [n]으로 인용한다.
  근거 없는 숫자는 절대 지어내지 않는다(없으면 솔직히 "자료 필요"라고 밝힌다).
- 사실(인용된 수치)과 해석(애널리스트 분석)을 자연스럽게 섞되, 분량은 질문에 비례하게 — 단순 질문은 짧게.
- 가능하면 가격은 트레이딩 차트로, 재무·추이는 차트/표로 보여준다(아티팩트). 차트는 출처가 달린 증거다.
- 한국 종목은 종목명으로, 큰 금액은 조/억(원)·$B/$M처럼 읽기 쉽게 표기한다.

[금지 — 가드레일은 신뢰 브랜드]
- 가격 예측·목표가·미래 전망·매수/매도/보유 의견·점수 매기기는 절대 하지 않는다(과거·현재의 서술만).
- 내부 도구·함수 이름(예: opendart__income_statements)이나 원문 URL을 본문에 노출하지 않는다([n]만).
- 답변 언어는 사용자의 질문 언어를 따른다.

당신의 차별점은 (1) 구성적 신뢰(모든 수치에 출처+증거), (2) 풀→푸시, (3) 복제 가능한 분석가 생태계입니다.
"""

TEMPLATES: list[dict] = [
    {
        "id": "tpl_desk", "name": "ValueGraph 리서치 데스크", "is_template": True, "model": "gemini",
        "description": "모든 데이터 소스·증거·차트·멀티에이전트 추론을 한 번에 — Gemini 기반 기본 분석가.",
        "system_prompt": _DEFAULT_SYSTEM,
        # Empty = unrestricted (every tool). User-built agents narrow this to a chosen set of
        # individual tools (fully-qualified ids), grouped by category in the builder.
        "data_sources": [],
    },
]
DEFAULT_AGENT_ID = "tpl_desk"


def seed_templates() -> None:
    """Reset the provided templates to the current set: delete stale templates (the old stub
    ones), then UPSERT each current template (so prompt/model edits land on redeploy). Only
    touches provided templates (user_email is None); user-created agents are never affected."""
    ids = {t["id"] for t in TEMPLATES}
    with SessionLocal() as db:
        for a in db.query(Agent).filter(Agent.is_template.is_(True), Agent.user_email.is_(None)).all():
            if a.id not in ids:
                db.delete(a)  # remove old templates (tpl_research/filings/market/macro)
        for t in TEMPLATES:
            a = db.get(Agent, t["id"])
            if a is None:
                db.add(Agent(
                    id=t["id"], user_email=None, name=t["name"], description=t["description"],
                    model=t["model"], system_prompt=t["system_prompt"],
                    data_sources=json.dumps(t["data_sources"]), is_template=True,
                ))
            else:  # keep it current
                a.name, a.description, a.model = t["name"], t["description"], t["model"]
                a.system_prompt = t["system_prompt"]
                a.data_sources = json.dumps(t["data_sources"])
        db.commit()


# --- schemas --------------------------------------------------------------
class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    model: str = "gemini"  # gemini-only (invariant #7); legacy "stub" is coerced to gemini
    system_prompt: str | None = None
    data_sources: list[str] = []


class AgentPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    data_sources: list[str] | None = None


def _out(a: Agent) -> dict:
    return {
        "id": a.id, "name": a.name, "description": a.description, "model": a.model,
        "system_prompt": a.system_prompt, "data_sources": json.loads(a.data_sources) if a.data_sources else [],
        "is_template": a.is_template, "editable": a.user_email is not None,
    }


def agent_to_spec(a: Agent) -> dict:
    """Map a stored agent to the agent-engine ``AgentSpec`` payload. An empty tool list means
    UNRESTRICTED (every tool) — collapse it to None so the engine applies no filter."""
    tools = json.loads(a.data_sources) if a.data_sources else []
    return {
        "system": a.system_prompt,
        "allowed_tools": tools or None,
        "backend": a.model or None,
    }


def load_agent(db, agent_id: str, email: str) -> Agent | None:
    """Fetch an agent the user may use: their own, or a provided template."""
    a = db.get(Agent, agent_id)
    if a is None or (a.user_email is not None and a.user_email != email):
        return None
    return a


# --- endpoints ------------------------------------------------------------
@router.get("", summary="List provided templates + the user's own agents")
async def list_agents(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Agent).where(or_(Agent.user_email == user.email, Agent.user_email.is_(None)))
            .order_by(Agent.is_template.desc(), Agent.created_at.desc())
        ).scalars().all()
        return {"agents": [_out(a) for a in rows]}


@router.post("", summary="Create an agent")
async def create_agent(body: AgentIn, user: User = Depends(current_user)) -> dict:
    # Gemini-only (invariant #7): any value (incl. legacy "stub") is stored as gemini.
    agent = Agent(
        user_email=user.email, name=body.name, description=body.description, model="gemini",
        system_prompt=body.system_prompt, data_sources=json.dumps(body.data_sources), is_template=False,
    )
    with SessionLocal() as db:
        db.add(agent)
        db.commit()
        return _out(agent)


@router.get("/{agent_id}", summary="Get one agent")
async def get_agent(agent_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = load_agent(db, agent_id, user.email)
        if a is None:
            raise HTTPException(404, "Agent not found.")
        return _out(a)


@router.patch("/{agent_id}", summary="Update an agent (own only)")
async def update_agent(agent_id: str, body: AgentPatch, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = get_owned(db, Agent, agent_id, user.email, "Agent not found or not editable.")  # templates aren't editable
        if body.name is not None:
            a.name = body.name
        if body.description is not None:
            a.description = body.description
        if body.model is not None:
            a.model = "gemini"  # gemini-only (invariant #7); any requested value normalizes here
        if body.system_prompt is not None:
            a.system_prompt = body.system_prompt
        if body.data_sources is not None:
            a.data_sources = json.dumps(body.data_sources)
        db.commit()
        return _out(a)


@router.delete("/{agent_id}", summary="Delete an agent (own only)")
async def delete_agent(agent_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        a = get_owned(db, Agent, agent_id, user.email, "Agent not found or not deletable.")
        db.delete(a)
        db.commit()
        return {"deleted": agent_id}


# --- connectors (data-source picker) --------------------------------------
connectors_router = APIRouter(tags=["Agents"], dependencies=[Depends(require_service)])


@connectors_router.get("/connectors", summary="Tools available to build agents, grouped by category")
async def list_connectors() -> dict:
    """Proxy the catalog and present it as **user-facing categories → individual tools** (not by
    upstream API). Each tool's `name` is the fully-qualified id (`{connector}__{resource}`) the
    agent stores in `data_sources` — so users pick tools, grouped intuitively, never by API."""
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{settings.control_plane_url}/catalog")
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError:
        return {"categories": []}

    cats = body.get("categories") or []
    connectors = body.get("connectors") or []
    # bucket every catalog resource under its category id
    by_cat: dict[str, list[dict]] = {c["id"]: [] for c in cats}
    for con in connectors:
        for r in con.get("resources") or []:
            cat = r.get("category")
            if cat is None:
                continue  # uncategorized tools are never exposed to the builder
            by_cat.setdefault(cat, []).append({
                "name": f"{con['id']}__{r['name']}",  # fully-qualified id stored in data_sources
                "label": r.get("description") or r["name"],
                "description": r.get("description"),
                "source": (r.get("provenance") or {}).get("source"),
                "markets": r.get("markets") or con.get("markets"),
                "connector_name": con.get("name", con["id"]),
            })
    return {"categories": [
        {"id": c["id"], "label": c["label"], "description": c.get("description"),
         "tools": by_cat.get(c["id"], [])}
        for c in cats if by_cat.get(c["id"])
    ]}
