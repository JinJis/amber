"""Chartable tool results → typed live artifacts (U3).

Pure data-shaping of known datasets shapes (prices / metrics-history / income
statements) into `Artifact` timeseries, plus the refresh path that re-runs a pinned
artifact's tool through the gateway. NOT reasoning — which tools to call is the
planner's job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agentengine.client import PlatformClient
from agentengine.freshness import compute_freshness
from agentengine.models import (
    Artifact,
    ArtifactCandle,
    ArtifactMarker,
    ArtifactPoint,
    ArtifactPriceLine,
    ArtifactSeries,
    CalcRow,
    ChartOverlay,
    Computation,
    OverlayLine,
    OverlayPoint,
)
from agentengine.provenance import _canonical_provenance, _filing_link, _market_hint


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _money(v) -> str:
    """Abbreviated money (B/M) for computation traces — shared by the computed-figure handlers."""
    if not isinstance(v, (int, float)):
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"{v/1e9:,.2f}B"
    if a >= 1e6:
        return f"{v/1e6:,.1f}M"
    return f"{v:,.2f}"


def _pct(v) -> str:
    return f"{v*100:.1f}%" if isinstance(v, (int, float)) else "—"


def _timeseries(title: str, series: list[ArtifactSeries], source, tool_name, ticker, url=None,
                chart_style: str | None = None) -> Artifact | None:
    series = [s for s in series if s.points]
    if not series:
        return None
    as_of = max((p.x for s in series for p in s.points if p.x), default=None)
    lengths = {len(s.points) for s in series}
    return Artifact(
        kind="timeseries", title=title.strip() or "추이", series=series, source=source,
        # money amounts (revenue/income) read better as bars; ratios/prices stay lines.
        chart_style=chart_style,
        as_of=as_of, freshness=compute_freshness(as_of), ticker=ticker,
        has_gap=len(lengths) > 1,  # series of differing coverage → a gap to draw
        url=url, tool=tool_name,
    )


# Chartable tool results → a typed artifact. Pure data-shaping of a known API shape
# (like _citations) — NOT reasoning; which tools to call is still the model's job.
def _artifacts(tool: dict, result: dict) -> list[Artifact]:
    """Build the tool's artifacts and stamp each with the source's periodicity + category (from
    the catalog tool dict) so a pinned chart/table can gate its notification-bot affordance."""
    arts = _build_artifacts(tool, result)
    cad, cat = tool.get("cadence"), tool.get("category")
    for a in arts:
        if a.cadence is None:
            a.cadence = cad
        if a.category is None:
            a.category = cat
    return arts


def _build_artifacts(tool: dict, result: dict) -> list[Artifact]:
    """Dispatch a tool's result to the matching artifact builder(s) (RF-08: a tool-suffix → handler
    registry replaced the former 19-branch if-ladder). Each handler is self-guarded (returns [] when
    the data shape doesn't fit), so adding a chartable tool = one handler + one registry row."""
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    name, src = tool["name"], tool.get("source")
    # canonical link to the filing the figures came from (drawn on the artifact card)
    url, accn, cik = _canonical_provenance(data)
    if not url and accn:
        url = _filing_link(_market_hint(tool, data), accn, cik)
    ctx = _Ctx(data=data, src=src, name=name, url=url)
    out: list[Artifact] = []
    for suffixes, handler in _BUILDERS:
        if name.endswith(suffixes):  # str.endswith accepts a tuple of suffixes
            out.extend(handler(ctx))
    return out


@dataclass(frozen=True)
class _Ctx:
    """The shared context every artifact handler receives: the tool's result `data`, its `source`,
    the catalog tool `name` (for suffix-dependent titles), and the canonical filing `url`."""
    data: dict
    src: object
    name: str
    url: object


def _h_prices(ctx: _Ctx) -> list[Artifact]:
    data, src, name, url = ctx.data, ctx.src, ctx.name, ctx.url
    if not isinstance(data.get("prices"), list):
        return []
    # the Price model's date lives in `time` (no `date` field); take the date part.
    rows = sorted((p for p in data["prices"] if p.get("time")), key=lambda p: str(p.get("time")))
    # PH-VIZ-1: real OHLCV → candlestick + volume. Keep a close-line series too (table view).
    candles = [ArtifactCandle(time=str(p.get("time"))[:10], open=_num(p.get("open")), high=_num(p.get("high")),
                              low=_num(p.get("low")), close=_num(p.get("close")), volume=_num(p.get("volume")))
               for p in rows]
    has_ohlc = any(c.open is not None and c.high is not None and c.low is not None for c in candles)
    pts = [ArtifactPoint(x=c.time, y=c.close) for c in candles]
    series = [ArtifactSeries(label="종가", points=pts)]
    as_of = max((p.x for p in pts if p.x), default=None)
    if not pts:
        return []
    return [Artifact(
        kind="candlestick" if has_ohlc else "timeseries",
        title=f"{data.get('ticker') or ''} 주가".strip(), series=series,
        candles=candles if has_ohlc else [], source=src, as_of=as_of,
        freshness=compute_freshness(as_of), ticker=data.get("ticker"), url=url, tool=name,
    )]


def _h_metrics_history(ctx: _Ctx) -> list[Artifact]:
    data, src, name, url = ctx.data, ctx.src, ctx.name, ctx.url
    if not isinstance(data.get("metrics"), list):
        return []
    rows = data["metrics"]
    series = []
    for key, label in (("gross_margin", "매출총이익률"), ("operating_margin", "영업이익률"), ("net_margin", "순이익률")):
        pts = [ArtifactPoint(x=str(r.get("report_period")), y=_num(r.get(key)))
               for r in rows if r.get("report_period") and r.get(key) is not None]
        if pts:
            series.append(ArtifactSeries(label=label, unit="ratio", points=sorted(pts, key=lambda p: p.x)))
    a = _timeseries(f"{data.get('ticker') or ''} 재무비율 추이", series, src, name, data.get("ticker"), url)
    return [a] if a else []


def _h_income_statements(ctx: _Ctx) -> list[Artifact]:
    data, src, name, url = ctx.data, ctx.src, ctx.name, ctx.url
    if not isinstance(data.get("income_statements"), list):
        return []
    rows = data["income_statements"]
    ticker = (rows[0].get("ticker") if rows else None)
    series = []
    for key, label in (("revenue", "매출"), ("net_income", "순이익")):
        pts = [ArtifactPoint(x=str(r.get("report_period")), y=_num(r.get(key)))
               for r in rows if r.get("report_period") and r.get(key) is not None]
        if pts:
            series.append(ArtifactSeries(label=label, points=sorted(pts, key=lambda p: p.x)))
    a = _timeseries(f"{ticker or ''} 매출·순이익", series, src, name, ticker, url, chart_style="bar")
    return [a] if a else []


def _h_groups(ctx: _Ctx) -> list[Artifact]:
    data, src, name = ctx.data, ctx.src, ctx.name
    if not isinstance(data.get("groups"), list):
        return []
    # CE-1 cross-asset / commodities / semiconductor / themes snapshot → a sourced grouped table.
    _GTITLE = {"__commodities": "원자재 시세", "__semiconductor": "반도체 사이클 프록시 (DRAM 현물가 아님)",
               "__themes": "테마·섹터 시세"}
    gtitle = next((t for suf, t in _GTITLE.items() if name.endswith(suf)), "자산군 현황")
    col0 = "분류" if name.endswith(("__commodities", "__semiconductor", "__themes")) else "자산군"

    def _px(v):
        return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

    def _pct(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "—"

    rows = [[col0, "종목", "현재가", "등락%"]]
    for g in data["groups"]:
        for m in (g.get("members") or []):
            rows.append([g.get("name", ""), m.get("label") or m.get("ticker", ""),
                         _px(m.get("price")), _pct(m.get("change_percent"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title=gtitle, table=rows,
                         source=src or "Yahoo Finance", as_of=data.get("as_of"),
                         freshness=compute_freshness(data.get("as_of")), tool=name)]
    return []


def _h_volume_rank(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("results"), list) and data["results"]):
        return []
    # CE-12: KR 거래량 순위 (movers) → a sourced table.
    def _kn(v):
        if not isinstance(v, (int, float)):
            return "—"
        return f"{v/1e12:,.2f}조" if abs(v) >= 1e12 else (f"{v/1e8:,.0f}억" if abs(v) >= 1e8 else f"{v:,}")

    rows = [["순위", "종목", "현재가", "등락%", "거래대금"]]
    for r in data["results"][:20]:
        cp = r.get("change_percent")
        rows.append([str(r.get("rank") or ""), r.get("name") or r.get("ticker") or "",
                     f"{r.get('price'):,}" if isinstance(r.get("price"), (int, float)) else "—",
                     f"{cp:+.2f}%" if isinstance(cp, (int, float)) else "—", _kn(r.get("value"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title="거래량 순위 (KR)", table=rows,
                         source=data.get("source") or "한국투자증권 (KIS)", tool=name)]
    return []


def _h_fluctuation_rank(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("results"), list) and data["results"]):
        return []
    # CE-12: 등락률 순위 (gainers/losers) → a sourced table.
    rows = [["순위", "종목", "현재가", "등락%", "거래량"]]
    for r in data["results"][:20]:
        cp = r.get("change_percent")
        rows.append([str(r.get("rank") or ""), r.get("name") or r.get("ticker") or "",
                     f"{r.get('price'):,}" if isinstance(r.get("price"), (int, float)) else "—",
                     f"{cp:+.2f}%" if isinstance(cp, (int, float)) else "—",
                     f"{r.get('volume'):,}" if isinstance(r.get("volume"), (int, float)) else "—"])
    if len(rows) > 1:
        dlabel = "하락률" if data.get("direction") == "down" else "상승률"
        return [Artifact(kind="table", title=f"{dlabel} 순위 (KR)", table=rows,
                         source=data.get("source") or "한국투자증권 (KIS)", tool=name)]
    return []


def _h_market_cap_rank(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("results"), list) and data["results"]):
        return []
    # CE-12: 시가총액 순위 → a sourced table (시총 억원→조원, 시장 비중).
    rows = [["순위", "종목", "시총", "비중", "등락%"]]
    for r in data["results"][:20]:
        mc = r.get("market_cap_eok")  # 억원
        cp = r.get("change_percent")
        rows.append([str(r.get("rank") or ""), r.get("name") or r.get("ticker") or "",
                     f"{mc/10000:,.1f}조" if isinstance(mc, (int, float)) else "—",
                     f"{r.get('market_weight_pct'):.2f}%" if isinstance(r.get("market_weight_pct"), (int, float)) else "—",
                     f"{cp:+.2f}%" if isinstance(cp, (int, float)) else "—"])
    if len(rows) > 1:
        return [Artifact(kind="table", title="시가총액 순위 (KR)", table=rows,
                         source=data.get("source") or "한국투자증권 (KIS)", tool=name)]
    return []


def _h_etf_nav(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if data.get("nav") is None:
        return []
    # CE-12: ETF 현재가 vs NAV + 괴리율 → a compact sourced table.
    def _pc(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "—"

    price, nav, dprt = data.get("price"), data.get("nav"), data.get("premium_discount_pct")
    table = [["구분", "값"],
             ["현재가", f"{price:,}" if isinstance(price, (int, float)) else "—"],
             ["NAV", f"{nav:,.2f}" if isinstance(nav, (int, float)) else "—"],
             ["괴리율", _pc(dprt)],
             ["가격 등락", _pc(data.get("price_change_percent"))],
             ["NAV 등락", _pc(data.get("nav_change_percent"))]]
    return [Artifact(kind="table", title=f"{data.get('name') or data.get('ticker') or ''} ETF NAV".strip(),
                     table=table, source=data.get("source") or "한국투자증권 (KIS)", tool=name,
                     ticker=data.get("ticker"))]


def _h_investor_flow(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("flows"), list) and data["flows"]):
        return []
    # CE-12: 투자자별 순매수 (수급) → a sourced table (개인/외국인/기관, 순매수 주식수).
    def _q(v):
        return f"{v:+,}" if isinstance(v, (int, float)) else "—"

    rows = [["일자", "종가", "개인", "외국인", "기관"]]
    for f in data["flows"][:15]:
        rows.append([str(f.get("date") or ""),
                     f"{f.get('close'):,}" if isinstance(f.get("close"), (int, float)) else "—",
                     _q(f.get("individual_net")), _q(f.get("foreign_net")), _q(f.get("institution_net"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title=f"{data.get('ticker', '')} 투자자별 순매수 (수급)".strip(),
                         table=rows, source=data.get("source") or "한국투자증권 (KIS)", tool=name,
                         ticker=data.get("ticker"))]
    return []


def _h_consensus_estimates(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("estimates"), list) and data["estimates"]):
        return []
    # CE-11: analyst consensus estimates → a sourced table (third-party, labelled).
    def _b(v):
        if not isinstance(v, (int, float)):
            return "—"
        return f"{v/1e9:,.1f}B" if abs(v) >= 1e9 else (f"{v/1e6:,.0f}M" if abs(v) >= 1e6 else f"{v:,.2f}")

    rows = [["기간", "매출(컨센서스)", "EPS(컨센서스)", "순이익(컨센서스)"]]
    for e in data["estimates"][:8]:
        rows.append([str(e.get("date") or "")[:10], _b(e.get("revenue_avg")),
                     _b(e.get("eps_avg")), _b(e.get("net_income_avg"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title=f"{data.get('symbol', '')} 애널리스트 컨센서스 추정치".strip(),
                         table=rows, source=data.get("source") or "FMP (컨센서스)", tool=name,
                         ticker=data.get("symbol"))]
    return []


def _h_earnings_calendar(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("events"), list) and data["events"]):
        return []
    # CE-11: earnings calendar → consensus vs actual (surprise) table.
    def _b2(v):
        if not isinstance(v, (int, float)):
            return "—"
        return f"{v/1e9:,.1f}B" if abs(v) >= 1e9 else f"{v:,.2f}"

    rows = [["발표일", "EPS 추정", "EPS 실제", "서프라이즈", "매출 실제"]]
    for ev in data["events"][:12]:
        su = ev.get("eps_surprise")
        rows.append([str(ev.get("date") or "")[:10], _b2(ev.get("eps_estimated")), _b2(ev.get("eps_actual")),
                     (f"{su:+,.2f}" if isinstance(su, (int, float)) else "—"), _b2(ev.get("revenue_actual"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title=f"{data.get('symbol', '')} 실적 캘린더".strip(),
                         table=rows, source="FMP", tool=name, ticker=data.get("symbol"))]
    return []


def _h_news(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("news"), list) and data["news"]):
        return []
    # CE-10: recent news → a sourced, pinnable digest table (headline · publisher · date).
    rows = [["헤드라인", "발행사", "날짜"]]
    for a in data["news"][:12]:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        rows.append([str(a.get("title"))[:120], str(a.get("source") or "")[:40], str(a.get("date") or "")[:10]])
    if len(rows) > 1:
        tk = (data["news"][0] or {}).get("ticker")
        return [Artifact(kind="table", title=f"{tk + ' ' if tk else ''}뉴스 다이제스트".strip(),
                         table=rows, source="Google News", as_of=(data["news"][0] or {}).get("date"),
                         tool=name, ticker=tk)]
    return []


def _h_macro_panel(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not isinstance(data.get("indicators"), list):
        return []
    # CE-9: 국가경제 패널 → a sourced table (지표·최신값·변화·그룹).
    def _v(v, unit):
        if not isinstance(v, (int, float)):
            return "—"
        return f"{v:,.2f}{'%' if unit == '%' else ''}"

    rows = [["그룹", "지표", "최신값", "변화", "기준"]]
    for ind in data["indicators"]:
        ch = ind.get("change")
        ch_s = f"{ch:+,.2f}" if isinstance(ch, (int, float)) else "—"
        # honesty: a stale-but-present value is LABELLED (지연), never shown as if current.
        asof = ind.get("as_of") or ""
        if ind.get("stale"):
            asof = f"{asof} ⚠지연" if asof else "⚠지연"
        rows.append([ind.get("group") or "", ind.get("name", ""), _v(ind.get("latest"), ind.get("unit")),
                     ch_s, asof])
    if len(rows) > 1:
        return [Artifact(kind="table", title=f"{data.get('region', '')} 거시경제 패널".strip(),
                         table=rows, source=data.get("source") or "DBnomics", tool=name)]
    return []


_BT_METRIC_LABEL = {"total_return": "누적수익", "cagr": "연평균수익(CAGR)", "volatility": "변동성(연율)",
                    "max_drawdown": "최대낙폭(MDD)", "best_day": "최고일", "worst_day": "최저일"}


def _backtest_computation(data: dict) -> Computation | None:
    """How the backtest figures came about — the holdings + capital + window queried (sourced
    prices), the simulation method, and the resulting performance metrics."""
    holdings = data.get("holdings") or []
    metrics = data.get("metrics") or {}
    if not holdings and not metrics:
        return None
    in_rows = [CalcRow(label=h.get("ticker", "?"), value=_pct(h.get("weight")),
                       source="ingestion store (Yahoo prices)")
               for h in holdings if isinstance(h, dict)]
    if isinstance(data.get("initial"), (int, float)):
        in_rows.append(CalcRow(label="초기자본", value=_money(data["initial"])))
    if data.get("start") and data.get("end"):
        in_rows.append(CalcRow(label="기간", value=f"{data['start']} ~ {data['end']}"))
    st_rows = [CalcRow(label=_BT_METRIC_LABEL.get(k, k), value=_pct(v))
               for k, v in metrics.items() if isinstance(v, (int, float))]
    return Computation(
        method="히스토리컬 시뮬레이션 (일별 종가 · 가중 보유)",
        formula="포트폴리오 가치ₜ = 초기자본 × Σ (보유비중ᵢ × 종목 누적수익률ᵢ,ₜ)",
        inputs=in_rows, steps=st_rows,
        note=data.get("disclaimer") or "과거 성과이며 미래 수익을 보장하지 않습니다. (예측 아님)")


def _h_backtest(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not (isinstance(data.get("curve"), list) and data["curve"]):
        return []
    # CE-7: portfolio equity curve → a timeseries (portfolio + benchmark, rebased to initial).
    series = [ArtifactSeries(label="포트폴리오", points=[
        ArtifactPoint(x=p["date"], y=_num(p.get("value"))) for p in data["curve"]])]
    bench = data.get("benchmark") or {}
    if isinstance(bench.get("curve"), list) and bench["curve"]:
        series.append(ArtifactSeries(label=bench.get("ticker") or "벤치마크", points=[
            ArtifactPoint(x=p["date"], y=_num(p.get("value"))) for p in bench["curve"]]))
    m = data.get("metrics") or {}
    tr = m.get("total_return")
    title = "포트폴리오 백테스트" + (f" (누적 {tr*100:+.1f}%)" if isinstance(tr, (int, float)) else "")
    a = _timeseries(title, series, "ingestion store (Yahoo prices)", name, None)
    if a:
        a.computation = _backtest_computation(data)
    return [a] if a else []


_FACTOR_FORMULA = {
    "pe": "주가 ÷ EPS", "pb": "시총 ÷ 자본", "ps": "시총 ÷ 매출", "roe": "순이익 ÷ 자본",
    "net_margin": "순이익 ÷ 매출", "gross_margin": "매출총이익 ÷ 매출", "revenue_growth": "매출 YoY 성장률",
    "fcf_yield": "FCF ÷ 시총", "return_window": "기간 주가수익률", "pct_from_high": "52주 고점 대비 하락률",
    "market_cap": "주가 × 발행주식수",
}
_OP = {"gte": "≥", "lte": "≤", "gt": ">", "lt": "<", "eq": "=", ">=": "≥", "<=": "≤"}


def _quant_computation(data: dict) -> Computation | None:
    """How the screen ran — the data queried, the filter criteria applied, and how each factor is
    computed from the underlying financials/prices."""
    factors = data.get("factors") or []
    filters = data.get("applied_filters") or []
    if not factors and not filters:
        return None
    crit = [CalcRow(label=str(f.get("field", "")).upper(),
                    value=f"{_OP.get(str(f.get('operator')), f.get('operator', ''))} {f.get('value')}")
            for f in filters if isinstance(f, dict)]
    srt = data.get("sort")
    if srt:
        crit.append(CalcRow(label="정렬", value=f"{srt} {'내림차순' if data.get('order') == 'desc' else '오름차순'}"))
    factor_rows = [CalcRow(label=str(f).upper(), value=_FACTOR_FORMULA[f]) for f in factors if f in _FACTOR_FORMULA]
    return Computation(
        method="팩터 스크리닝 (랭킹)",
        formula="종목별 팩터를 재무·가격 데이터로 계산 → 기준을 통과한 종목을 정렬",
        inputs=[CalcRow(label="재무제표", value="SEC EDGAR / OpenDART"),
                CalcRow(label="가격", value="Yahoo Finance")],
        assumptions=crit, steps=factor_rows,
        note=f"{data.get('count', 0)}종목 통과")


def _h_quant_screen(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not isinstance(data.get("results"), list):
        return []
    # CE-6: factor screener → a sourced ranked table (key factors per ticker).
    def _r2(v):
        return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

    def _pct(v):
        return f"{v*100:+.1f}%" if isinstance(v, (int, float)) else "—"

    def _mc(v):
        if not isinstance(v, (int, float)):
            return "—"
        return f"{v/1e12:,.2f}T" if abs(v) >= 1e12 else (f"{v/1e9:,.1f}B" if abs(v) >= 1e9 else f"{v/1e6:,.0f}M")

    rows = [["종목", "시총", "PER", "PBR", "ROE", "기간수익"]]
    for r in data["results"]:
        rows.append([r.get("ticker", ""), _mc(r.get("market_cap")), _r2(r.get("pe")),
                     _r2(r.get("pb")), _pct(r.get("roe")), _pct(r.get("return_window"))])
    if len(rows) > 1:
        srt = data.get("sort")
        title = f"퀀트 스크리너 ({data.get('market', '')}{' · ' + srt + ' 순' if srt else ''}) — {data.get('count', 0)}종목"
        return [Artifact(kind="table", title=title.strip(), table=rows,
                         source="ingestion store (SEC/DART + Yahoo)", tool=name,
                         computation=_quant_computation(data))]
    return []


# labels + formula strings for the valuation computation trace (per model).
_VAL_METHOD = {"dcf": "2단계 FCF 할인 (DCF)", "ddm": "배당할인 (DDM)", "rim": "잔여이익 (RIM)"}
_VAL_FORMULA = {
    "dcf": "EV = Σ PV(FCFₜ) + PV(터미널) · 자기자본가치 = EV − 순부채 · 내재가치 = 자기자본가치 ÷ 발행주식수",
    "ddm": "내재가치 = D1 ÷ (할인율 − 성장률),  D1 = D0 × (1 + 성장률)",
    "rim": "내재가치 = BVPS + Σ PV(잔여이익ₜ) + PV(터미널),  잔여이익ₜ = (ROE − 할인율) × BVPS",
}
_VAL_ASSUMPTION_LABEL = {"growth_rate": "성장률", "discount_rate": "할인율", "years": "추정기간",
                         "terminal_growth": "영구성장률", "dividend_per_share": "주당배당 D0"}
_VAL_INPUT_LABEL = {"base_fcf": "기준 FCF (영업CF − CapEx)", "shares": "발행주식수", "net_debt": "순부채",
                    "dividend_per_share": "주당배당 D0", "bvps": "주당순자산 BVPS", "roe": "ROE",
                    "equity": "자본총계"}
_VAL_STEP_LABEL = {"enterprise_value": "기업가치 EV", "equity_value": "자기자본가치",
                   "pv_explicit": "추정기간 PV 합", "pv_terminal": "터미널 PV", "terminal_value": "터미널 가치 TV",
                   "d1": "차기 배당 D1", "pv_residual": "잔여이익 PV 합"}
_VAL_PCT_KEYS = {"growth_rate", "discount_rate", "terminal_growth", "roe"}


def _valuation_computation(data: dict) -> Computation | None:
    """The auditable derivation of a DCF/DDM/RIM intrinsic value — sourced inputs, the user's
    assumptions, the formula, and the intermediate math, so the figure is never a black box."""
    model = str(data.get("model") or "").lower()
    if model not in _VAL_METHOD:
        return None
    src = data.get("source")
    inputs = data.get("inputs") or {}
    assumptions = data.get("assumptions") or {}
    bd = data.get("breakdown") or {}

    def _v(k, val):  # ratios as %, years as a count, money abbreviated
        if k in _VAL_PCT_KEYS:
            return _pct(val)
        if k == "years":
            return f"{val}년"
        return _money(val)

    in_rows = [CalcRow(label=_VAL_INPUT_LABEL.get(k, k), value=_v(k, v), source=src)
               for k, v in inputs.items() if v is not None]
    as_rows = [CalcRow(label=_VAL_ASSUMPTION_LABEL.get(k, k), value=_v(k, v))
               for k, v in assumptions.items() if v is not None]
    st_rows = [CalcRow(label=_VAL_STEP_LABEL[k], value=_money(bd[k]))
               for k in _VAL_STEP_LABEL if isinstance(bd.get(k), (int, float))]
    return Computation(method=_VAL_METHOD[model], formula=_VAL_FORMULA.get(model),
                       inputs=in_rows, assumptions=as_rows, steps=st_rows,
                       note=data.get("note") or data.get("disclaimer"))


def _h_valuation(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not data.get("model"):
        return []
    # CE-5: a transparent valuation calc → a sourced table (projection + intrinsic value).
    model = str(data.get("model")).upper()
    vps = data.get("value_per_share")
    bd = data.get("breakdown") or {}
    rows_in = bd.get("rows") or []
    _m = _money  # local alias (kept for the projection-row formatting below)

    if data.get("model") == "rim":
        table = [["연차", "BVPS", "잔여이익", "현재가치"]] + [
            [str(r.get("year")), _m(r.get("bvps")), _m(r.get("residual_income")), _m(r.get("pv"))] for r in rows_in]
    elif data.get("model") == "dcf":
        table = [["연차", "예상 FCF", "현재가치(PV)"]] + [
            [str(r.get("year")), _m(r.get("fcf")), _m(r.get("pv"))] for r in rows_in]
    else:  # ddm — no projection rows; show the components
        table = [["구분", "값"], ["D0 (현재 배당)", _m(bd.get("d0"))], ["D1", _m(bd.get("d1"))]]
    if isinstance(vps, (int, float)):  # summary row, padded to the table's column count
        cols = len(table[0])
        table.append((["내재가치 / 주", f"{vps:,.2f}"] + [""] * cols)[:cols])
    title = f"{data.get('ticker') or ''} {model} 내재가치"
    if isinstance(vps, (int, float)):
        title += f" — {vps:,.2f}/주 (가정 기반)"
    if len(table) > 1:
        return [Artifact(kind="table", title=title.strip(), table=table,
                         source=data.get("source") or "재무제표 기반 모델", as_of=data.get("as_of"),
                         freshness=compute_freshness(data.get("as_of")), tool=name,
                         ticker=data.get("ticker"), computation=_valuation_computation(data))]
    return []


def _h_sector_heatmap(ctx: _Ctx) -> list[Artifact]:
    data, src, name = ctx.data, ctx.src, ctx.name
    if not isinstance(data.get("sectors"), list):
        return []
    # CE-2: US sector heatmap → a sourced, ranked table card (섹터 히트맵).
    def _pct(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "—"

    rows = [["섹터", "ETF", "등락%"]]
    for s in data["sectors"]:
        rows.append([s.get("sector", ""), s.get("ticker", ""), _pct(s.get("change_percent"))])
    if len(rows) > 1:
        return [Artifact(kind="table", title="섹터 히트맵 (S&P 500)", table=rows,
                         source=src or "Yahoo Finance", as_of=data.get("as_of"),
                         freshness=compute_freshness(data.get("as_of")), tool=name)]
    return []


def _h_guru_trades(ctx: _Ctx) -> list[Artifact]:
    data, src, name, url = ctx.data, ctx.src, ctx.name, ctx.url
    if not isinstance(data.get("trades"), list):
        return []
    # CE-3: 거장 매매 — quarter-over-quarter 13F moves → a sourced table card.
    def _usd(v):
        if not isinstance(v, (int, float)) or v == 0:
            return "—"
        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1e9:
            return f"{sign}${a/1e9:,.2f}B"
        if a >= 1e6:
            return f"{sign}${a/1e6:,.1f}M"
        return f"{sign}${a:,.0f}"

    _ACTION = {"new": "신규", "added": "추가", "trimmed": "축소", "exited": "전량매도"}
    rows = [["종목", "매매", "보유가치", "가치변동", "주식수 변동"]]
    for t in data["trades"]:
        sc = t.get("shares_change")
        sc_s = f"{sc:+,}" if isinstance(sc, (int, float)) else "—"
        rows.append([
            t.get("ticker") or t.get("name_of_issuer") or t.get("cusip", ""),
            _ACTION.get(t.get("action"), t.get("action", "")),
            _usd(t.get("value_usd")), _usd(t.get("value_change_usd")), sc_s,
        ])
    if len(rows) > 1:
        guru = (data.get("guru") or {}).get("investor") or "거장"
        rp = data.get("report_period") or ""
        return [Artifact(kind="table", title=f"{guru} 매매내역 ({rp})".strip(),
                         table=rows, source=src or "SEC EDGAR 13F", as_of=data.get("filing_date"),
                         freshness=compute_freshness(data.get("filing_date")), url=url, tool=name)]
    return []


def _h_guru_common(ctx: _Ctx) -> list[Artifact]:
    data, src, name = ctx.data, ctx.src, ctx.name
    if not isinstance(data.get("common"), list):
        return []
    # CE-3: 공통 보유종목 — securities held by the most superinvestors → sourced table.
    rows = [["종목", "보유 거장 수", "보유 거장"]]
    for c in data["common"]:
        holders = ", ".join(h.get("investor", "") for h in (c.get("holders") or [])[:6])
        if len(c.get("holders") or []) > 6:
            holders += " 외"
        rows.append([
            c.get("ticker") or c.get("name_of_issuer") or c.get("cusip", ""),
            str(c.get("holder_count", len(c.get("holders") or []))), holders,
        ])
    if len(rows) > 1:
        return [Artifact(kind="table", title="거장 공통 보유종목", table=rows,
                         source=src or "SEC EDGAR 13F", as_of=None,
                         freshness=None, tool=name)]
    return []


def _h_technical_indicators(ctx: _Ctx) -> list[Artifact]:
    data, name = ctx.data, ctx.name
    if not isinstance(data.get("indicators"), list):
        return []
    # PH-VIZ-4: a descriptive indicator artifact. `enrich_chart_overlays` later folds
    # these onto a same-ticker price chart when one exists this turn; otherwise it
    # renders standalone (price-pane lines on the price scale, RSI/MACD as sub-panes).
    overlays = _overlays_from_technical(data)
    if overlays:
        tk = data.get("ticker")
        return [Artifact(
            kind="timeseries", title=f"{tk or ''} 기술적 지표".strip(),
            overlays=overlays, source=data.get("source"), as_of=data.get("as_of"),
            freshness=compute_freshness(data.get("as_of")), ticker=tk, tool=name,
        )]
    return []


# tool-name suffix(es) → artifact handler. str.endswith accepts the tuple, so a handler covering
# several shapes (the grouped snapshots) lists them together. Append order matches the old ladder.
_BUILDERS: list[tuple[tuple[str, ...], object]] = [
    (("__prices",), _h_prices),
    (("__metrics_history",), _h_metrics_history),
    (("__income_statements",), _h_income_statements),
    (("__asset_classes", "__commodities", "__semiconductor", "__themes"), _h_groups),
    (("__volume_rank",), _h_volume_rank),
    (("__fluctuation_rank",), _h_fluctuation_rank),
    (("__market_cap_rank",), _h_market_cap_rank),
    (("__etf_nav",), _h_etf_nav),
    (("__investor_flow",), _h_investor_flow),
    (("__consensus_estimates",), _h_consensus_estimates),
    (("__earnings_calendar",), _h_earnings_calendar),
    (("__news",), _h_news),
    (("__macro_panel",), _h_macro_panel),
    (("__backtest",), _h_backtest),
    (("__quant_screen",), _h_quant_screen),
    (("__valuation",), _h_valuation),
    (("__sector_heatmap",), _h_sector_heatmap),
    (("__guru_trades",), _h_guru_trades),
    (("__guru_common",), _h_guru_common),
    (("__technical_indicators",), _h_technical_indicators),
]


# Descriptive, stable colors per indicator line (server-owned → consistent across turns).
_OVERLAY_COLORS = {
    "upper": "#9aa7bd", "lower": "#9aa7bd", "middle": "#4f8cff",
    "macd": "#4f8cff", "signal": "#D9A300", "histogram": "#86868C",
}
_OVERLAY_PALETTE = ["#4f8cff", "#D9A300", "#1FA463", "#A05CF5", "#D1483A"]


def _overlay_color(label: str, idx: int) -> str:
    lab = (label or "").lower()
    for key, color in _OVERLAY_COLORS.items():
        if key in lab:
            return color
    if lab.startswith("sma"):
        return "#4f8cff"
    if lab.startswith("ema"):
        return "#D9A300"
    if lab.startswith("rsi"):
        return "#A05CF5"
    if lab.startswith("vol"):
        return "#1FA463"
    return _OVERLAY_PALETTE[idx % len(_OVERLAY_PALETTE)]


def _overlays_from_technical(data: dict) -> list[ChartOverlay]:
    """Shape PH-DATA-6's `/technical-indicators` result into chart overlays — pure
    data-shaping (like _artifacts), drops gaps, no fabrication."""
    out: list[ChartOverlay] = []
    src = data.get("source")
    for ind in (data.get("indicators") or []):
        lines: list[OverlayLine] = []
        for i, li in enumerate(ind.get("lines") or []):
            pts = [OverlayPoint(time=str(p["date"])[:10], value=float(p["value"]))
                   for p in (li.get("points") or [])
                   if p.get("date") and p.get("value") is not None]
            if pts:
                lab = li.get("label") or ind.get("name") or ind.get("key") or "지표"
                lines.append(OverlayLine(label=lab, color=_overlay_color(lab, i), points=pts))
        if lines:
            out.append(ChartOverlay(
                key=str(ind.get("key") or ind.get("name") or "ind"),
                name=str(ind.get("name") or ind.get("key") or "지표"),
                pane=("sub" if ind.get("pane") == "sub" else "price"),
                unit=ind.get("unit"), lines=lines, source=src,
            ))
    return out


def enrich_chart_overlays(artifacts: list[Artifact]) -> None:
    """PH-VIZ-4: fold each standalone technical-indicator artifact onto the same-ticker
    price (candlestick) chart, so the overlays render ON the price. When no price chart
    exists this turn, the technical artifact stays and renders on its own. Mutates the
    list in place (removing the merged standalone artifacts)."""
    tech = [a for a in artifacts if a.overlays and a.kind != "candlestick"]
    if not tech:
        return
    merged: list[Artifact] = []
    for p in artifacts:
        if p.kind != "candlestick" or not p.candles or p.overlays:
            continue
        for t in tech:
            if t in merged:
                continue
            if (t.ticker or "").upper() == (p.ticker or "").upper():
                p.overlays = t.overlays
                merged.append(t)
    if merged:
        artifacts[:] = [a for a in artifacts if a not in merged]


def _is_date(s: str) -> bool:
    import re
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", s or ""))


def _price_lines(a: Artifact) -> list[ArtifactPriceLine]:
    """PH-VIZ-2: descriptive period high/low reference lines, drawn from the price data
    itself (no extra source needed). 52-week label only when the span really covers ~1y."""
    highs = [c.high for c in a.candles if c.high is not None]
    lows = [c.low for c in a.candles if c.low is not None]
    if not highs or not lows:
        closes = [p.y for s in a.series for p in s.points if p.y is not None]
        if not closes:
            return []
        highs, lows = closes, closes
    n = len(a.candles) if a.candles else (len(a.series[0].points) if a.series else 0)
    span = "52주" if n >= 200 else "기간"
    return [ArtifactPriceLine(price=round(max(highs), 4), label=f"{span} 고가", color="#1FA463"),
            ArtifactPriceLine(price=round(min(lows), 4), label=f"{span} 저가", color="#D1483A")]


def _event_markers(history: list, ticker: str | None) -> list[ArtifactMarker]:
    """Sourced event markers for the price chart, gathered from THIS turn's corporate-actions
    + earnings results (same ticker). Each marker keeps its source so a click opens evidence."""
    tk = (ticker or "").upper()
    out: list[ArtifactMarker] = []
    for dec, res in history:
        data = (res or {}).get("data")
        if not isinstance(data, dict):
            continue
        name = getattr(dec, "tool", "") or ""
        dtk = str(data.get("ticker") or "").upper()
        if tk and dtk and dtk != tk:
            continue
        if name.endswith("__corporate_actions"):
            for d in (data.get("dividends") or []):
                ex = str((d or {}).get("ex_date") or "")[:10]
                amt = (d or {}).get("amount")
                if _is_date(ex):
                    out.append(ArtifactMarker(time=ex, label="배당", kind="dividend", position="belowBar",
                                              color="#4f8cff", source="Yahoo Finance",
                                              snippet=f"배당락일 {ex}" + (f" · 주당 {amt}" if amt is not None else "")))
            for s in (data.get("splits") or []):
                dt = str((s or {}).get("date") or "")[:10]
                ratio = (s or {}).get("ratio")
                if _is_date(dt):
                    out.append(ArtifactMarker(time=dt, label="분할", kind="split", position="belowBar",
                                              color="#D9A300", source="Yahoo Finance",
                                              snippet=f"주식분할 {dt}" + (f" · {ratio}" if ratio else "")))
        elif name.endswith("__earnings"):
            mkt = str(data.get("market") or "").upper()
            esrc = "OpenDART (FSS)" if mkt == "KR" else "SEC EDGAR"
            for e in (data.get("earnings") or []):
                dt = str((e or {}).get("filing_date") or (e or {}).get("report_period") or "")[:10]
                if _is_date(dt):
                    out.append(ArtifactMarker(time=dt, label="실적", kind="earnings", position="aboveBar",
                                              color="#9aa7bd", source=esrc, url=(e or {}).get("filing_url"),
                                              snippet=f"실적 공시 {dt}"))
    seen, dedup = set(), []
    for m in sorted(out, key=lambda m: m.time):
        k = (m.time, m.kind)
        if k not in seen:
            seen.add(k)
            dedup.append(m)
    return dedup[:50]


def enrich_chart_markers(artifacts: list[Artifact], history: list) -> None:
    """PH-VIZ-2: attach descriptive price lines + sourced event markers to price charts.
    Mutates the artifacts in place. The chart literally shows the cited events on the timeline."""
    for a in artifacts:
        if a.kind != "candlestick" or not a.candles:  # markers/lines belong on the price chart
            continue
        if not a.pricelines:
            a.pricelines = _price_lines(a)
        if not a.markers:
            a.markers = _event_markers(history, a.ticker)


_enrich_logger = logging.getLogger(__name__)


async def enrich_artifacts(artifacts: list[Artifact], history: list, task: str, model: str,
                           backend: str | None, *, annotate_timeout: float | None = None) -> None:
    """The post-loop chart-enrichment sequence (PH-VIZ), shared by run_agent + the chat stream (RF-10).

    In order, mutating `artifacts` in place: (1) attach sourced event markers + period high/low lines
    to price charts, (2) fold technical-indicator overlays onto the same-ticker price chart, (3) let
    Gemini annotate the price chart from the question (historical points only; best-effort).

    `annotate_timeout` bounds step 3: the streaming caller passes a cap so chart annotation never
    delays the `done` event (timeout/error → skip); run_agent passes None (await it directly)."""
    enrich_chart_markers(artifacts, history)
    enrich_chart_overlays(artifacts)
    from agentengine.annotations import annotate_charts  # lazy: avoids an artifacts<->annotations cycle
    if annotate_timeout is None:
        await annotate_charts(artifacts, task, model, backend)
        return
    import asyncio
    try:  # best-effort + bounded — chart annotation must never delay `done`
        await asyncio.wait_for(annotate_charts(artifacts, task, model, backend), timeout=annotate_timeout)
    except (TimeoutError, asyncio.TimeoutError):
        _enrich_logger.warning("annotate_charts exceeded %.0fs cap → skipping annotations", annotate_timeout)
    except Exception:  # noqa: BLE001
        _enrich_logger.warning("annotate_charts failed → skipping annotations", exc_info=True)


async def refresh_artifact(tool_name: str, args: dict | None, api_key: str | None, title: str | None = None) -> Artifact | None:
    """Re-run one tool through the gateway and re-shape its result into a fresh artifact
    (U3-03b). Returns the artifact matching `title` if given, else the first produced."""
    client = PlatformClient(api_key)
    tools = await client.fetch_tools()
    tool = tools.get(tool_name)
    if tool is None:
        return None
    result = await client.call_tool(tool, args or {})
    arts = _artifacts(tool, result)
    for a in arts:
        a.args = args or {}
    if title:
        match = next((a for a in arts if a.title == title), None)
        if match is not None:
            return match
    return arts[0] if arts else None
