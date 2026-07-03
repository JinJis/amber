"""The "라이브 데모" dashboard template — a high-impact, Datadog-style showcase of what the platform
can surface, with realistic MOCK data embedded directly in each widget's spec (no `tool` → renders
statically, never re-fetches). Several widgets carry ``live: true`` → the frontend ticks them on a
timer (a client-side simulation, demo only) so the board feels alive.

Layout (explicit 12-col grid, honored by board_from_template):
  ┌──────────────────────── 핵심 지표 (현재가·PER·PBR·Fwd PER·탐욕지수) — 큼지막 + 실시간 ────────────────────────┐
  │ 주가 캔들 ────────────────┐ 섹터 히트맵 ──┐ 📰 실시간 뉴스 (우측 라이브 피드) │
  │ 매출·영업이익 ────────────┐ 중요 일정     ┐                                  │
  │ Peer 밸류 비교 ───────────┐ 거시 지표     ┐                                  │
  │ 종목 내러티브 ────────────┐ DCF 내재가치 ─────────────────────────────────┐ │
  │ 🔔 공시 알림 ─────────────┐ 수급 동향 ────────────────────────────────────┘ │
  └──────────────────────────────────────────────────────────────────────────────┘

Data is generated deterministically at import (seed time) so dates roll forward to "now".
"""

from __future__ import annotations

import datetime
import random

_SEED = 20260626
_TODAY = datetime.date.today()
# Real current level (researched): 삼성전자 ~₩324,500, mkt cap ₩2,215조 (2026-06, AI/HBM 슈퍼사이클 재평가;
# 사상 최고 ₩374,500 on 2026-06-19). The hero/heatmap/peer all show this fixed value. Source: investing.com.
_PRICE = 324500


def _weekdays(n: int, end: datetime.date) -> list[datetime.date]:
    out: list[datetime.date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return list(reversed(out))


def _round100(x: float) -> int:
    return int(round(x / 100.0) * 100)


def _price_candles() -> tuple[list[dict], list[dict], int, int, int]:
    """A realistic ~3-month daily KRW candle series (deterministic, mean-reverting). Returns
    (candles, close-series points, latest_close, 52w_high, 52w_low)."""
    # anchored to the real June-2026 level: 삼성전자 ~₩324,500 (mkt cap ₩2,215조), after a 2026 AI/HBM
    # super-cycle re-rating (historic high ₩374,500 on 2026-06-19, then a pullback). Source: investing.com.
    rng = random.Random(_SEED)
    days = _weekdays(62, _TODAY - datetime.timedelta(days=1))
    candles: list[dict] = []
    points: list[dict] = []
    close = 286000.0
    target = 325000.0
    for d in days:
        drift = (target - close) / close * 0.045 + rng.uniform(-0.016, 0.016)
        new_close = close * (1 + drift)
        open_ = close * (1 + rng.uniform(-0.007, 0.007))
        hi = max(open_, new_close) * (1 + rng.uniform(0.001, 0.013))
        lo = min(open_, new_close) * (1 - rng.uniform(0.001, 0.013))
        vol = rng.randint(9, 22) * 1_000_000
        iso = d.isoformat()
        candles.append({"time": iso, "open": open_, "high": hi, "low": lo, "close": new_close, "volume": vol})
        close = new_close
    # scale the whole series so the LATEST close lands exactly on the real current price (₩324,500)
    factor = _PRICE / candles[-1]["close"]
    for c in candles:
        for k in ("open", "high", "low", "close"):
            c[k] = _round100(c[k] * factor)
    points = [{"x": c["time"], "y": c["close"]} for c in candles]
    return candles, points, _PRICE, 374500, 189000   # last = fixed real price · real 52w high / plausible low


def _quarters(n: int) -> list[str]:
    y, q = _TODAY.year, (_TODAY.month - 1) // 3 + 1
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y}-Q{q}")
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return list(reversed(out))


def _revenue_series() -> list[dict]:
    qs = _quarters(11)
    rev = [73.6, 76.8, 77.8, 79.3, 71.9, 74.1, 79.0, 81.5, 77.0, 82.9, 79.8]
    op = [4.8, 6.7, 8.9, 10.7, 6.6, 8.4, 10.4, 12.9, 9.1, 13.6, 11.2]
    return [
        {"label": "매출", "points": [{"x": q, "y": v * 1e12} for q, v in zip(qs, rev)]},
        {"label": "영업이익", "points": [{"x": q, "y": v * 1e12} for q, v in zip(qs, op)]},
    ]


def _ago(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)}분 전"
    if hours < 24:
        return f"{int(hours)}시간 전"
    return (_TODAY - datetime.timedelta(days=int(hours // 24))).strftime("%m-%d")


def _future(days: int) -> str:
    return (_TODAY + datetime.timedelta(days=days)).isoformat()


def _w(spec: dict, x: int, y: int, w: int, h: int) -> dict:
    """A widget with an EXPLICIT grid placement (12-col units) — board_from_template honors x/y/w/h."""
    return {"spec": spec, "x": x, "y": y, "w": w, "h": h}


def demo_template() -> dict:
    candles, close_pts, last, hi52, lo52 = _price_candles()
    today = _TODAY.isoformat()
    intrinsic = _round100(last * 1.14)
    mk1, mk2 = candles[len(candles) * 2 // 3]["time"], candles[len(candles) // 3]["time"]

    widgets = [
        # ── HERO: 큼지막한 핵심 지표 (실시간으로 틱) ────────────────────────────────────
        _w({"kind": "stat", "title": "삼성전자 핵심 지표", "source": "실시간 시세·밸류", "live": True,
            "as_of": today, "freshness": "fresh", "cadence": "intraday", "category": "valuation",
            "stats": [
                {"label": "현재가", "value": last, "unit": "원", "delta": -1.4, "fmt": "won"},
                {"label": "PER", "value": 28.4, "unit": "배", "delta": -0.9, "fmt": "num"},
                {"label": "PBR", "value": 4.9, "unit": "배", "delta": -0.6, "fmt": "num"},
                {"label": "Forward PER", "value": 21.6, "unit": "배", "delta": -0.7, "fmt": "num"},
                {"label": "탐욕·공포 지수", "value": 74, "gauge": True},
            ]}, 0, 0, 12, 4),

        # ── 주가 캔들 · 섹터 히트맵 · 우측 라이브 뉴스 피드 ─────────────────────────────
        _w({"kind": "candlestick", "title": "삼성전자 주가 (005930)", "source": "Yahoo Finance",
            "ticker": "005930.KS", "as_of": today, "freshness": "fresh", "cadence": "daily",
            "category": "market", "candles": candles, "series": [{"label": "종가", "points": close_pts}],
            "pricelines": [{"price": hi52, "label": "52주 최고"}, {"price": lo52, "label": "52주 최저"}],
            "markers": [{"time": mk1, "label": "1분기 실적발표", "kind": "earnings", "position": "aboveBar"},
                        {"time": mk2, "label": "배당락", "kind": "dividend", "position": "belowBar"}]},
           0, 4, 6, 9),
        _w({"kind": "heatmap", "title": "반도체 섹터 히트맵", "source": "실시간 시세", "live": True,
            "as_of": today, "freshness": "fresh", "cadence": "intraday", "category": "market",
            "cells": [
                {"label": "삼성전자", "sub": f"{last:,}", "pct": -1.4},
                {"label": "SK하이닉스", "sub": "1,318,000", "pct": 1.8},
                {"label": "한미반도체", "sub": "612,000", "pct": -2.3},
                {"label": "DB하이텍", "sub": "76,400", "pct": 0.6},
                {"label": "리노공업", "sub": "284,000", "pct": -0.9},
                {"label": "이오테크닉스", "sub": "412,000", "pct": 3.4},
                {"label": "원익IPS", "sub": "61,800", "pct": 2.1},
                {"label": "주성엔지니어링", "sub": "58,300", "pct": -0.7},
                {"label": "HPSP", "sub": "47,500", "pct": 2.8},
                {"label": "테스", "sub": "52,100", "pct": 1.3},
                {"label": "솔브레인", "sub": "498,000", "pct": -0.4},
                {"label": "고영", "sub": "39,200", "pct": 1.0},
            ]}, 6, 4, 3, 9),
        _w({"kind": "feed", "title": "📰 실시간 뉴스", "source": "Google News", "live": True,
            "as_of": today, "freshness": "fresh", "cadence": "streaming", "category": "news",
            "items": [
                {"time": _ago(0.3), "tag": "속보", "text": "삼성전자, 세계 최초 HBM4 양산 출하…AI 컴퓨팅용 최고 성능"},
                {"time": _ago(1), "tag": "HBM", "text": "삼성, 엔비디아에 HBM4 첫 공급…하반기 점유율 확대 전망"},
                {"time": _ago(2.5), "tag": "시장", "text": "HBM 점유율 SK하이닉스 58% 선두…삼성·마이크론 각 21%"},
                {"time": _ago(4), "tag": "메모리", "text": "삼성, 1분기 글로벌 D램 점유율 38%…2개 분기 연속 1위"},
                {"time": _ago(6), "tag": "기술", "text": "삼성, HBM4E 샘플 2분기 출하…16Gbps·4.0TB/s 지원"},
                {"time": _ago(9), "tag": "실적", "text": "\"삼성 2026 HBM 매출, 작년比 3배 이상\"…생산능력 선제 확대"},
                {"time": _ago(13), "tag": "해외", "text": "AMD '잭팟'에 웃는 삼성…HBM4로 SK하이닉스 독주 균열"},
                {"time": _ago(20), "tag": "해외", "text": "마이크론도 HBM4 속도전…\"5세대보다 생산 2배\""},
                {"time": _ago(26), "tag": "시황", "text": "삼성전자 사상 최고가(₩374,500) 후 차익실현…조정 국면"},
                {"time": _ago(31), "tag": "수급", "text": "외국인, 반도체株 순매수 지속…코스피 사상 최고권"},
            ]}, 9, 4, 3, 23),

        # ── 실적 추이 · 중요 일정 ──────────────────────────────────────────────────────
        _w({"kind": "timeseries", "title": "삼성전자 매출·영업이익 (분기)", "source": "OpenDART (FSS)",
            "ticker": "005930.KS", "as_of": _quarters(1)[0], "freshness": "fresh", "cadence": "event",
            "category": "fundamentals", "chart_style": "bar", "series": _revenue_series()}, 0, 13, 6, 7),
        _w({"kind": "calendar", "title": "중요 일정", "source": "실적·거시 캘린더", "as_of": today,
            "freshness": "fresh", "cadence": "event", "category": "market", "events": [
                # real July-2026 calendar (US CPI 7/14, FOMC 7/28-29, 삼성 2Q 7/23 — researched)
                {"date": "2026-07-08", "label": "삼성전자 2분기 잠정실적", "tag": "실적"},
                {"date": "2026-07-09", "label": "선물·옵션 동시 만기", "tag": "수급"},
                {"date": "2026-07-14", "label": "미국 6월 CPI 발표", "tag": "거시"},
                {"date": "2026-07-23", "label": "삼성전자 2분기 확정실적·컨콜", "tag": "실적"},
                {"date": "2026-07-24", "label": "SK하이닉스 2분기 실적", "tag": "실적"},
                {"date": "2026-07-29", "label": "FOMC 금리결정", "tag": "거시"},
            ]}, 6, 13, 3, 7),

        # ── Peer 밸류 비교 · 거시 지표 ─────────────────────────────────────────────────
        _w({"kind": "table", "title": "반도체 Peer 밸류 비교", "source": "SEC EDGAR · OpenDART",
            "as_of": today, "freshness": "fresh", "cadence": "daily", "category": "valuation", "table": [
                ["기업", "주가", "PER", "PBR", "배당", "시가총액"],
                ["삼성전자", f"{last:,}원", "28.4배", "4.9배", "0.6%", "2,215조원"],
                ["SK하이닉스", "1,318,000원", "16.8배", "5.4배", "0.4%", "959조원"],
                ["TSMC", "$418", "34.2배", "11.6배", "0.9%", "$2.17T"],
                ["Micron", "$286", "21.5배", "4.8배", "0.2%", "$320B"],
                ["Intel", "$44", "—", "1.3배", "1.1%", "$190B"],
            ]}, 0, 20, 6, 7),
        _w({"kind": "table", "title": "거시 지표", "source": "FRED · ECOS", "as_of": today,
            "freshness": "fresh", "cadence": "scheduled", "category": "macro", "table": [
                ["지표", "현재", "전월", "추세"],
                ["한국 기준금리", "2.75%", "2.75%", "→ 동결"],
                ["미국 기준금리(상단)", "4.50%", "4.50%", "→ 동결"],
                ["원/달러 환율", "1,362원", "1,378원", "↘ 원화강세"],
                ["미 10년 국채", "4.21%", "4.34%", "↘ 하락"],
                ["미 CPI(YoY)", "2.8%", "3.0%", "↘ 둔화"],
                ["KOSPI", "4,180", "4,021", "↗ 사상 최고권"],
            ]}, 6, 20, 3, 7),

        # ── 종목 내러티브 · DCF 내재가치 ───────────────────────────────────────────────
        _w({"kind": "narrative", "title": "삼성전자 종목 내러티브", "source": "자체 분석 (사실 기반)",
            "as_of": today, "freshness": "fresh", "cadence": "one_shot", "category": "fundamentals",
            "sections": [
                {"heading": "사업 구조", "body": "메모리(DRAM·NAND)·시스템LSI·파운드리의 DS부문과 MX·VD 부문으로 "
                 "구성. 메모리가 영업이익의 큰 축이며 HBM·DDR5 등 고부가 비중 확대가 이익 레버리지의 핵심."},
                {"heading": "투자 포인트", "body": "① AI 가속기향 HBM 구조적 성장 ② 메모리 업황 회복 사이클 "
                 "③ 자사주·배당 등 주주환원 확대 ④ 파운드리 2나노 로드맵."},
                {"heading": "리스크", "body": "메모리 가격 변동성, 중국 수요 둔화, 환율, 파운드리 가동률. (전망·목표가 아님)"},
            ]}, 0, 27, 6, 7),
        _w({"kind": "table", "title": "DCF 내재가치 (가정 기반 · 예측 아님)", "source": "재무제표 기반 모델",
            "ticker": "005930.KS", "as_of": today, "freshness": "fresh", "cadence": "one_shot",
            "category": "valuation", "table": [
                ["연차", "예상 FCF", "현재가치(PV)"],
                ["1년차", "81조", "74조"], ["2년차", "87조", "73조"], ["3년차", "94조", "71조"],
                ["4년차", "102조", "70조"], ["5년차", "110조", "69조"],
                ["내재가치 / 주", f"{intrinsic:,}원", "현재가 +14%"],
            ], "computation": {
                "method": "2단계 FCF 할인 (DCF)",
                "formula": "EV = Σ PV(FCFₜ) + PV(터미널) · 자기자본가치 = EV − 순부채 · ÷ 발행주식수",
                "inputs": [{"label": "기준 FCF", "value": "75조 (HBM 슈퍼사이클)", "source": "OpenDART 현금흐름표"},
                           {"label": "발행주식수", "value": "59.7억주", "source": "OpenDART"},
                           {"label": "순부채", "value": "−92조 (순현금)", "source": "OpenDART"}],
                "assumptions": [{"label": "성장률", "value": "9.0%"}, {"label": "할인율", "value": "9.0%"},
                                {"label": "추정기간", "value": "5년"}, {"label": "영구성장률", "value": "3.0%"}],
                "steps": [{"label": "추정기간 PV 합", "value": "357조"}, {"label": "터미널 PV", "value": "1,759조"},
                          {"label": "자기자본가치", "value": "2,208조"}],
                "note": "사용자 가정 기반 계산이며 예측·목표가가 아닙니다.",
            }}, 6, 27, 6, 7),

        # ── 공시 알림 · 수급 동향 ──────────────────────────────────────────────────────
        _w({"kind": "table", "title": "🔔 공시 알림", "source": "OpenDART (FSS)", "as_of": today,
            "freshness": "fresh", "cadence": "event", "category": "filings", "table": [
                ["시각", "종목", "공시", "상태"],
                [_ago(2), "삼성전자", "단일판매·공급계약체결", "🔴 NEW"],
                [_ago(5), "삼성전자", "자기주식취득 결정", "🔴 NEW"],
                [_ago(26), "SK하이닉스", "주요사항보고서(유상증자)", "읽음"],
                [_ago(50), "현대차", "현금·현물배당 결정", "읽음"],
                [_ago(72), "삼성전자", "분기보고서 (1분기)", "읽음"],
            ]}, 0, 34, 6, 6),
        _w({"kind": "kpi", "title": "수급 동향 (5거래일 누적)", "source": "KIS", "ticker": "005930.KS",
            "as_of": today, "freshness": "fresh", "cadence": "daily", "category": "market", "table": [
                ["투자자", "순매수", "추세"],
                ["외국인", "+8,420억", "5일 연속 매수"],
                ["기관", "+2,160억", "순매수"],
                ["개인", "−10,580억", "순매도"],
                ["연기금", "+1,340억", "순매수"],
            ]}, 6, 34, 6, 6),
    ]
    return {
        "id": "dt_demo",
        "name": "⭐ 라이브 데모 — 삼성전자",
        "market": None,
        "description": "13 위젯 · 핵심지표(실시간)·차트·히트맵·뉴스피드·캘린더 (데모)",
        "widgets": widgets,
    }
