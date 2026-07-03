# Data & Feature Expansion — research + integration plan (CE)

> **Purpose.** The user wants to keep adding investment/finance/economics **content** on top of the
> current architecture — every feature below must be answerable from **licensed, point-in-time, fully
> cited** data, combined by the multi-agent layer, with **provenance + live evidence**. This doc is the
> research: for each requested feature it maps **what data is needed → which upstream API provides it →
> whether we already have it**, then lays out a one-by-one, verified build plan (the **CE** phase in
> `ROADMAP.md`, now top priority).
>
> **Principles** (from `CLAUDE.md` + memories): maximize the **existing** upstreams first; new data goes
> through a **new connector + manifest entry** (the catalog is the single source REST/MCP/RAG/agent all
> derive from); every figure carries source+as_of+freshness; **no forecasting/advice** (consensus
> *estimates* are sourced DATA, not our prediction — but they sit near the guardrail, so they're flagged
> ❓ for an explicit product decision). When an upstream's coverage/spec is uncertain, it's marked **❓
> CONFIRM** — do not integrate until the user verifies the API.

---

## A. What we already provide (reuse first)

| Connector | Resources | Covers |
|---|---|---|
| `sec_edgar` (US) | company_facts, company_search, filings, earnings, insider_trades, institutional_holdings (13F), index_funds (N-PORT), gurus (superinvestor 13F), metrics_snapshot, comparables, as_reported (XBRL) | US fundamentals, filings, 13F, insider, ETF constituents |
| `opendart` (KR) | company_facts, company_search, filings, earnings, insider_trades, metrics_snapshot, comparables | KR fundamentals, filings, insider |
| `yahoo` | prices (EOD OHLCV), price_snapshot, corporate_actions (div/split), technical_indicators (SMA/EMA/RSI/MACD/Bollinger/vol) | prices (US+KR, also indices/ETF/FX/commodity/crypto **proxy tickers**), technicals |
| `fred` / DBnomics | interest_rates, interest_rates_snapshot, economic_indicators (CPI/unemployment/GDP…) | US + global macro (DBnomics is a huge multi-country catalog) |
| `ecos` (KR) | interest_rates(+snapshot) | Bank of Korea macro |
| `google_news` | news | delayed headlines (RSS) |
| `datasets_store` | screener, line_items, metrics_history | cross-sectional queries over the ingested universe |
| `rag` | document retrieve/rerank + `/evidence` highlight | filing/news passage search + visual evidence |
| **store (PH-PIPE)** | `PriceBar` (OHLCV), `CorporateAction`, `FinancialFact`, `EvidenceDoc` | persisted history for screeners/backtests/charts |

**Compute we already do:** comparables/multiples, financial ratios + history, technical indicators,
13F/guru portfolios, ETF constituents. Several requested features are **new compute over existing data**
(🔵) — no new upstream needed.

---

## B. Feature → data → source map (the 8 categories)

Legend: ✅ have today · 🟡 partial on existing APIs (needs a slice) · 🔵 new compute, **no new upstream** ·
🔴 needs a **new upstream** · ❓ **CONFIRM with user** (spec/coverage/licensing uncertain or guardrail-adjacent).

### 1. 금융시장 현황 (Market overview)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 금융시장 동향 | index levels/changes (S&P500 ^GSPC, KOSPI ^KS11…), breadth, movers | yahoo indices ✅; **gainers/losers/most-active** rankings | 🟡 indices ✅ · movers 🔴 (US: FMP/Finnhub ❓ · KR: KIS) |
| 섹터 히트맵 | sector membership + per-sector return | sector ETFs (XLK/XLF/…) via yahoo prices → compute; KR sector indices | 🔵 US (sector-ETF compute) · 🔴 KR sector indices (KRX/KIS ❓) |
| 기술지표 탐색 | OHLCV + indicator math, screenable across universe | yahoo + **PriceBar** store | 🟡 (have per-ticker; cross-universe = new slice on PriceBar) |

### 2. 프리미엄 뉴스룸 (Premium newsroom)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 실시간 속보 | low-latency news stream | google_news (delayed) → **realtime** feed | 🔴 (Finnhub/Benzinga/Polygon news ❓) |
| 실시간 내러티브 | news + LLM synthesis (what's the story) | rag + gemini over ingested news | 🔵 (feature on existing) |
| 프리미엄 뉴스 | licensed premium wire | Reuters/Bloomberg/Benzinga | 🔴❓ (licensing) |

### 3. 거시경제 분석 (Macro analysis)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 국가경제 분석 | per-country indicator panels | FRED + DBnomics (multi-country) ✅ | 🟡 (expand indicator set) |
| 하위 요인 분석 | indicator components (e.g. CPI breakdown) | FRED/DBnomics component series | 🟡 (more series + grouping) |
| 경제지표 일정 | upcoming release calendar | FRED release dates / econ-calendar API | 🔴❓ (FRED has release schedule; full calendar = FMP/Finnhub/TradingEconomics ❓) |
| 경제지표 열람 | browse/search the indicator catalog | DBnomics catalog ✅ | 🟡 (catalog browse UI) |
| 사이클 분석 | leading/lagging composites, regime | FRED/DBnomics series → compute | 🔵 |
| 자산군 | cross-asset prices (equity/bond/commodity/FX/crypto) | yahoo proxy tickers (^TNX, GC=F, KRW=X, BTC-USD…) | 🟡 (curate the cross-asset set) |

### 4. 밸류에이션 (Valuation / quant)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 퀀트 탐색 | factor values across universe (value/quality/momentum…) | FinancialFact + PriceBar → compute factors | 🔵 (needs broad backfill) |
| 스크리너 | screen by financial+price criteria | datasets_store screener ✅ | 🟡 (expand criteria: price/technical/factor) |
| 백테스터 | historical prices + portfolio sim | PriceBar → backtest engine | 🔵 (new compute) |

### 5. 투자거장 분석 (Guru analysis)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 거장 매매 | 13F quarter-over-quarter deltas (new/added/trimmed/exited) | sec_edgar gurus/13F → compute deltas | 🔵 |
| 거장 포트폴리오 | latest 13F holdings | sec_edgar gurus ✅ | ✅ |
| 공통 보유종목 | intersection across gurus | gurus 13F → compute | 🔵 |

### 6. 종목 재무분석 (Stock fundamentals)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 종목 개요 | profile + snapshot | company_facts + price_snapshot ✅ | ✅ |
| 재무제표 | 3 statements (+as-reported) | sec_edgar/opendart ✅ | ✅ |
| 어닝콜 (US) | earnings-call transcript text | Alpha Vantage → RAG (`transcript_text` pipeline) | ✅ (US; free key, 25 calls/day) |
| 발표자료 (US) | 8-K EX-99 investor/earnings decks | SEC EDGAR PDF → Document AI Layout Parser → RAG + in-app pdf.js viewer (`presentation_text`) | ✅ (US; needs DocAI) |
| 실적공시 (KR) | 잠정실적 공정공시 = 경영진 잠정실적+코멘터리 | OpenDART `list.json`→`document.xml` → RAG + in-app DART viewer (`kr_earnings` pipeline) | ✅ (KR analog of 어닝콜 — no free KR transcript/audio API) |
| IR자료실 (KR decks/audio) | investor presentation decks / call audio | company IR / KIND / Quartr·FnGuide(유료) | 🔴❓ (no clean free API; KR transcript·deck gated → paid or scrape) |
| 실적 및 전망 | actuals ✅ + **consensus estimates** | estimates provider | 🔴❓ (estimates = FMP/Finnhub; **guardrail-adjacent** — show as sourced consensus, not our forecast) |
| 내부자 거래 | Form 4 / 임원·주요주주 | sec_edgar/opendart insider_trades ✅ | ✅ |
| 실적 발표 일정 | upcoming earnings dates | earnings-calendar API | 🔴❓ (FMP/Finnhub; or our Disclosure-Calendar `next_expected_update`) |
| 종목 내러티브 | the stock's data + filings + news → story | rag + gemini | 🔵 |

### 7. 종목 가치평가 (Valuation models)
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 상대가치평가 | peer multiples | comparables ✅ | ✅ |
| DDM | dividends + user assumptions | corporate_actions + financials → model | 🔵 |
| RIM (잔여이익) | book value + earnings + cost of equity | financials → model | 🔵 |
| Index DCF / Reverse DCF / Simplified DCF | FCF, growth/discount inputs | financials → DCF engine | 🔵 (⚠ frame as a **transparent model with user inputs**, not our price target — guardrail) |

### 8. 포트폴리오 관리 + 산업 분석
| Feature | Data needed | Source | Status |
|---|---|---|---|
| 포트폴리오 대시보드/분석 | user holdings + prices + fundamentals | new `Portfolio`/`Holding` model + PriceBar | 🔵 (new product data) |
| 인기 산업 탐색 | industry performance/flows | sector/industry ETFs + membership | 🟡/🔴 (KR membership ❓) |
| 관전 포인트 | LLM-curated highlights | existing data + gemini | 🔵 |
| 밸류체인 | supplier/customer graph | derive from filings via RAG/LLM, or a dataset | 🔴❓ (no free clean API; LLM-extract from filings) |

---

## C. Upstream API research (existing-first, then candidates)

**Already integrated / free (use to the max):**
- **SEC EDGAR** — US fundamentals, filings, 13F, insider, N-PORT, XBRL. Free.
- **OpenDART** — KR fundamentals, filings, insider. Free key (have).
- **Yahoo Finance** (via our provider) — EOD OHLCV for stocks **and** index/ETF/FX/commodity/crypto proxy
  tickers, corporate actions. Free, keyless. (Cloud-IP fragility noted; pykrx similarly.)
- **FRED + DBnomics** — US + global macro, large catalog, release dates (FRED). Free (DBnomics keyless).
- **ECOS** — KR macro. Free key.
- **Google News** — delayed headlines. Free.

**Candidate NEW upstreams for the gaps** (ranked by coverage-per-integration; all platform-held keys per
the subscription model, [[monetization-subscription]]):
1. **Financial Modeling Prep (FMP)** — *one key covers many gaps*: analyst **estimates**, **earnings/
   economic/IPO/dividend calendars**, **market movers / sector performance**, screener factors, DCF
   inputs, ratios. We already reserve `FMP_API_KEY` (deferred adapter PH-DEFER). **Highest leverage.**
   ❓ confirm tier/limits + that the specific endpoints we need are in the plan you'd buy.
2. **Finnhub** — alternative/complement: realtime-ish news, estimates, earnings + econ calendars,
   insider sentiment, peers. Generous free tier. ❓ confirm.
3. **KIS (한국투자증권)** — KR realtime/intraday, **investor flows**, **rankings (movers)**, ETF NAV.
   Already planned (KIS-* tasks). The KR half of movers/flows/realtime.
4. **Polygon / Tiingo** — prices + news (keys reserved, PH-DEFER). Mainly if Yahoo proves too fragile.
5. **KRX open API** — KR sector indices / market stats (have `KRX_API_KEY`). ❓ confirm endpoints.

**Guardrail note (important).** "전망" / estimates / earnings dates / target prices brush against the
no-forecast rule. Policy: **consensus estimates + calendars are licensed third-party DATA** and may be
shown **as sourced facts attributed to the provider** (e.g. "consensus EPS 2.10, source: FMP, as_of …"),
never re-stated as *our* prediction; DCF/DDM/RIM are **transparent models driven by the user's own
inputs**, labeled as such. This must be an explicit product decision (see Open Questions).

---

## D. Build plan — one-by-one, verified (the CE phase)

Ordering: **(1)** ship everything possible on EXISTING/free data first (no new upstream, fastest, fully
sourced), **(2)** then the confirmed new upstreams. Each task = new connector/manifest entry (or store +
compute) + unit tests + an eval scenario + the agent able to use it + provenance/evidence wired + docs/
roadmap updated (Definition of Done).

**Wave 0 — broad backfill foundation** (everything quant/screener/backtest/heatmap needs data in the store)
- **CE-0** — widen the scheduled universe + run the prices/financials pipelines to depth, so PriceBar +
  FinancialFact cover the screenable universe (builds on PH-PIPE; dynamic S&P500/KOSPI/KOSDAQ).

**Wave 1 — existing data, new compute (no new upstream)** — highest ROI, fully cited
- **CE-1 · 자산군 (cross-asset)** — curated index/bond/commodity/FX/crypto proxy set via yahoo → a market
  dashboard + asset-class view. 🔵
- **CE-2 · 섹터 히트맵 (US)** — sector-ETF set → per-sector return heatmap (descriptive, sourced). 🔵
- **CE-3 · 거장 매매 + 공통 보유** — 13F deltas (Δ quarter) + cross-guru intersection over existing gurus. 🔵
- **CE-4 · 종목 내러티브 / 관전 포인트** — gemini synthesis over a stock's facts+filings+news (RAG). 🔵
- **CE-5 · 밸류에이션 모델 (DCF/DDM/RIM/Reverse/Simplified)** — model engine over financials + user inputs,
  labeled as models (guardrail-safe). 🔵
- **CE-6 · 퀀트 탐색 + 스크리너 확장** — factor compute over FinancialFact+PriceBar; expand screener criteria. 🔵
- **CE-7 · 백테스터** — portfolio backtest engine over PriceBar (descriptive performance, no advice). 🔵
- **CE-8 · 포트폴리오 (대시보드/분석)** — new `Portfolio`/`Holding` product model + analytics over PriceBar. 🔵
- **CE-9 · 거시 확장 (국가/하위요인/사이클/열람)** — broaden FRED/DBnomics indicator catalog + component
  grouping + cycle composites + a browse UI. 🟡
- **CE-10 · 실시간 내러티브** — LLM narrative over the (existing) news ingestion. 🔵

**Wave 2 — new upstreams** (build start on hold per the user; CE-11 upstream + policy now CONFIRMED)
- **CE-11 · 시장 movers / 실적·경제 캘린더 / 추정치** via **FMP** (confirmed) → market movers, earnings/econ
  calendars, consensus estimates (실적 및 전망, 실적 발표 일정, 경제지표 일정), shown as sourced data. 🔵 (ready)
- **CE-12 · KR 실시간·플로우·랭킹·ETF NAV** via KIS (KIS-* tasks) → KR movers/flows/realtime, KR sector. 🔴
- **CE-13 · 실시간/프리미엄 뉴스** via the confirmed news provider (Finnhub/Benzinga/Polygon). 🔴❓
- **CE-14 · IR자료실 + 밸류체인** — IR decks (8-K exhibits/DART/scrape) + value-chain graph (LLM-extract
  from filings). 🔴❓

**Every CE task keeps the interface stable:** new connector → manifest entry (integrity test asserts the
path is a real route) → auto-flows into REST docs, MCP tools, RAG registration, entitlement, metering, the
agent's tool list, and the chart/evidence/provenance pipeline. No forked surfaces.

---

## E. Decisions (2026-06-22) + remaining open items

**Decided by the user:**
1. ✅ **Primary gap-filler upstream = FMP** (Financial Modeling Prep) — one platform-held key for
   estimates + earnings/econ/IPO/dividend calendars + market movers + sector performance + screener/DCF
   inputs. `FMP_API_KEY` already reserved. (Finnhub/Polygon stay as fallbacks only.)
2. ✅ **Estimates + calendars are allowed as SOURCED DATA** — show consensus estimates / earnings & econ
   dates **attributed to the provider** ("consensus EPS 2.10 · source FMP · as_of …"), never re-stated as
   *our* forecast/target. DCF/DDM/RIM stay transparent user-input models. The guardrail still refuses
   *our* predictions/advice; third-party data with provenance is fine.
3. ✅ **Build start: HOLD.** Plan is approved for review; do NOT start CE tasks until the user gives the go.

**Still open (decide before those specific tasks):**
- **CE-13 realtime/premium news** — provider/budget not chosen yet (FMP has some news; else Finnhub free
  vs Benzinga/Polygon paid). Confirm before CE-13.
- **CE-14 IR materials & value chain** — no clean free API; propose deriving (IR from SEC 8-K exhibits/
  DART; value chain via LLM extraction from filings, labeled "derived"). Confirm before CE-14.
