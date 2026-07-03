"""The connector catalog — one ``ConnectorManifest`` per implemented data source.

This is the machine-readable description every later platform surface reads (REST
docs, MCP tool generation, RAG registration, entitlements, metering, governance).
Each resource's ``path`` must map to a real registered route (enforced by a test).
"""

from __future__ import annotations

from app.connectors.manifest import (
    Cadence,
    CATEGORIES,
    Category,
    ConnectorManifest,
    CostTier,
    Freshness,
    License,
    Provenance,
    Resource,
    ResourceParam,
    UpstreamCredential,
)

# Shared vocabulary (reusable params, license policies, provenance specs) lives in
# a sibling so the governance constants stay apart from the connector listing below.
from app.connectors.catalog_policies import (
    LIC_DART,
    LIC_DERIVED,
    LIC_ECOS,
    LIC_FRED,
    LIC_NEWS,
    LIC_SEC,
    LIC_YAHOO,
    P_LIMIT,
    P_MARKET,
    P_PERIOD,
    P_TICKER,
    P_TICKER_REQ,
    PROV_DART,
    PROV_DART_FILINGS,
    PROV_ECOS,
    PROV_DBNOMICS,
    PROV_FRED,
    PROV_NEWS,
    PROV_SEC,
    PROV_SEC_FILINGS,
    PROV_TECHNICAL,
    PROV_YAHOO,
)


def _statements(market: str, prov: Provenance, cost: CostTier) -> list[Resource]:
    items = [
        ("income_statements", "/financials/income-statements", "IncomeStatementResponse", "Income statements."),
        ("balance_sheets", "/financials/balance-sheets", "BalanceSheetResponse", "Balance sheets."),
        ("cash_flow_statements", "/financials/cash-flow-statements", "CashFlowStatementResponse", "Cash flow statements."),
        ("all_financials", "/financials", "FinancialsResponse", "All three statements."),
    ]
    return [
        Resource(name=n, description=d, path=p, output_model=m, markets=[market], cost_tier=cost,
                 params=[P_TICKER_REQ, P_PERIOD, P_LIMIT, P_MARKET], provenance=prov)
        for n, p, m, d in items
    ]


CONNECTORS: list[ConnectorManifest] = [
    ConnectorManifest(
        id="sec_edgar", name="SEC EDGAR", domain="us-fundamentals",
        description="US company facts, XBRL financial statements, filings, earnings, insider (Form 4), 13F.",
        markets=["US"],
        upstream=UpstreamCredential(requires_key=False, key_env="SEC_EDGAR_USER_AGENT"),
        license=LIC_SEC,
        resources=[
            Resource(name="company_facts", description="Company facts.", path="/company/facts",
                     output_model="CompanyFactsResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[P_TICKER, ResourceParam(name="cik", description="SEC CIK."), P_MARKET], provenance=PROV_SEC),
            Resource(name="company_search", description="Search companies by name or ticker.",
                     path="/company/search", output_model="CompanySearchResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="q", required=True, description="Name or ticker query."),
                             P_LIMIT, P_MARKET], provenance=PROV_SEC),
            *_statements("US", PROV_SEC, CostTier.free),
            Resource(name="filings", description="SEC filings.", path="/filings", output_model="FilingsResponse",
                     markets=["US"], cost_tier=CostTier.free, params=[P_TICKER, P_LIMIT, P_MARKET], provenance=PROV_SEC_FILINGS),
            Resource(name="earnings", description="Earnings actuals (XBRL).", path="/earnings", output_model="EarningsResponse",
                     markets=["US"], cost_tier=CostTier.free, params=[P_TICKER_REQ, P_LIMIT], provenance=PROV_SEC),
            Resource(name="insider_trades", description="Insider trades (Form 4).", path="/insider-trades",
                     output_model="InsiderTradesResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[P_TICKER, P_LIMIT, P_MARKET], provenance=PROV_SEC_FILINGS),
            Resource(name="institutional_holdings", description="13F holdings (filer_cik mode).", path="/institutional-holdings",
                     output_model="InstitutionalHoldingsResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="filer_cik", description="13F filer CIK."), P_LIMIT], provenance=PROV_SEC_FILINGS),
            Resource(name="index_funds", description="ETF/index-fund holdings (constituents) from SEC N-PORT.",
                     path="/index-funds", output_model="IndexFundHoldingsResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="ticker", description="Fund/ETF ticker (e.g. SPY)."), P_LIMIT, P_MARKET],
                     provenance=PROV_SEC_FILINGS),
            Resource(name="gurus", description="Superinvestor (Buffett/Burry/Ackman…) 13F portfolios; holdings cited to SEC.",
                     path="/gurus", output_model="InstitutionalHoldingsResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="slug", description="Guru slug (e.g. buffett); omit to list all."), P_LIMIT],
                     provenance=PROV_SEC_FILINGS),
            Resource(name="guru_trades", description="거장 매매 — a superinvestor's latest quarter-over-quarter 13F moves (new/added/trimmed/exited).",
                     path="/gurus/trades", output_model="GuruTradesResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="slug", description="Guru slug (e.g. buffett)."), P_LIMIT],
                     provenance=PROV_SEC_FILINGS),
            Resource(name="guru_common", description="공통 보유종목 — securities held by the most superinvestors right now; each holder cited to SEC.",
                     path="/gurus/common", output_model="GuruCommonResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="slugs", description="Comma-separated guru slugs; omit for all."),
                             ResourceParam(name="min_holders", description="Minimum gurus holding a security (default 2)."), P_LIMIT],
                     provenance=PROV_SEC_FILINGS),
            Resource(name="metrics_snapshot", description="Derived valuation metrics snapshot.", path="/financial-metrics/snapshot",
                     output_model="FinancialMetricSnapshotResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[P_TICKER_REQ, P_MARKET], provenance=PROV_SEC),
            Resource(name="comparables", description="Peer valuation comparables — multiples side by side for a set of tickers.",
                     path="/comparables", output_model="FinancialMetricSnapshotResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[ResourceParam(name="tickers", required=True, description="Comma-separated peers, e.g. AAPL,MSFT,GOOGL"),
                             P_MARKET], provenance=PROV_SEC),
            Resource(name="as_reported", description="Financials exactly as reported in XBRL (raw us-gaap concepts, per period).",
                     path="/financials/as-reported", output_model="AsReportedResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[P_TICKER_REQ, ResourceParam(name="period", enum=["annual", "quarterly", "ttm"]), P_LIMIT, P_MARKET],
                     provenance=PROV_SEC),
        ],
    ),
    ConnectorManifest(
        id="yahoo", name="Yahoo Finance", domain="prices",
        description="End-of-day prices and snapshots for US and KR (.KS/.KQ). Delayed.",
        markets=["US", "KR"],
        upstream=UpstreamCredential(requires_key=False),
        license=LIC_YAHOO,
        resources=[
            Resource(name="prices", description="Historical EOD OHLCV.", path="/prices", output_model="PricesResponse",
                     cost_tier=CostTier.free, params=[
                         P_TICKER_REQ, ResourceParam(name="interval", required=True, enum=["day", "week", "month", "year"]),
                         ResourceParam(name="start_date", required=True, type="date"),
                         ResourceParam(name="end_date", required=True, type="date"), P_MARKET], provenance=PROV_YAHOO),
            Resource(name="price_snapshot", description="Latest price snapshot.", path="/prices/snapshot",
                     output_model="PriceSnapshotResponse", cost_tier=CostTier.free, params=[P_TICKER_REQ, P_MARKET], provenance=PROV_YAHOO),
            Resource(name="corporate_actions", description="Dividends + stock splits history.", path="/corporate-actions",
                     output_model="CorporateActionsResponse", cost_tier=CostTier.free,
                     params=[P_TICKER_REQ, ResourceParam(name="years", type="integer", description="Look-back years."), P_MARKET],
                     provenance=PROV_YAHOO),
            Resource(name="technical_indicators",
                     description="Descriptive technical indicators (SMA/EMA/RSI/MACD/Bollinger/volatility) computed from prices — not signals.",
                     path="/technical-indicators", output_model="TechnicalIndicatorsResponse", cost_tier=CostTier.free,
                     params=[P_TICKER_REQ,
                             ResourceParam(name="indicators", description="Comma list e.g. sma_50,ema_20,rsi_14,macd,bbands_20."),
                             ResourceParam(name="interval", enum=["day", "week", "month", "year"]),
                             ResourceParam(name="start_date", type="date"), ResourceParam(name="end_date", type="date"), P_MARKET],
                     provenance=PROV_TECHNICAL),
            Resource(name="asset_classes",
                     description="Cross-asset snapshot (자산군): indices, rates, commodities, FX, crypto — descriptive levels + day change.",
                     path="/market/asset-classes", output_model="CrossAssetResponse", cost_tier=CostTier.free,
                     params=[], provenance=PROV_YAHOO),
            Resource(name="sector_heatmap",
                     description="US sector heatmap (섹터 히트맵): 11 GICS sectors via SPDR ETFs — ranked day change, descriptive.",
                     path="/market/sectors", output_model="SectorHeatmapResponse", markets=["US"], cost_tier=CostTier.free,
                     params=[], provenance=PROV_YAHOO),
            Resource(name="commodities",
                     description="원자재 시세 (귀금속·산업금속·에너지·농산물) — Yahoo 선물 기준 현재 수준 + 등락. (DRAM 현물가는 무료 소스 없음.)",
                     path="/market/commodities", output_model="CommoditiesResponse", cost_tier=CostTier.free,
                     params=[], provenance=PROV_YAHOO),
            Resource(name="semiconductor",
                     description="반도체 사이클 프록시 (필라델피아 반도체지수·반도체 ETF·메모리 제조사 주가) — DRAM 현물가의 무료 대용(현물가 아님).",
                     path="/market/semiconductor", output_model="SemiconductorProxyResponse", cost_tier=CostTier.free,
                     params=[], provenance=PROV_YAHOO),
            Resource(name="themes",
                     description="테마/섹터 시세 — 글로벌(AI·반도체·2차전지·청정에너지·원자력·바이오·방산·우주·로봇·핀테크·금광·농업·리츠·지역·디지털자산) + 한국(KODEX/TIGER 반도체·2차전지·바이오·방산·조선 등) 대표 ETF 프록시 현재 수준+등락.",
                     path="/market/themes", output_model="ThemesResponse", cost_tier=CostTier.free,
                     params=[], provenance=PROV_YAHOO),
        ],
    ),
    ConnectorManifest(
        id="fred", name="FRED / DBnomics (US macro)", domain="macro",
        description="US/global central-bank policy rates (FED/ECB/BOE/BOJ). Keyless and cloud-safe by "
                    "default via DBnomics (BIS); falls back to FRED only when FRED_API_KEY is set.",
        markets=["US"],
        upstream=UpstreamCredential(requires_key=False),
        license=LIC_FRED,
        resources=[
            Resource(name="interest_rates", description="Historical interest rates.", path="/macro/interest-rates",
                     output_model="InterestRatesResponse", markets=["US"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="bank", required=True, enum=["FED", "ECB", "BOE", "BOJ"]), P_MARKET], provenance=PROV_FRED),
            Resource(name="interest_rates_snapshot", description="Latest interest rate.", path="/macro/interest-rates/snapshot",
                     output_model="InterestRatesResponse", markets=["US"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="bank", required=True, enum=["FED", "ECB", "BOE", "BOJ"]), P_MARKET], provenance=PROV_FRED),
            Resource(name="economic_indicators", description="Economic indicators (CPI, unemployment, GDP, …) via DBnomics — 그룹(하위요인)·지역 분류, 카탈로그 열람.",
                     path="/macro/indicators", output_model="EconomicIndicatorsResponse", markets=["US"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="indicator", description="Indicator slug (e.g. cpi); omit to list all."),
                             ResourceParam(name="region", description="카탈로그 지역 필터 (US/EA)."),
                             ResourceParam(name="group", description="카탈로그 그룹/하위요인 필터 (물가/고용/성장/금리)."),
                             ResourceParam(name="limit", type="integer", description="Recent observations.")],
                     provenance=PROV_DBNOMICS),
            Resource(name="macro_panel", description="국가경제 패널 — 한 지역의 핵심 거시지표 최신값·직전·변화 스냅샷(그룹별), DBnomics 출처.",
                     path="/macro/panel", output_model="MacroPanelResponse", markets=["US"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="region", description="국가/지역 (US, EA).")],
                     provenance=PROV_DBNOMICS),
        ],
    ),
    ConnectorManifest(
        id="opendart", name="OpenDART (KR)", domain="kr-fundamentals",
        description="KR company facts, financial statements, filings, earnings, insider (elestock), metrics.",
        markets=["KR"],
        upstream=UpstreamCredential(requires_key=True, key_env="OPENDART_API_KEY", signup_url="https://opendart.fss.or.kr/"),
        license=LIC_DART,
        resources=[
            Resource(name="company_facts", description="Company facts.", path="/company/facts", output_model="CompanyFactsResponse",
                     markets=["KR"], cost_tier=CostTier.low, params=[P_TICKER, P_MARKET], provenance=PROV_DART),
            Resource(name="company_search", description="Search companies by name or ticker.",
                     path="/company/search", output_model="CompanySearchResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="q", required=True, description="Name or ticker query."),
                             P_LIMIT, P_MARKET], provenance=PROV_DART),
            *_statements("KR", PROV_DART, CostTier.low),
            Resource(name="filings", description="DART filings.", path="/filings", output_model="FilingsResponse",
                     markets=["KR"], cost_tier=CostTier.low, params=[P_TICKER, P_LIMIT, P_MARKET], provenance=PROV_DART_FILINGS),
            Resource(name="earnings", description="Earnings actuals (DART).", path="/earnings", output_model="EarningsResponse",
                     markets=["KR"], cost_tier=CostTier.low, params=[P_TICKER_REQ, P_LIMIT, P_MARKET], provenance=PROV_DART),
            Resource(name="insider_trades", description="Insider/major-shareholder reports.", path="/insider-trades",
                     output_model="InsiderTradesResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[P_TICKER, P_LIMIT, P_MARKET], provenance=PROV_DART_FILINGS),
            Resource(name="metrics_snapshot", description="Derived metrics snapshot.", path="/financial-metrics/snapshot",
                     output_model="FinancialMetricSnapshotResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[P_TICKER_REQ, P_MARKET], provenance=PROV_DART),
            Resource(name="comparables", description="Peer valuation comparables — multiples side by side for a set of tickers.",
                     path="/comparables", output_model="FinancialMetricSnapshotResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="tickers", required=True, description="Comma-separated peers, e.g. 005930,000660"),
                             P_MARKET], provenance=PROV_DART),
        ],
    ),
    ConnectorManifest(
        id="ecos", name="Bank of Korea ECOS", domain="macro",
        description="KR interest rates (BOK base rate).",
        markets=["KR"],
        upstream=UpstreamCredential(requires_key=True, key_env="ECOS_API_KEY", signup_url="https://ecos.bok.or.kr/api/"),
        license=LIC_ECOS,
        resources=[
            Resource(name="interest_rates", description="Historical BOK rates.", path="/macro/interest-rates",
                     output_model="InterestRatesResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="bank", required=True, enum=["BOK"]), P_MARKET], provenance=PROV_ECOS),
            Resource(name="interest_rates_snapshot", description="Latest BOK rate.", path="/macro/interest-rates/snapshot",
                     output_model="InterestRatesResponse", markets=["KR"], cost_tier=CostTier.low,
                     params=[ResourceParam(name="bank", required=True, enum=["BOK"]), P_MARKET], provenance=PROV_ECOS),
        ],
    ),
    ConnectorManifest(
        id="google_news", name="Google News", domain="news",
        description="Company / market news headlines for US and KR (by company name).",
        markets=["US", "KR"],
        upstream=UpstreamCredential(requires_key=False),
        license=LIC_NEWS,
        resources=[
            Resource(name="news", description="Recent news articles.", path="/news", output_model="NewsResponse",
                     cost_tier=CostTier.free, params=[P_TICKER, P_LIMIT, P_MARKET], provenance=PROV_NEWS),
        ],
    ),
    ConnectorManifest(
        id="datasets_store", name="Ingestion Store (screener)", domain="screening",
        description="Cross-sectional screener + line-items over the point-in-time ingestion store.",
        markets=["US", "KR"],
        upstream=UpstreamCredential(requires_key=False),
        license=LIC_DERIVED,
        resources=[
            Resource(name="screener", description="Screen the ingested universe by financial criteria.",
                     method="POST", path="/financials/search/screener", output_model="FinancialsSearchResponse",
                     cost_tier=CostTier.free, params=[P_MARKET], provenance=Provenance(source="ingestion store (SEC/DART)", as_of_field="report_period", freshness=Freshness.periodic)),
            Resource(name="line_items", description="Fetch line items across tickers.", method="POST",
                     path="/financials/search/line-items", output_model="FinancialsSearchResponse", cost_tier=CostTier.free,
                     params=[], provenance=Provenance(source="ingestion store (SEC/DART)", as_of_field="report_period", freshness=Freshness.periodic)),
            Resource(name="quant_screen",
                     description="퀀트 팩터 스크리너 — 밸류(PER/PBR/PSR)·퀄리티(ROE/마진)·성장·모멘텀·사이즈 팩터를 store에서 계산해 필터·랭킹. 서술적 횡단면 분석(예측 아님).",
                     method="POST", path="/quant/screen", output_model="QuantScreenResponse", markets=["US", "KR"], cost_tier=CostTier.free,
                     params=[P_MARKET], provenance=Provenance(source="ingestion store (SEC/DART + Yahoo prices)", freshness=Freshness.periodic)),
            Resource(name="metrics_history", description="Derived financial ratios across periods (margins, returns, leverage, growth).",
                     path="/financial-metrics", output_model="FinancialMetricsHistoryResponse", markets=["US", "KR"], cost_tier=CostTier.free,
                     params=[P_TICKER_REQ, ResourceParam(name="period", enum=["annual", "quarterly", "ttm"]), P_LIMIT, P_MARKET],
                     provenance=Provenance(source="ingestion store (SEC/DART)", as_of_field="report_period", freshness=Freshness.periodic)),
            Resource(name="ir_materials",
                     description="IR 자료실 — IR/실적 관련 공시 (US: 8-K · KR: 주요사항보고서) 목록.",
                     path="/filings/ir", output_model="FilingsResponse", markets=["US", "KR"], cost_tier=CostTier.free,
                     params=[P_TICKER_REQ, P_LIMIT, P_MARKET],
                     provenance=Provenance(source="공시 (SEC/DART)", source_link_field="url", freshness=Freshness.periodic)),
            Resource(name="filing_search",
                     description="공시 본문에서 특정 주제(위험요소·공급망·수요·전략 등)를 언급한 문단을 찾아 원문 인용 — 처음 보는 종목은 최근 보고서를 즉시 인덱싱 후 의미검색. 출처(공시·섹션) 표기.",
                     path="/filings/search", output_model="RagSearchResponse", markets=["US", "KR"], cost_tier=CostTier.low,
                     params=[P_TICKER_REQ,
                             ResourceParam(name="query", required=True, description="공시 본문에서 찾을 주제/문구 (e.g. '공급망 리스크', 'AI 수요')."),
                             ResourceParam(name="top_k", type="integer", description="반환 문단 수 (기본 6)."), P_MARKET],
                     provenance=Provenance(source="공시 본문 (SEC/DART)", source_link_field="url", freshness=Freshness.periodic)),
            Resource(name="backtest",
                     description="포트폴리오 백테스트 — 종목·비중의 매수후보유 과거 성과(누적수익·CAGR·변동성·MDD, 벤치마크 대비) 서술적 계산. 미래 예측·조언 아님.",
                     method="POST", path="/backtest", output_model="BacktestResponse", markets=["US", "KR"], cost_tier=CostTier.free,
                     params=[P_MARKET], provenance=Provenance(source="ingestion store (Yahoo prices)", freshness=Freshness.eod)),
            Resource(name="valuation",
                     description="밸류에이션 모델 DCF/DDM/RIM — 실제 재무를 base로, 사용자 가정(성장률·할인율 등)에 따른 주당 내재가치 투명 계산. 예측·목표가 아님(가정 변경 시 결과도 변동).",
                     path="/valuation", output_model="ValuationResponse", markets=["US", "KR"], cost_tier=CostTier.free,
                     params=[P_TICKER_REQ,
                             ResourceParam(name="model", enum=["dcf", "ddm", "rim"], description="밸류에이션 모델 (기본 dcf)."),
                             ResourceParam(name="growth_rate", type="number", description="연간 성장률 가정 (예: 0.08)."),
                             ResourceParam(name="discount_rate", type="number", description="할인율/자본비용 (예: 0.10)."),
                             ResourceParam(name="years", type="integer", description="명시적 추정 기간(년)."),
                             ResourceParam(name="terminal_growth", type="number", description="DCF 영구성장률."),
                             ResourceParam(name="dividend_per_share", type="number", description="DDM 전용: 현재 주당배당금(D0)."),
                             P_MARKET],
                     provenance=Provenance(source="재무제표 기반 모델 (SEC/DART)", as_of_field="as_of", freshness=Freshness.periodic)),
        ],
    ),
    ConnectorManifest(
        id="rag", name="Document RAG (filings/news)", domain="retrieval",
        description="Semantic search over ingested filings/news/transcripts; returns passages with provenance.",
        markets=["US", "KR"],
        service="rag",  # served by the RAG service, not the data plane
        upstream=UpstreamCredential(requires_key=False),
        license=License(
            id="rag-derived", redistribution=False, attribution_required=True,
            note="Passages may include copyrighted source text — cite + link, minimal quoting.",
        ),
        resources=[
            Resource(
                name="search", description="Retrieve relevant passages with provenance (source, as_of, url).",
                method="POST", path="/rag/search", cost_tier=CostTier.low, markets=["US", "KR"],
                params=[
                    ResourceParam(name="query", required=True, description="Natural-language query."),
                    ResourceParam(name="top_k", type="integer", description="Max passages."),
                    P_TICKER, P_MARKET,
                ],
                provenance=Provenance(source="Platform RAG (filings/news)", freshness=Freshness.periodic),
            ),
        ],
    ),
    ConnectorManifest(
        id="kis", name="KIS (KR realtime)", domain="kr-realtime",
        description="한국투자증권 실시간 — 거래량 순위(KR movers) + 투자자별 수급(개인/외국인/기관). 서술적 시세, 조언 아님.",
        markets=["KR"],
        upstream=UpstreamCredential(requires_key=True, key_env="KIS_APP_KEY",
                                    signup_url="https://apiportal.koreainvestment.com/"),
        license=License(id="kis-commercial", redistribution=False, attribution_required=True,
                        note="브로커 실시간 데이터 — 출처 표기, 재배포 제한."),
        resources=[
            Resource(name="volume_rank",
                     description="거래량 순위 (KR 활발 종목 / movers) — 종목·현재가·등락%·거래량·거래대금.",
                     path="/kr/rankings/volume", output_model="KisVolumeRankResponse", markets=["KR"], cost_tier=CostTier.medium,
                     params=[P_LIMIT],
                     provenance=Provenance(source="한국투자증권 (KIS)", freshness=Freshness.realtime)),
            Resource(name="investor_flow",
                     description="투자자별 순매수 (개인·외국인·기관 수급) — 최근 일자별.",
                     path="/kr/investor-flow", output_model="KisInvestorFlowResponse", markets=["KR"], cost_tier=CostTier.medium,
                     params=[P_TICKER_REQ, P_LIMIT],
                     provenance=Provenance(source="한국투자증권 (KIS)", as_of_field="date", freshness=Freshness.realtime)),
            Resource(name="fluctuation_rank",
                     description="등락률 순위 — 상승률(up)/하락률(down) 상위 종목(현재가·등락%·거래량).",
                     path="/kr/rankings/fluctuation", output_model="KisFluctuationRankResponse", markets=["KR"], cost_tier=CostTier.medium,
                     params=[ResourceParam(name="direction", enum=["up", "down"], description="up(상승)/down(하락)"), P_LIMIT],
                     provenance=Provenance(source="한국투자증권 (KIS)", freshness=Freshness.realtime)),
            Resource(name="market_cap_rank",
                     description="시가총액 순위 — KR 대형주(시총·시장 비중·현재가·등락%).",
                     path="/kr/rankings/market-cap", output_model="KisMarketCapRankResponse", markets=["KR"], cost_tier=CostTier.medium,
                     params=[P_LIMIT],
                     provenance=Provenance(source="한국투자증권 (KIS)", freshness=Freshness.realtime)),
            Resource(name="etf_nav",
                     description="ETF 현재가 vs NAV + 괴리율(프리미엄/디스카운트) — ETF가 NAV 대비 비싼지/싼지.",
                     path="/kr/etf-nav", output_model="KisEtfNavResponse", markets=["KR"], cost_tier=CostTier.medium,
                     params=[P_TICKER_REQ],
                     provenance=Provenance(source="한국투자증권 (KIS)", freshness=Freshness.realtime)),
        ],
    ),
    ConnectorManifest(
        id="fmp", name="FMP (estimates / calendar)", domain="estimates",
        description="Analyst consensus estimates + earnings calendar — third-party DATA shown as-sourced "
                    "(NOT our forecast/target). No price targets or buy/sell ratings (guardrail).",
        markets=["US"],
        upstream=UpstreamCredential(requires_key=True, key_env="FMP_API_KEY",
                                    signup_url="https://site.financialmodelingprep.com/developer/docs"),
        license=License(id="fmp-commercial", redistribution=False, attribution_required=True,
                        note="Commercial license — analyst consensus shown as third-party sourced data."),
        resources=[
            Resource(name="consensus_estimates",
                     description="애널리스트 컨센서스 추정치(매출·EPS·순이익, 연/분기) — 제3자 데이터, 우리 예측 아님.",
                     path="/estimates", output_model="ConsensusEstimatesResponse", markets=["US"], cost_tier=CostTier.medium,
                     params=[P_TICKER_REQ, ResourceParam(name="period", enum=["annual", "quarter"]), P_LIMIT],
                     provenance=Provenance(source="FMP (애널리스트 컨센서스)", as_of_field="date", freshness=Freshness.periodic)),
            Resource(name="earnings_calendar",
                     description="실적 캘린더 — 컨센서스 vs 실제 EPS/매출(서프라이즈), FMP 출처.",
                     path="/earnings-calendar", output_model="EarningsCalendarResponse", markets=["US"], cost_tier=CostTier.medium,
                     params=[P_TICKER_REQ, P_LIMIT],
                     provenance=Provenance(source="FMP", as_of_field="date", freshness=Freshness.periodic)),
        ],
    ),
]


# --- resource metadata: category + cadence (single source) -----------------
# Central (connector_id, resource_name) → (category, cadence) map — the ONE place every tool is
# classified for the builder's user-facing CATEGORY and the pin→alert CADENCE (periodic vs one_shot).
# Applied at import; a resource missing here (or a stale entry) fails the load, so every tool —
# present or future — must be classified. Connectors remain the data-plane routing unit; this is
# presentation/periodicity only. Cadence rule of thumb — discrete future event worth pushing? →
# event; price/technicals/daily-flow → daily/intraday; rate/macro release → scheduled; news →
# streaming; a derived/computed snapshot or profile → one_shot.
_RESOURCE_META: dict[tuple[str, str], tuple[Category, Cadence]] = {
    # sec_edgar
    ("sec_edgar", "company_facts"): (Category.fundamentals, Cadence.one_shot),
    ("sec_edgar", "company_search"): (Category.fundamentals, Cadence.one_shot),
    ("sec_edgar", "income_statements"): (Category.fundamentals, Cadence.event),
    ("sec_edgar", "balance_sheets"): (Category.fundamentals, Cadence.event),
    ("sec_edgar", "cash_flow_statements"): (Category.fundamentals, Cadence.event),
    ("sec_edgar", "all_financials"): (Category.fundamentals, Cadence.event),
    ("sec_edgar", "as_reported"): (Category.fundamentals, Cadence.event),
    ("sec_edgar", "filings"): (Category.filings, Cadence.event),
    ("sec_edgar", "earnings"): (Category.filings, Cadence.event),
    ("sec_edgar", "insider_trades"): (Category.gurus, Cadence.event),
    ("sec_edgar", "institutional_holdings"): (Category.gurus, Cadence.event),
    ("sec_edgar", "index_funds"): (Category.gurus, Cadence.event),
    ("sec_edgar", "gurus"): (Category.gurus, Cadence.event),
    ("sec_edgar", "guru_trades"): (Category.gurus, Cadence.event),
    ("sec_edgar", "guru_common"): (Category.gurus, Cadence.event),
    ("sec_edgar", "metrics_snapshot"): (Category.valuation, Cadence.one_shot),
    ("sec_edgar", "comparables"): (Category.valuation, Cadence.one_shot),
    # yahoo
    ("yahoo", "prices"): (Category.market, Cadence.daily),
    ("yahoo", "price_snapshot"): (Category.market, Cadence.daily),
    ("yahoo", "corporate_actions"): (Category.market, Cadence.event),
    ("yahoo", "technical_indicators"): (Category.market, Cadence.daily),
    ("yahoo", "asset_classes"): (Category.market, Cadence.one_shot),
    ("yahoo", "sector_heatmap"): (Category.market, Cadence.one_shot),
    ("yahoo", "commodities"): (Category.market, Cadence.one_shot),
    ("yahoo", "semiconductor"): (Category.market, Cadence.one_shot),
    ("yahoo", "themes"): (Category.market, Cadence.one_shot),
    # fred
    ("fred", "interest_rates"): (Category.macro, Cadence.scheduled),
    ("fred", "interest_rates_snapshot"): (Category.macro, Cadence.scheduled),
    ("fred", "economic_indicators"): (Category.macro, Cadence.scheduled),
    ("fred", "macro_panel"): (Category.macro, Cadence.scheduled),
    # opendart
    ("opendart", "company_facts"): (Category.fundamentals, Cadence.one_shot),
    ("opendart", "company_search"): (Category.fundamentals, Cadence.one_shot),
    ("opendart", "income_statements"): (Category.fundamentals, Cadence.event),
    ("opendart", "balance_sheets"): (Category.fundamentals, Cadence.event),
    ("opendart", "cash_flow_statements"): (Category.fundamentals, Cadence.event),
    ("opendart", "all_financials"): (Category.fundamentals, Cadence.event),
    ("opendart", "filings"): (Category.filings, Cadence.event),
    ("opendart", "earnings"): (Category.filings, Cadence.event),
    ("opendart", "insider_trades"): (Category.gurus, Cadence.event),
    ("opendart", "metrics_snapshot"): (Category.valuation, Cadence.one_shot),
    ("opendart", "comparables"): (Category.valuation, Cadence.one_shot),
    # ecos
    ("ecos", "interest_rates"): (Category.macro, Cadence.scheduled),
    ("ecos", "interest_rates_snapshot"): (Category.macro, Cadence.scheduled),
    # google_news
    ("google_news", "news"): (Category.news, Cadence.streaming),
    # fmp
    ("fmp", "consensus_estimates"): (Category.valuation, Cadence.event),
    ("fmp", "earnings_calendar"): (Category.fundamentals, Cadence.event),
    # kis
    ("kis", "volume_rank"): (Category.market, Cadence.intraday),
    ("kis", "investor_flow"): (Category.gurus, Cadence.daily),
    ("kis", "fluctuation_rank"): (Category.market, Cadence.intraday),
    ("kis", "market_cap_rank"): (Category.market, Cadence.intraday),
    ("kis", "etf_nav"): (Category.market, Cadence.intraday),
    # datasets_store
    ("datasets_store", "screener"): (Category.screener, Cadence.one_shot),
    ("datasets_store", "line_items"): (Category.screener, Cadence.one_shot),
    ("datasets_store", "quant_screen"): (Category.screener, Cadence.one_shot),
    ("datasets_store", "metrics_history"): (Category.fundamentals, Cadence.event),
    ("datasets_store", "ir_materials"): (Category.filings, Cadence.event),
    ("datasets_store", "filing_search"): (Category.filings, Cadence.one_shot),
    ("datasets_store", "valuation"): (Category.valuation, Cadence.one_shot),
    ("datasets_store", "backtest"): (Category.portfolio, Cadence.one_shot),
    # rag
    ("rag", "search"): (Category.filings, Cadence.one_shot),
}


def _apply_resource_meta() -> None:
    """Stamp each resource's user-facing ``category`` + periodicity ``cadence`` from _RESOURCE_META.
    Raises if a resource has no entry (forces every new tool to be classified) or the map has a stale
    entry — the load-time guard that keeps the catalog the single source of truth (RF-07 merged the
    former parallel _CATEGORY / _CADENCE maps + their two apply fns into this one)."""
    actual = {(c.id, r.name) for c in CONNECTORS for r in c.resources}
    missing = actual - set(_RESOURCE_META)
    stale = set(_RESOURCE_META) - actual
    if missing or stale:
        raise RuntimeError(
            f"Catalog resource-meta map out of sync — missing {sorted(missing)}, stale {sorted(stale)}. "
            "Every catalog resource needs a _RESOURCE_META entry (datasets/app/connectors/catalog.py)."
        )
    for c in CONNECTORS:
        for r in c.resources:
            r.category, r.cadence = _RESOURCE_META[(c.id, r.name)]


_apply_resource_meta()


def get_categories() -> list[dict]:
    """Ordered user-facing categories (label + description) for the agent builder."""
    return CATEGORIES


def get_catalog() -> list[ConnectorManifest]:
    return CONNECTORS


def get_connector(connector_id: str) -> ConnectorManifest | None:
    return next((c for c in CONNECTORS if c.id == connector_id), None)


def all_resource_paths(service: str = "datasets") -> set[tuple[str, str]]:
    """(method, path) pairs for connectors served by `service` (default the data plane).
    Other services (e.g. 'rag') are served elsewhere, so their paths are not data-plane routes."""
    return {(r.method.upper(), r.path) for c in CONNECTORS if c.service == service for r in c.resources}
