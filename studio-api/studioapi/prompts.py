"""Prompt library (F2): a personal collection + a seeded community catalog.

A prompt is reusable text a user can drop into the composer to start a message.
Community prompts (``user_email is None``) are read-only; importing one creates an
editable personal copy that records its ``source_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from studioapi.db import SessionLocal
from studioapi.deps import current_user, require_service
from studioapi.models import Prompt, User
from studioapi.orm_helpers import idempotent_clone

router = APIRouter(prefix="/prompts", tags=["Prompts"], dependencies=[Depends(require_service)])


# --- seeded community catalog (user_email = None; fixed ids for idempotency) ---
COMMUNITY: list[dict] = [
    {
        "id": "cpr_earnings", "title": "실적 요약", "category": "리서치",
        "description": "한 종목의 최근 분기 실적을 핵심 지표 중심으로 정리.",
        "body": "{TICKER}의 가장 최근 분기 실적을 매출·영업이익·순이익·EPS 중심으로 요약하고, "
                "전년 동기 대비 증감과 함께 각 수치의 출처(공시)를 표시해줘.",
    },
    {
        "id": "cpr_filings_risk", "title": "리스크 공시 점검", "category": "리서치",
        "description": "최근 공시에서 드러난 사업·공급망 리스크를 출처와 함께 정리.",
        "body": "{TICKER}의 최근 공시(10-K/사업보고서)에서 언급된 주요 사업·공급망·규제 리스크를 "
                "항목별로 정리하고, 각 항목을 해당 공시 문구에 근거해 출처와 함께 보여줘.",
    },
    {
        "id": "cpr_macro_rates", "title": "금리·물가 점검", "category": "거시",
        "description": "미국/한국의 최근 정책금리와 물가 지표를 as-of 날짜와 함께.",
        "body": "미국(FRED)과 한국(ECOS)의 최근 기준금리와 소비자물가 지표를 각각 가져와서, "
                "각 수치의 기준일(as-of)과 함께 한 줄씩 정리해줘. 예측은 하지 말고 사실만.",
    },
    {
        "id": "cpr_holdings_news", "title": "보유 종목 뉴스 브리핑", "category": "뉴스",
        "description": "관심 종목들의 최근 뉴스를 출처 링크와 함께 묶어서.",
        "body": "{TICKERS} 각 종목의 최근 주요 뉴스를 2~3개씩 골라 한 줄 요약과 출처 링크를 붙여줘. "
                "점수·전망·매수의견은 넣지 말고 사실 위주로.",
    },
    {
        "id": "cpr_price_trend", "title": "가격 흐름 설명", "category": "시황",
        "description": "최근 가격 흐름을 사실 기반으로 설명(예측 없음).",
        "body": "{TICKER}의 최근 가격 흐름(시작가/종가/등락)을 사실 기반으로 설명해줘. "
                "앞으로의 방향이나 목표가는 절대 제시하지 말고, 무엇이 있었는지만.",
    },
    # --- 재무제표 (SEC EDGAR / OpenDART) ---
    {
        "id": "cpr_income", "title": "손익계산서", "category": "재무",
        "description": "최근 연도 손익계산서 주요 항목을 출처와 함께.",
        "body": "{TICKER}의 최근 연간 손익계산서를 매출·매출원가·영업이익·순이익 중심으로 정리하고, "
                "각 수치를 공시(SEC EDGAR / OpenDART) 출처와 회계기간과 함께 표로 보여줘.",
    },
    {
        "id": "cpr_balance", "title": "재무상태표", "category": "재무",
        "description": "자산·부채·자본 구조를 최근 공시 기준으로.",
        "body": "{TICKER}의 최근 재무상태표를 총자산·총부채·자본총계와 주요 항목(현금성자산, 차입금) 중심으로 "
                "정리하고, 각 수치를 공시 출처·기준일과 함께 보여줘.",
    },
    {
        "id": "cpr_cashflow", "title": "현금흐름표", "category": "재무",
        "description": "영업/투자/재무활동 현금흐름 요약.",
        "body": "{TICKER}의 최근 현금흐름표에서 영업활동·투자활동·재무활동 현금흐름과 잉여현금흐름(FCF)을 "
                "정리하고 각 수치의 공시 출처를 표시해줘. 전망은 하지 말고 보고된 사실만.",
    },
    {
        "id": "cpr_asreported", "title": "원문 그대로(as-reported)", "category": "재무",
        "description": "공시 XBRL 원문 개념을 가공 없이 확인.",
        "body": "{TICKER}의 최근 공시에서 보고된 us-gaap 원문 개념(as-reported)을 기간별로 보여줘. "
                "표준화·가공하지 말고 공시에 적힌 라벨 그대로, 출처와 함께.",
    },
    # --- 비교·지표 ---
    {
        "id": "cpr_metrics", "title": "밸류에이션 지표", "category": "재무",
        "description": "PER·PBR·ROE 등 파생 지표 스냅샷(출처 기반).",
        "body": "{TICKER}의 최근 밸류에이션·수익성 지표(PER, PBR, ROE, 영업이익률, 순이익률)를 "
                "파생 계산값으로 정리하고, 기반이 된 가격·재무 출처를 함께 표시해줘.",
    },
    {
        "id": "cpr_comparables", "title": "동종업계 멀티플 비교", "category": "재무",
        "description": "여러 종목의 밸류에이션 멀티플을 한 표로 비교.",
        "body": "{TICKERS} 종목들의 밸류에이션 멀티플(PER 등)을 한 표로 나란히 비교해줘. "
                "각 수치는 SEC/가격 기반 파생값으로, 목표가·매수의견 없이 비교만.",
    },
    {
        "id": "cpr_metrics_history", "title": "마진 추이 차트", "category": "차트",
        "description": "매출총이익률/영업이익률/순이익률 추이를 차트로.",
        "body": "{TICKER}의 매출총이익률·영업이익률·순이익률 추이를 차트로 보여줘. "
                "각 비율은 보고된 재무에서 계산한 값이며 출처를 표시해줘. 전망선은 그리지 말 것.",
    },
    # --- 가격·차트 (Yahoo / TradingView) ---
    {
        "id": "cpr_candles", "title": "캔들 차트", "category": "차트",
        "description": "일봉 OHLCV 트레이딩 차트로 가격 확인.",
        "body": "{TICKER}의 최근 가격을 일봉 캔들 차트(OHLCV)로 보여줘. 표로 전환해 시·고·저·종가와 "
                "거래량도 볼 수 있게. 예측·목표가 없이 과거 데이터만.",
    },
    {
        "id": "cpr_technical", "title": "기술적 지표 오버레이", "category": "차트",
        "description": "SMA/RSI/MACD를 차트에 서술적으로 표시.",
        "body": "{TICKER} 가격 차트에 SMA(20)/SMA(50), 그리고 RSI(14)·MACD를 함께 보여줘. "
                "매수/매도 신호나 전망은 하지 말고, 계산값(출처: 가격)을 서술적으로만.",
    },
    {
        "id": "cpr_annotate", "title": "차트 추세선 표시", "category": "차트",
        "description": "최근 저점→고점 추세선·주요 레벨을 차트에 표시.",
        "body": "{TICKER} 차트에서 최근 의미 있는 저점에서 고점까지 추세선을 긋고, 눈에 띄는 가격 레벨을 "
                "표시해줘. 미래 구간 투영이나 목표가는 절대 그리지 말고 과거 구간만.",
    },
    {
        "id": "cpr_compare", "title": "여러 종목 가격 비교", "category": "차트",
        "description": "여러 종목의 상대 수익률을 % 기준으로 비교.",
        "body": "{TICKERS}의 최근 가격을 한 차트에서 시작점=100 기준 % 변화로 비교해줘. "
                "사실 기반의 상대 흐름만, 전망·추천 없이.",
    },
    {
        "id": "cpr_dividends", "title": "배당·분할 이력", "category": "시황",
        "description": "배당락일·금액과 액면분할 이력을 출처와 함께.",
        "body": "{TICKER}의 최근 배당(배당락일+주당 금액)과 액면분할 이력을 Yahoo 출처로 정리하고, "
                "가능하면 차트의 해당 날짜에 표시해줘. 배당 예측은 하지 말 것.",
    },
    # --- 거시 (FRED/DBnomics/ECOS) ---
    {
        "id": "cpr_cpi", "title": "미국 물가(CPI)", "category": "거시",
        "description": "최근 CPI/근원CPI 관측치를 기준일과 함께.",
        "body": "미국의 최근 CPI와 근원 CPI 관측치를 기간·값과 함께 정리해줘(출처: FRED/DBnomics). "
                "인플레이션 전망·예측은 하지 말고 발표된 수치만.",
    },
    {
        "id": "cpr_econ", "title": "경제지표 대시보드", "category": "거시",
        "description": "실업률·GDP 등 핵심 경제지표 최근치.",
        "body": "미국의 실업률·비농업고용·GDP 성장률 등 핵심 경제지표의 최근 관측치를 각각 기준일과 함께 "
                "한 줄씩 정리해줘(DBnomics). 예측 없이 사실만.",
    },
    # --- 뉴스 ---
    {
        "id": "cpr_news_one", "title": "단일 종목 뉴스", "category": "뉴스",
        "description": "한 종목의 최신 헤드라인을 발행사·날짜·링크로.",
        "body": "{TICKER}의 최근 주요 뉴스 3~5개를 발행사·날짜와 함께 한 줄 요약하고 출처 링크를 붙여줘. "
                "점수·전망·매수의견 없이 맥락 정보로만.",
    },
    # --- RAG · 증거 · provenance ---
    {
        "id": "cpr_rag_topic", "title": "공시 본문 의미 검색", "category": "증거",
        "description": "특정 주제를 다룬 공시 문구를 의미 기반으로 검색.",
        "body": "{TICKER}의 공시에서 '공급망 리스크' 또는 '데이터센터/AI 수요'를 언급한 문단을 찾아 "
                "원문 문구를 인용하고, 어떤 공시·페이지에서 나왔는지 출처를 함께 보여줘.",
    },
    {
        "id": "cpr_evidence", "title": "수치 원문 근거 보기", "category": "증거",
        "description": "특정 수치가 공시 원문 어디에 있는지 하이라이트로.",
        "body": "{TICKER}의 최근 매출(또는 순이익) 수치가 실제 공시 원문 어디에 나오는지 보여줘. "
                "수치를 인용 [n]하고, 출처 카드에서 원문 하이라이트(증거)와 '원문 열기'까지 확인할 수 있게.",
    },
    {
        "id": "cpr_kpi", "title": "보고된 KPI 추출", "category": "증거",
        "description": "기업이 보고한 KPI를 공시 문구에 인용하여.",
        "body": "{TICKER}가 최근 공시·실적자료에서 보고한 핵심 KPI(예: 가입자수, 출하량, ARPU 등)를 "
                "추출하고, 각 KPI를 실제 공시 문구에 근거해 출처와 함께 보여줘. 전망 KPI는 제외.",
    },
    # --- 보유·자금흐름 ---
    {
        "id": "cpr_13f", "title": "거장 포트폴리오(13F)", "category": "리서치",
        "description": "슈퍼투자자의 13F 상위 보유 종목.",
        "body": "버크셔 해서웨이(또는 다른 거장)의 최근 13F 상위 보유 종목과 비중을 SEC 13F 출처로 "
                "정리해줘. 전망·매수의견 없이 보유 현황만.",
    },
    {
        "id": "cpr_etf", "title": "ETF 구성종목", "category": "리서치",
        "description": "ETF의 상위 구성종목과 비중(N-PORT).",
        "body": "{TICKER} ETF의 상위 구성종목과 비중을 SEC N-PORT 출처로 보여줘. "
                "전망·추천 없이 보유 구성만.",
    },
    # --- 종합(멀티에이전트) · 개념 ---
    {
        "id": "cpr_comprehensive", "title": "종합 분석", "category": "종합",
        "description": "주가·재무·공시 리스크·뉴스를 한 번에(멀티에이전트).",
        "body": "{TICKER}를 종합적으로 분석해줘 — 최근 주가 흐름, 재무(매출·순이익), 공시상 주요 리스크, "
                "그리고 최근 뉴스를 각각 출처와 함께. 전망·목표가·매수의견은 빼고 사실 위주로.",
    },
    {
        "id": "cpr_concept", "title": "개념 설명", "category": "개념",
        "description": "투자 개념을 출처 없이 전문 지식으로 설명.",
        "body": "PER(주가수익비율)이 어떤 지표인지 개념·계산법·해석·한계를 쉽게 설명해줘. "
                "특정 종목의 구체적 수치는 지어내지 말고 개념 위주로.",
    },
]


def seed_community_prompts() -> None:
    with SessionLocal() as db:
        for c in COMMUNITY:
            if db.get(Prompt, c["id"]):
                continue
            db.add(Prompt(
                id=c["id"], user_email=None, title=c["title"], description=c.get("description"),
                body=c["body"], category=c.get("category"), community=True,
            ))
        db.commit()


# --- schemas --------------------------------------------------------------
class PromptIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    description: str | None = None
    category: str | None = None


class PromptPatch(BaseModel):
    title: str | None = None
    body: str | None = None
    description: str | None = None
    category: str | None = None


def _out(p: Prompt) -> dict:
    return {
        "id": p.id, "title": p.title, "description": p.description, "body": p.body,
        "category": p.category, "community": p.community, "source_id": p.source_id,
        "editable": p.user_email is not None,
    }


# --- endpoints ------------------------------------------------------------
@router.get("", summary="The user's personal prompt library")
async def list_prompts(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Prompt).where(Prompt.user_email == user.email).order_by(Prompt.created_at.desc())
        ).scalars().all()
        return {"prompts": [_out(p) for p in rows]}


@router.get("/community", summary="The seeded community catalog (read-only)")
async def list_community(user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        rows = db.execute(
            select(Prompt).where(Prompt.user_email.is_(None)).order_by(Prompt.category, Prompt.title)
        ).scalars().all()
        return {"prompts": [_out(p) for p in rows]}


@router.post("", summary="Create a personal prompt")
async def create_prompt(body: PromptIn, user: User = Depends(current_user)) -> dict:
    p = Prompt(
        user_email=user.email, title=body.title, description=body.description,
        body=body.body, category=body.category, community=False,
    )
    with SessionLocal() as db:
        db.add(p)
        db.commit()
        return _out(p)


@router.post("/{prompt_id}/import", summary="Import a community prompt into the user's library")
async def import_prompt(prompt_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        src = db.get(Prompt, prompt_id)
        if src is None or src.user_email is not None:
            raise HTTPException(404, "Community prompt not found.")
        # idempotent clone keyed on source_id (shared with future analyst/agent cloning — RF-13)
        copy, created = idempotent_clone(
            db, Prompt, src, user.email,
            fields=("title", "description", "body", "category"), overrides={"community": False},
        )
        if created:
            db.commit()
        return _out(copy)


@router.get("/{prompt_id}", summary="Get one prompt (own or community)")
async def get_prompt(prompt_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = db.get(Prompt, prompt_id)
        if p is None or (p.user_email is not None and p.user_email != user.email):
            raise HTTPException(404, "Prompt not found.")
        return _out(p)


@router.patch("/{prompt_id}", summary="Update a personal prompt (own only)")
async def update_prompt(prompt_id: str, body: PromptPatch, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = db.get(Prompt, prompt_id)
        if p is None or p.user_email != user.email:
            raise HTTPException(404, "Prompt not found or not editable.")
        if body.title is not None:
            p.title = body.title
        if body.body is not None:
            p.body = body.body
        if body.description is not None:
            p.description = body.description
        if body.category is not None:
            p.category = body.category
        db.commit()
        return _out(p)


@router.delete("/{prompt_id}", summary="Delete a personal prompt (own only)")
async def delete_prompt(prompt_id: str, user: User = Depends(current_user)) -> dict:
    with SessionLocal() as db:
        p = db.get(Prompt, prompt_id)
        if p is None or p.user_email != user.email:
            raise HTTPException(404, "Prompt not found or not deletable.")
        db.delete(p)
        db.commit()
        return {"deleted": prompt_id}
