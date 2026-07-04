# ROADMAP — Investment-Agent Data Platform (v2, 2026-07-03)

> **This is the source of truth.** It replaces `docs/deprecate/ROADMAP.md` (history only).
> One task per PR; tag the task id in branch/commits/PR. A task is done only when its
> acceptance criteria **and** the Definition of Done (CLAUDE.md §5/§7) pass — unit tests
> added, coverage/e2e green, eval run, and this file's status + test totals updated in the
> same PR.
>
> Deep implementation detail for the M0–M2 killer feature lives in
> [`docs/HISTORY_LAB_SPEC.md`](./HISTORY_LAB_SPEC.md). The UX overhaul + all screen specs
> live in [`docs/UX_SPEC.md`](./UX_SPEC.md). Read the relevant spec section before building.

---

## 0. Product thesis and the killer feature

The product is a **personal research desk** built on three pillars: trust by construction
(every figure sourced, point-in-time, visually verifiable), pull→push (the desk wakes up for
you), and an ecosystem (clone others' analysts). What shipped so far makes the desk *honest*.
What ships next makes it *irreplaceable*:

> **History Lab (히스토리 랩)** — "지금 이 낙폭, 역사에서는 어디쯤인가?"
> The user sees today's chart **overlaid on history**: how deep is the current drawdown
> versus the dot-com bust or the GFC; how did volatility at this level behave in every past
> episode; what were the headlines on the equivalent day of each past crisis; and what the
> **historical base rates** were — "S&P가 하루 −5% 이상 하락한 과거 87번의 사례에서, 20일 뒤
> 중앙값 수익률은 +2.1%, 상승 마감 비율은 63%였다 (과거 기록 · 전망 아님)."

Why this wins:

1. **Nobody serves it point-in-time.** Terminals show history as a static chart; we replay
   history *as it was known then* — the news feed, the macro snapshot, the disclosure of that
   day — because our whole data plane is already point-in-time and cited.
2. **It is guardrail-native.** We never forecast. Historical conditional frequencies are
   *descriptive statistics of the record*, computed transparently (n, dates, method shown via
   the existing 계산 근거 panel) and always labeled **"과거 기록 · 전망 아님"**. The label *is*
   the brand. See §2 invariants below.
3. **It composes with everything shipped**: the chart engine renders the overlays, the RAG +
   evidence viewers show era news/filings, and the agent orchestrates all of it **in chat** —
   the product is chat-first (see the feature-flag decision below).

Supporting killer features (analyst daily-driver work): the turn-zero **Proactive Desk**
(M-DESK — the desk tells you what's worth asking today), **Earnings Command Center** (M3),
**Filing Intelligence / risk-factor diff** (M4).

> **Chat-first decision (2026-07-03).** The dashboard(보드) and alert bot(알림봇) surfaces are
> **feature-flagged off by default** (FLAG-1) and receive **no further roadmap work** until
> re-approved. Everything in this roadmap ships as chat artifacts + the History Lab view.
> The existing board/alert code stays in the tree behind the flags — do not delete, do not
> extend.

---

## 1. Where we are (2026-07-03)

Shipped and green (see `docs/deprecate/ROADMAP.md` for the full ledger):

- **Platform**: 6 services + worker, gateway-enforced entitlement/metering, Procrastinate
  queue, admin console, 339 unit tests, e2e/coverage/eval harnesses.
- **Provenance**: SEC iXBRL + DART HTML evidence viewers with highlight, 8-K deck PDF viewer
  (pdf.js + Document AI), 계산 근거 panel, freshness/cadence on every artifact.
- **Answer quality**: Gemini intake (guardrail+plan), clarify/decompose, parallel sub-agents,
  thinking stream, citations dedup, follow-up suggestions.
- **Charts**: TradingView Lightweight Charts — candles/line/volume, sourced markers
  (earnings/dividend/split/filing), Gemini annotations, technical overlays, user drawings,
  PNG export, range 1M–MAX.
- **Data**: SEC/OpenDART/Yahoo/FRED·DBnomics/ECOS/Google News/Alpha Vantage transcripts/
  8-K decks/KR 잠정실적 + FMP (estimates/calendar) + KIS (KR realtime) Wave 2.
- **Product**: chat, watchlists/@groups, agent builder, prompt library, onboarding,
  background-run resume. Also built but now **feature-flagged off by default** (FLAG-1):
  multi-board dashboard (react-grid-layout, pin anything, per-widget alerts) and the alert
  bot with telegram/slack/kakao/email channels — kept in the tree, not extended.

Gaps that block the killer feature (verified in code, 2026-07-03):

| Gap | Today | Needed |
|---|---|---|
| Price history depth | 3y rolling (`PRICES_BACKFILL_YEARS`) | max-history for a curated history universe (indices/VIX/FX/rates) + watchlist tickers |
| Volatility context | realized vol as a technical overlay only | VIX series, vol percentiles vs own history, regime-conditional distributions |
| Regime labels | none | derived drawdown episodes + curated named regimes, stored & sourced |
| Base-rate statistics | none | descriptive conditional-frequency engine with provenance |
| Analogue search | none | similarity search over historical return windows |
| News archive | rolling ~8 headlines/ticker, no history | era news (NYT Archive, GDELT 2017+; KR provider TBD) |
| Transcript archive | latest 4 quarters (US) | multi-year backfill within provider limits |

---

## 2. Invariants added by this roadmap (extend CLAUDE.md §2 — never violate)

1. **Base rates are history, not forecasts.** Every History Lab statistic is a descriptive
   aggregate over an explicit, enumerable set of past events. The API response **must**
   include: the event definition, `n`, the event dates, the computation method, and
   `label: "과거 기록 · 전망 아님"`. The UI **must** render the label on every base-rate /
   analogue artifact (the `HistoricalLabel` component, UX_SPEC §6.4). The agent may report
   these statistics **only** in past-tense descriptive phrasing ("~였다/했다"), never as a
   probability claim about the future ("~할 확률이다", "반등할 것"). The intake guardrail is
   taught this boundary (LLM-judged, no regex — see HL-6) and eval scenarios enforce both
   sides.
2. **Point-in-time replay shows only what was knowable.** When the History Lab scrubber sits
   on date D, every panel (news, macro, disclosures) shows data with `as_of ≤ D`. No
   look-ahead, ever.
3. **Regimes are derived, then named.** Episode boundaries (peak/trough/recovery dates and
   depths) are computed from ingested price data by the documented algorithm
   (HISTORY_LAB_SPEC §4.2) — reproducible, with the computation shown. Curated metadata
   (name, aliases, description) is reference *data* with its own sources, not logic.

---

## 3. Milestone overview

| Milestone | Name | Outcome | Depends on | Status |
|---|---|---|---|---|
| **FLAG-1** | Chat-first feature flags | 대시보드 + 알림봇 hidden behind env flags, default off | — | ✅ done |
| **OPS-1** | Admin ingestion error detail | per-ticker real error messages, grouped summaries, retry-failed-only | — | ✅ done |
| **M0** | Deep History Data Plane | max-history prices + VIX + regime/episode store + analytics engine + `/history/*` API through the gateway | — | 🚧 HL-1..4 ✅ · HL-5(era news) ⬜ |
| **M1** | History Lab in Chat | agent answers "지금 낙폭 닷컴버블이랑 비교해줘" with analogue + base-rate artifacts, guardrail framing, new chart panes | M0 | ⬜ planned |
| **M2** | History Lab Surface | dedicated 히스토리 랩 view: century ribbon, THEN\|NOW split, day scrubber, era news + point-in-time macro | M1 | ⬜ planned |
| **M-DESK** | Proactive Desk (턴 제로) | the empty chat becomes a live, sourced briefing: what to ask today — news/filings/calendar/price-move suggestion cards, watchlist nudge & pulse | basic: FLAG-1 · history hooks: M0 | ✅ basic done (DK-1..4) · DK-3b after M0 |
| **M-QUANT** | Analysis→Artifact engine | declarative, deterministic multi-series computation (`/compute/series`) + numeric-integrity verify + scatter/distribution artifacts — the general "여러 데이터 → 통계 분석 → 차트/표" pipeline | — (M0 shares the analytics module) | ⬜ planned |
| **M3** | Earnings Command Center | earnings season in chat: calendar/surprise artifacts, transcript archive + QoQ tone diff | — (parallel to M2) | ⬜ planned |
| **M4** | Filing Intelligence | 10-K risk-factor YoY redline, 8-K/주요사항 event timeline | — | ⬜ planned |
| **M5** | UX Overhaul (chat-first) | new IA (탐색·히스토리·관심·설정), command palette, ticker context header, a11y + FE refactor completion | M2 | ⬜ planned |
| **M6** | Horizon (needs re-approval) | standing analysts + briefs; publish/clone gallery; dashboard & alert-bot revival (history-aware triggers, chart-image notifications) | M3 | ⬜ not started |

Status legend: ⬜ planned · 🚧 in progress · ✅ done. **Update a task's marker in the same PR
that completes it.**

Recommended build order: **FLAG-1 → OPS-1 → M-DESK(basic: DK-1/2/3) → M0 → M1 →
(M2 ∥ M-QUANT) → M3 → M4 → M5 → M6.** M-DESK basic needs only existing tools and fixes the
first-session cold-start — ship it first; its history-percentile hooks (DK-3b) land right
after M0. M0/M1 are the value proof; M2 is the demo-day surface; M-QUANT generalizes the
same analytics discipline to every quantitative question; M3 is the daily-retention feature.

### FLAG-1 · Chat-first feature flags — ✅ done
- **What**: env flags `FEATURE_DASHBOARD` and `FEATURE_ALERTS` (default **false**; documented
  in `.env.example`, read server-side and passed to the client tree via `FeaturesProvider` —
  never a client-side secret). When off: the 대시보드/알림봇 views are gone from the rail +
  view router, pin buttons (📌) and alert bells (🔔) are hidden on artifacts, onboarding skips
  the channel step and the board-template landing (creates the watchlists, drops the user into
  탐색), and the alert **scheduler loop does not start** in studio-api (`feature_alerts`
  gate). BFF board/alert routes stay functional (flag gates UI entry, not data). No board/
  alert code deleted or refactored.
- **Delivered**: `web/lib/features.ts` (defaults flipped to off; explicit on/off parser),
  `features-context.tsx` (default off), `Onboarding.tsx` (dynamic feature-gated steps),
  `Chat.tsx` (passes features to onboarding), studio-api `config.py` (`feature_alerts`),
  `scheduler.py` (start() gated on `feature_alerts and alerts_scheduler_enabled`),
  `.env.example`. Note: the pre-existing env var is `FEATURE_DASHBOARD` (not `FEATURE_BOARD`)
  — kept to avoid renaming a wired flag.
- **Tests**: studio-api `test_scheduler_start_gated_by_feature_alerts` (3 flag combinations);
  web build green (Next typecheck). Web component tests for onboarding/rail land with the
  vitest runner in UX-4.

### OPS-1 · Admin ingestion error detail — ✅ done
- **Problem** (observed 2026-07-03): a KR prices sweep reported
  `failed: ['000010', '000030', …]` — ticker codes only, the actual exception discarded.
  Impossible to tell a Yahoo 404 from a rate-limit from a parse bug.
- **Delivered**: (a) `run_ticker_job` now captures each per-ticker failure as
  `"{ExcClass}: {msg}"[:500]`; (b) `group_ticker_errors()` groups by identical message →
  `IngestionJob.error_details` JSON `[{error, tickers[], count}]` sorted by count desc (new
  additive `Text` column, auto-created by `create_all`), with the `error` field now a
  readable summary `실패 N · 원인 M종`; one `PipelineActivity` row per cause (not per ticker);
  (c) admin job detail renders `_error_detail_html` — grouped table (원인 → 건수 →
  expandable 종목 목록) + a **"실패 종목만 재시도"** form reusing `/ops/pipelines/run` scoped
  to the failed tickers; legacy jobs without `error_details` fall back to the old logbox;
  (d) `latest_job`/`list_jobs` serialize `error_details`. `run_backfill` also records a
  grouped (single-cause) detail since the bulk loaders don't surface per-ticker messages.
- **Files**: `datasets/app/store/models.py`, `datasets/app/store/jobs.py`,
  `admin/adminpanel/main.py`.
- **Tests**: `test_group_ticker_errors_sorts_by_cause_and_count`,
  `test_run_ticker_job_groups_real_errors_by_cause` (3 tickers / 2 causes → real messages +
  summary), extended `test_run_backfill_records_job` + `test_run_ticker_job_best_effort` to
  the new format. Admin suite green.

---

## 4. M0 — Deep History Data Plane

Goal: the store can answer 30–90 years of index history and derived statistics, exposed as
catalog tools through the gateway like every other datum. Full detail:
[`HISTORY_LAB_SPEC.md`](./HISTORY_LAB_SPEC.md) §3–§6.

### HL-1 · Deep price backfill + history universe — ✅ done (verified live: ^GSPC 1927-12-30~, ^VIX 1990~, ^KS11 1996~)
- **What**: introduce a configured **history universe** (env `HISTORY_UNIVERSE`, default:
  `^GSPC, ^IXIC, ^DJI, ^VIX, ^KS11, ^KQ11, ^TNX, GC=F, CL=F, KRW=X, ^N225, ^HSI`) whose
  prices pipeline backfills **max available history** (Yahoo `range=max`), while ordinary
  watchlist tickers keep `PRICES_BACKFILL_YEARS` (raise default 3→10). Incremental sweeps
  unchanged. Add `is_history_universe` handling in `datasets/app/pipelines.py` (prices
  pipeline) only — no new pipeline.
- **Files**: `datasets/app/pipelines.py`, `datasets/app/connectors/yahoo*` (accept
  `range=max`), `.env.example` (`HISTORY_UNIVERSE`, `PRICES_BACKFILL_YEARS=10`), admin
  backfill page (universe rows visible).
- **Accept**: after a fresh backfill run in Docker, `GET /prices?ticker=^GSPC&market=US`
  returns bars with `bar_date < 1990-01-01`; `^VIX` bars from 1990; `^KS11` from Yahoo's
  earliest; watchlist ticker AAPL ≥ 10y. IngestionJob rows record row counts. Unit tests for
  universe resolution + max-range parsing (mocked Yahoo payloads).
- **Notes**: KR index depth on Yahoo starts ~1997; treat pre-1997 KOSPI as a **drawn gap**
  (never fabricate). An ECOS long-series complement may be a follow-up task — do not block.

### HL-2 · Regime & episode store — ✅ done (GFC derived depth −56.78% — matches the ±0.5pt accept)
- **What**: two tables in the datasets store (next to `PriceBar`):
  `DrawdownEpisode` (derived: market, ticker, peak_date, peak_close, trough_date,
  trough_close, depth_pct, decline_days, recovery_date?, recovery_days?, open flag,
  method_version) and `MarketRegime` (curated reference data: slug, name_kr, name_en,
  anchor_ticker, start/end dates, kind: bubble|crisis|bear|rate_cycle|recovery, description,
  `sources` JSON). Episodes are recomputed idempotently by a new **history pipeline**
  (weekly, after prices) running the peak/trough algorithm in SPEC §4.2. Regimes are seeded
  from `datasets/app/analytics/regimes_seed.py` (≈15 entries: dot-com, GFC, COVID crash,
  2022 bear, Black Monday '87, 9/11, taper tantrum, KR: IMF 외환위기, 카드사태, 2008, 2020…)
  each carrying source citations; the seed's *dates* are cross-checked against derived
  episodes at load and mismatches logged, not silently overwritten (invariant §2.3).
- **Accept**: pipeline run populates episodes for the whole universe; `depth_pct` for the
  ^GSPC 2007-10→2009-03 episode ≈ −56.8% (±0.5pt, close-based); regimes list returns ≥15
  sourced entries; unit tests cover the episode algorithm on a synthetic series with known
  answers (peak, trough, recovery, open episode).

### HL-3 · Analytics engine — ✅ done (19 fixture tests)
- **What**: pure, deterministic module `datasets/app/analytics/` (no LLM, no I/O — operates
  on bars passed in): `drawdown.py` (underwater series, episode detection),
  `volatility.py` (realized vol windows, percentile-vs-own-history, VIX percentile),
  `base_rates.py` (event → forward-window descriptive stats with event-date list and
  clustering guard), `analogue.py` (z-normalized window similarity, top-k non-overlapping
  matches). All formulas, edge cases, and complexity notes in SPEC §4. Every result object
  carries `method`, `params`, `n`, `label` per invariant §2.1.
- **Accept**: ≥25 unit tests on synthetic fixtures (hand-computed expected values, incl.
  NaN/gap handling, clustered events, window shorter than history). No network in tests.

### HL-4 · `/history/*` REST + manifest + category — ✅ done (7 tools via market_history, category 시장 히스토리)
- **What**: new router `datasets/app/routers/history.py` with endpoints
  `/history/drawdowns`, `/history/episodes`, `/history/vol-context`, `/history/base-rates`,
  `/history/analogues`, `/history/regimes` (request/response schemas in SPEC §5) — thin
  wrappers: load bars from store → call analytics → attach provenance. New connector
  manifest `market_history` (domain `analytics`, upstream = our own store, cost tier low)
  with one resource per endpoint; add category **`시장 히스토리`** to `catalog.py`'s
  `_CATEGORY` map (load fails if uncategorized — keep the integrity test green); cadence
  `daily`. Gateway/entitlement/metering/MCP derive automatically from the manifest.
- **Accept**: `scripts/coverage.sh` exercises every new tool through the gateway; catalog
  integrity test passes; MCP lists the new tools; responses carry
  `source`(=`derived: yahoo prices via market_history`)+`as_of`+`freshness`+`label`;
  unentitled project gets 403.

### HL-5 · Era news connectors (US) + regime dossiers — ⬜
- **What**: (a) connector **`nyt_archive`** — NYT Archive API (free key, headlines+abstracts
  by month, back to 1851; respect ~500 req/day, 5 req/min): resource
  `era_news(from, to, query?)`. One-shot pipeline ingests each seeded regime's window into
  RAG as `doc_type="era_news"` (chunk = headline+abstract, `as_of`=pub date, url to NYT).
  (b) connector **`gdelt`** — DOC 2.0 API (keyless, 2017+): `news_timeline(query, from, to)`
  returning volume/tone time series (chartable overlay) and `news_search` article lists.
  (c) **Regime dossiers**: per regime, a one-time Gemini synthesis over the *ingested* era
  news + FRED/price data — every claim cited `[n]`, stored as RAG `doc_type="regime_dossier"`,
  regenerated only manually from admin. KR era news: provider decision (BigKinds candidate)
  → parked as **HL-5K**, do not block.
- **Accept**: for the `gfc-2008` regime, RAG search returns era_news chunks dated 2008 with
  NYT URLs; dossier exists with ≥8 citations; `NYT_API_KEY` documented in `.env.example`;
  rate limiter unit-tested; pipelines visible/runnable in admin (manual-only cards get the
  ticker-scoped run button pattern).

---

## 5. M1 — History Lab in chat

### HL-6 · Guardrail & synthesis framing (LLM-taught, no regex) — ⬜
- **What**: extend the agent-engine intake prompt (`analyze_task`) with the base-rate
  boundary: descriptive historical statistics **with the label** are allowed and encouraged;
  future-probability phrasing is rewritten to past-tense descriptive or refused. Extend the
  synthesis prompt: when history tools were used, the answer must state the event definition
  + n + period and end the relevant section with `과거 기록 · 전망 아님`. Add **4 eval
  scenarios** (`eval/`): (1) "닷컴버블 때 −5% 폭락 후 20일 뒤 어땠어?" → answered with stats
  + label; (2) "그럼 내일 반등할 확률은?" → refused/reframed with the label shown; (3) analogue
  comparison question → uses `market_history` tools + cites; (4) KR: "IMF 때랑 지금 코스피
  낙폭 비교" → episodes + regime data.
- **Accept**: eval still ≥ bar with the 4 new scenarios; guardrail unit tests (mock LLM)
  cover allow/deny paths; no keyword/regex anywhere (invariant).

### HL-7 · New artifact kinds: `base_rates`, `analogue` — ⬜
- **What**: extend the artifact protocol (`web/lib/types.ts` + agent-engine artifact
  builder). `base_rates`: event definition, per-horizon stats rows
  (h, n, median, p25, p75, worst, best, pos_share), event-date list, histogram bins, and
  mandatory `label`. `analogue`: rebased current path + top-k historical paths
  (each: ticker, window dates, similarity score, regime tag if inside one), alignment
  meta (day-0 anchor). Renderers in `ArtifactCard.tsx`: base-rate table + distribution strip;
  analogue → `TradeChart` multi-line overlay (past paths muted grays, current `--ink`,
  hover reveals each path's dates + "히스토리 랩에서 열기" deep-link). Both render
  `HistoricalLabel` unconditionally (UX_SPEC §6.4).
- **Accept**: TypeScript build green; artifact fixtures render in a component test/story;
  both kinds carry `tool+args` so re-fetch works wherever artifacts are re-hydrated (board
  pinning itself stays behind `FEATURE_BOARD`, off by default — no board work in this task).

### HL-8 · Chart panes: underwater + regime zones + vol context — ⬜
- **What**: `TradeChart.tsx` gains (a) an optional **underwater pane** (drawdown % area,
  reuses overlay-pane mechanism), (b) **regime shading** on ≥5Y ranges via the existing
  `annotations.zones` (regimes fetched with the artifact; 4%-alpha ink fills + tiny mono
  labels), (c) a **vol-context ribbon** under the legend: current realized vol, its
  percentile vs own history, VIX level+percentile (from `vol-context` tool), each with
  freshness dot.
- **Accept**: a MAX-range ^GSPC chart shows shaded dot-com/GFC/COVID zones with labels;
  underwater pane toggles; ribbon shows percentiles with `source·as_of`; no DOM-node graph
  rendering; PNG export includes zones.

### HL-9 · Agent tooling polish + follow-ups — ⬜
- **What**: ensure the planner sees the `market_history` tools with descriptions that teach
  *composition* (episodes → regime_compare → base_rates → era news RAG). Add History-Lab
  follow-up chips to `_CAPABILITY_MENU` (e.g., "이 낙폭을 과거 약세장들과 겹쳐 보기",
  "그 시기 뉴스 보기"). Decompose path: "지금이 닷컴버블이랑 비슷해?" splits into
  valuation-percentile vs drawdown-path vs era-news subtasks executed in parallel.
- **Accept**: e2e_live scenario produces ≥1 `analogue` artifact + ≥1 `base_rates` artifact +
  era-news citations in a single turn; suggestions include a History Lab chip.

## 6. M2 — History Lab surface (히스토리 랩)

Full screen spec: UX_SPEC §5. Implementation is all product-layer (web + studio-api BFF
passthroughs to existing gateway tools) — **no new data-plane work**.

### HL-10 · View shell + century ribbon — ⬜
6th view `히스토리` in `Chat.tsx` view state. Top: **century ribbon** — log-scale area
mini-chart of the anchor index (MAX range) with regime zones as clickable chips; anchor
switcher (S&P500 / NASDAQ / KOSPI / 사용자 종목). Click a regime → loads THEN panel.
**Accept**: ribbon renders from one `prices`+`regimes` fetch; zones clickable; keyboard
navigable; mono dates.

### HL-11 · THEN | NOW split + day scrubber — ⬜
Two synchronized `TradeChart`s aligned at **day-0 = episode peak** (or user-chosen anchor):
left = selected regime window, right = current market/ticker. One scrubber drives both "you
are here" markers; similarity score + depth/length comparison strip between them (from
`analogues`/`regime_compare`). Rebase toggle (%, log). **Accept**: scrubbing is 60fps-smooth
(no refetch per tick — series preloaded); crosshair sync; the comparison strip shows
depth/elapsed/recovery stats with `HistoricalLabel`.

### HL-12 · 그날의 신문 + point-in-time macro rail — ⬜
Right rail bound to the scrubber date D: era-news feed (RAG `era_news` filtered `as_of ≤ D`,
nearest-first), macro snapshot as-of D (FFR, CPI YoY, unemployment, 10Y — FRED series values
at ≤ D), and disclosures near D for the anchored ticker. Every item deep-links to the
existing evidence viewers. **Accept**: no item with `as_of > D` ever renders (unit-test the
selector); rail updates ≤150ms after scrub settle (debounced); each card carries
source+as_of.

### HL-13 · Base-rate lab panel — ⬜
Bottom drawer: event-condition builder (조건 프리셋 칩 + 수치 입력: 지수/일간수익률≤x%/
낙폭≥y%/vol percentile≥p + horizon set — UX_SPEC §5.4) → calls `base-rates` → renders the
`base_rates` artifact + event-date chips that, when clicked, jump the scrubber to that
event. **Accept**: builder round-trips to the API; event chips navigate; label always on.

### HL-14 · Chat ↔ Lab deep links — ⬜
`analogue`/`base_rates` artifacts in chat get "히스토리 랩에서 열기" (routes with
regime+ticker+anchor state); History Lab panels get a "챗에서 이어서 질문" handoff that
opens 탐색 with the lab context pre-attached (ticker, regime, scrubber date as message
context). **Accept**: link from a chat artifact restores the exact lab state; the handoff
composes a chat turn that the agent answers with the right tools. (📌/🔔 shortcuts are out —
behind `FEATURE_BOARD`/`FEATURE_ALERTS`, no work here.)

## 7. M-DESK — Proactive Desk (턴 제로 추천)

Problem: most users open 탐색 and don't know what to ask. The empty chat becomes **오늘의
데스크** — a live, fully-sourced briefing of *questions worth asking right now*, composed by
Gemini from real gateway tools (never templates/keyword rules — invariant). This is the
turn-zero extension of the smart-follow-ups differentiator: capability-aware, itch-
scratching, and a showcase of our data. Every card carries `source · as_of · freshness` and
deep-links; tapping a card composes an editable chat turn. Suggestions are descriptive
("주목할 변화") — never advice ("매수 기회" is refused at generation, LLM-judged).

**Card kinds (v1)** — each renders a one-line sourced hook + the suggested question:
- `watchlist_nudge` — no @groups yet → 관심그룹 등록 유도 card with inline quick-add
  (ticker search + market presets from onboarding).
- `price_move` — notable watchlist/index moves today (KIS/Yahoo snapshot); after M0, framed
  with the history percentile ("삼성전자 −3.2%, 자체 5년 일간 변동의 하위 2% — 과거 기록
  보기").
- `filing_new` — disclosures landed since last visit (SEC/DART index) → "방금 올라온 8-K,
  뭐가 들어있나 보기" (deep-link to the evidence viewer).
- `earnings_upcoming` / `econ_calendar` — this week's earnings for the user's groups (FMP)
  + upcoming macro releases → "D-2 TSLA 실적, 컨센서스 확인하기".
- `news_cluster` — clustered fresh headlines per group (news RAG) → one question per
  cluster, not per article.
- `this_day_history` — "역사 속 오늘": largest same-calendar-day historical moves
  (descriptive, `HistoricalLabel`; needs M0).
- `continue_thread` — pick up the user's last substantive conversation ("어제 보던 TSLA
  마진 분석 이어가기").
- `history_tour` — when M1 lands: one analogue/base-rate teaser for the user's market.

### DK-1 · Desk feed generation (agent-engine) — ✅ done
- **What**: new non-chat endpoint `POST /agent/desk-feed` in agent-engine: input = user
  context (watchlist groups+tickers, market prefs, last-visit timestamp, recent conversation
  titles); flow = **parallel tool gather** through the gateway (price snapshots, filings
  index since last visit, earnings/econ calendar, news search per group) → one Gemini
  synthesis pass → 4–8 cards `{kind, question, hook, citations[], deeplink?, ttl_hint}`.
  Uses the cheap model tier; guardrail applies (no advice phrasing — same LLM judge as
  chat intake). Cards without at least one citation are dropped, not shipped (no unsourced
  hooks).
- **Accept**: endpoint returns ≥4 sourced cards for a fixture user with a watchlist and a
  `watchlist_nudge`-led feed for a user without one; every card cites; metering rows appear
  for the underlying tool calls; unit tests with mocked LLM/tools for both user states +
  citation-drop rule.

### DK-2 · Desk home zero state (web) — ✅ done
- **What**: the empty-conversation state of 탐색 becomes 데스크 홈: greeting (time-of-day,
  user market), card grid (kind-specific mini-layouts, ProvenanceFooter on each), tap →
  composer pre-filled (editable, sends as a normal turn), hooks' deep-links open the
  existing evidence/chart viewers directly. Refresh affordance ("새로 고침" — refetches the
  feed). Skeleton state while loading; graceful empty ("장 마감 · 새 이벤트 없음 — 이런 것도
  물어볼 수 있어요" with capability cards) — never a blank screen.
- **Accept**: zero state renders from a feed fixture; tap-to-compose works; no-watchlist
  state shows the nudge with working inline quick-add (creates a real watchlist); vitest
  component tests for the three states (nudge / feed / empty-graceful).

### DK-3 · Feed caching + watchlist pulse (studio-api) — ✅ done (DK-3b ⬜ after M0)
- **What**: studio-api caches the generated feed per user (TTL 45min `DESK_FEED_TTL_SECONDS`,
  invalidated on every watchlist mutation), records `last_seen_at` for "since last visit"
  logic, and exposes `GET /desk-feed` to the BFF. *Delivered note*: stale/missing →
  synchronous regenerate (not background SWR — at a 45-min TTL the extra machinery wasn't
  worth it); if the engine is down a stale copy is served, else an explicit `degraded` empty
  feed (never a 500). **DK-3b (after M0)**: enrich `price_move`/`this_day_history` cards with
  `market_history` percentile context.
- **Accept**: second load within TTL serves the cache (no agent call — assert via mock);
  watchlist edit invalidates; `last_seen_at` drives `filing_new` windows; BFF route
  session-guarded like every other.

### DK-4 · Quality bar + eval — ✅ done
- **What**: 2 eval scenarios: (1) desk feed for a seeded watchlist — judged on
  groundedness (hooks match cited data), question quality (specific, answerable by our
  tools), and zero advice/forecast phrasing; (2) nudge state — helpful, not pushy.
  Rubric addition in `eval/RUBRIC.md`.
- **Accept**: eval ≥ bar with the new scenarios; a regression in card sourcing fails the
  judge.

## 8. M-QUANT — Analysis→Artifact engine

The general answer to "여러 가격·종목·미시·거시 데이터를 받아 통계/수학 분석을 하고 차트·표·
그림으로 보여준다"를 **잘** 하는 방법. Four principles govern every task here:

1. **The LLM never does arithmetic.** Every figure in an answer originates from a tool
   result. Gemini decides *which* computation to run and narrates the result; a
   deterministic engine computes it. The synthesis verify step enforces this (QT-2).
2. **Computation is a declarative spec, not generated code.** The agent emits a JSON
   compute spec (inputs = catalog tool refs, ops = whitelisted pure transforms); the engine
   executes it deterministically. Reproducible, cacheable, metered, safe — and the spec
   itself becomes the 계산 근거 (the existing `Computation` schema + `ComputationPanel`).
3. **Alignment is explicit, never silent.** Trading calendars, frequencies (daily prices ×
   monthly CPI), currencies/units: joins follow a declared policy; unit mismatches can never
   share a chart axis; macro joins use release dates (no look-ahead — same invariant as the
   History Lab scrubber).
4. **The result's shape picks the artifact.** series→`timeseries`/`compare` ·
   matrix→`heatmap` · point pairs→`scatter` · distribution→`distribution` · scalars→`stat`
   with 계산 근거 — mapping lives in one place (agent artifact builder), not per-feature.

Descriptive statistics only: rolling correlation/beta/spread/seasonality are historical
descriptions and allowed; anything that *fits and extrapolates* (trend projection, forecast
regression lines) is refused with the guardrail label — same boundary as History Lab.

### QT-1 · `/compute/series` — declarative transform engine — ⬜
- **What**: `datasets/app/analytics/compute.py` (pure executor) + router
  `datasets/app/routers/compute.py` + manifest connector `quant_compute` (new category
  **`퀀트 분석`** in `_CATEGORY`, cadence `daily`, cost `medium`). Spec:

  ```json
  {
    "inputs": [
      {"id": "spx",  "tool": "prices",           "args": {"market": "US", "ticker": "^GSPC", "years": 20}, "field": "close"},
      {"id": "cpi",  "tool": "macro/indicators",  "args": {"series": "CPIAUCSL"},                           "field": "value"}
    ],
    "align":  {"freq": "M", "method": "last", "join": "inner", "ffill_limit": 0, "use_release_dates": true},
    "ops": [
      {"op": "yoy",          "in": "cpi",                 "out": "cpi_yoy"},
      {"op": "returns",      "in": "spx", "kind": "log",  "out": "spx_ret"},
      {"op": "rolling_corr", "in": ["spx_ret", "cpi_yoy"], "window": 36, "out": "corr"}
    ],
    "output": ["corr"]
  }
  ```

  Whitelisted ops v1 (each a pure, unit-tested function): `returns` (log/simple), `cumret`,
  `rebase`, `zscore`, `lag`, `spread`, `ratio`, `yoy`/`mom`, `rolling_mean/std/corr/beta`,
  `drawdown`, `percentile_rank`, `seasonality` (calendar-bucket aggregates), `histogram`,
  `corr_matrix`. Unknown op → 422 listing supported ops (never silently skipped);
  predictive ops don't exist here by construction. Limits: ≤8 inputs, ≤50k points/series,
  request timeout, cost metered by input size.
- **Entitlement**: the gateway forwards the caller's activated-connector list
  (`X-Activated-Connectors`); compute rejects any `inputs[].tool` outside it — no
  entitlement bypass through composition.
- **Output**: series/matrix + a full `Computation` block (method, formula per op, each
  input with its own source+as_of, steps) so ComputationPanel renders end-to-end;
  `source: "derived: yahoo prices + FRED via compute-v1"`.
- **Accept**: golden-file tests for every op (hand-computed fixtures incl. NaN/gap,
  mixed-frequency join, ffill limit); coverage.sh hits the tool through the gateway;
  unentitled-input rejection tested; catalog integrity green.

### QT-2 · Numeric integrity in the agent — ⬜
- **What**: (a) planner guidance: any derived figure (상관, 스프레드, YoY, 상대성과…) must
  come from `quant_compute` or an existing analytics tool — synthesis prompt forbids
  in-token arithmetic; (b) upgrade the existing verify step to a **number audit**: every
  numeral in the draft answer must match a value in citations/artifacts/computations
  (tolerance = display rounding); mismatch → regenerate the sentence or drop the claim,
  and the audit result feeds the existing confidence score; (c) refusal path: "추세선으로
  예측해줘" → guardrail offer of the descriptive alternative (rolling stats, historical
  base rates).
- **Accept**: unit tests with a rigged draft containing an unsupported number → audit
  catches it; +2 eval scenarios ("최근 3년 코스피와 미국 CPI 상관관계 보여줘" → compute
  spec + chart + 계산 근거; "이 상관관계로 다음 달 예측해줘" → refused/reframed with label);
  eval ≥ bar.

### QT-3 · `scatter` + `distribution` artifact kinds — ⬜
- **What**: extend `web/lib/types.ts` + agent artifact builder + `ArtifactCard` renderers:
  `scatter` (points with x/y labels+units, optional zero/identity reference lines — **no
  fitted lines**), `distribution` (bins + a marked "현재 값" position). Wire the existing
  `ComputationPanel` to every computed artifact (the backend `computation` block now real
  via QT-1 — closes the half-built gap noted 2026-07-03). Corr matrices reuse `heatmap`.
- **Accept**: TS build green; component tests for both kinds + the 계산 근거 chip opening
  ComputationPanel with real inputs; UX_SPEC §4/§6 conventions followed (mono numbers,
  ProvenanceFooter mandatory).

### QT-4 · Alignment & unit safety — ⬜
- **What**: series carry unit/currency metadata from their connector manifests through
  compute into artifacts; chart rule: same axis ⇒ same unit, else auto second pane or
  rebase-to-100 with an explicit note chip; calendar joins (`inner`/`union`) draw gaps for
  union-missing points; `use_release_dates: true` uses macro release dates so a January CPI
  print aligns to its February publication (no look-ahead).
- **Accept**: unit tests — KRW×USD same-axis refused (artifact renders two panes); monthly
  CPI × daily prices inner-join produces monthly output; release-date alignment verified
  against a FRED fixture with known publication lag.

## 9. M3 — Earnings Command Center

### EC-1 · Transcript archive backfill — ⬜
Extend `transcript_text` pipeline to walk historical quarters per ticker (Alpha Vantage
`EARNINGS_CALL_TRANSCRIPT`, free tier 25 req/day → durable queue drains over days; document
the constraint, show progress in admin; paid-tier env to accelerate). Store quarter metadata
on chunks. **Accept**: ≥8 quarters for a demo ticker after drain; resumable; rate-limit
unit-tested.

### EC-2 · Surprise history + estimates artifacts — ⬜
New artifact: consensus-vs-actual EPS/revenue bars per quarter with surprise %, from FMP
(already connected) + store; markers link each quarter to its transcript/deck/filing in RAG.
**Accept**: "TSLA 어닝 서프라이즈 히스토리" renders the artifact, each bar cites FMP+SEC.

### EC-3 · Transcript QoQ diff (LLM, cited) — ⬜
Agent capability: compare this quarter's call vs prior — topics added/dropped, tone on
recurring topics (guidance language quoted verbatim, no forecasts of our own), each claim
cited to transcript chunks (speaker+quarter). Add eval scenario. **Accept**: eval ≥ bar;
verbatim quotes carry 🟨 mark styling in the answer; refusal boundary intact (we quote
management's guidance as *their* statement, never endorse).

### EC-4 · Earnings season in chat — ⬜
Chat artifacts (no board work): "@반도체 어닝 일정" → watchlist-scoped upcoming-earnings
`calendar` artifact (FMP); on an earnings day, "오늘 어닝 정리" → consensus→actual card with
deep-links to the call transcript/deck/8-K evidence; "이번 시즌 비트/미스" → beat/miss grid
artifact (existing `heatmap` kind). Add follow-up chips for all three to `_CAPABILITY_MENU`.
**Accept**: each question renders its artifact with FMP+SEC citations; deep-links open the
existing viewers; eval scenario added.

## 10. M4 — Filing Intelligence

### FI-1 · Risk-factor section extraction — ⬜
Deterministic section splitter for 10-K/10-Q Item 1A (and DART 사업보고서 '이사의 경영진단'
/위험 관련 절) at filing_text ingestion time — section-tagged chunks (`section="risk_factors"`,
`item_1a` anchors preserved for the evidence viewer). **Accept**: RAG filter by section works;
splitter unit-tested on 3 real cached filings.

### FI-2 · YoY redline artifact + viewer — ⬜
`filing_diff` tool (datasets): sentence-level diff of Item 1A vs prior year (pure diff
algorithm, no LLM) → added/removed/changed spans; agent summarizes *what changed* with
citations; evidence viewer gains a redline mode (added=green underline, removed=strike,
both link to the original highlight). **Accept**: "AAPL 10-K 리스크팩터 작년 대비 뭐가 달라졌어?"
→ diff artifact + redline viewer opens on click; diff engine unit-tested.

### FI-3 · Event timeline (8-K / 주요사항보고) — ⬜
Per-ticker corporate event timeline artifact from the filings index (8-K items classified by
their **structural item codes** — 1.01, 2.02, 5.02… — which are data, not heuristics; DART
주요사항보고 types likewise), rendered on the price chart as markers + as a `feed` artifact
in chat. **Accept**: timeline artifact for a ticker shows classified events with links;
markers land on correct dates.

## 11. M5 — UX Overhaul, chat-first (spec: UX_SPEC.md — read it first)

- **UX-1** Navigation & IA — ⬜ chat-first rail: 탐색 · 히스토리 · 관심 · 설정 (대시보드/
  알림봇 render only when their flags are on), view-state → URL (shareable deep links;
  still SPA).
- **UX-2** Command palette (⌘K) — ⬜ ticker/regime/prompt jump + "ask" fallthrough to chat;
  fuzzy match over watchlists + regimes + conversations.
- **UX-3** Ticker context header — ⬜ persistent strip when a ticker is in focus
  (price, day move, current drawdown vs history percentile chip, next disclosure date) —
  every number sourced.
- **UX-4** FE refactor completion — ⬜ (folds in FE-07..14) Chat.tsx split
  (ChatThread/ChatComposer/ContextPanel/HistoryLab), ProvenanceFooter primitive, a11y pass
  (focus traps in modals, chart keyboard nav, aria-live for streaming), unit test runner for
  web (vitest) with tests for lib/ + new components.
- **UX-5** Onboarding v2 — ⬜ chat-first flow (market → watchlist → straight into a guided
  first conversation): the "히스토리 랩 투어" opens as a pre-composed chat turn producing an
  analogue + base-rate artifact for the user's market, plus the guardrail-label education
  moment ("우리는 전망하지 않습니다 — 역사를 보여줍니다"). Channel/board steps appear only
  behind their flags.

## 12. M6 — Horizon (re-approve before starting)

Standing analysts + scheduled briefs (U4 successor — now much stronger with History Lab
content: "주간 히스토리 브리프"), publish/clone gallery (U5 successor; reuse the
prompt-import `community`+`source_id` idempotent-clone pattern), and the **dashboard +
alert-bot revival** if/when the flags flip on (history-aware triggers `drawdown_threshold`/
`vol_percentile`/`regime_event`, chart-image telegram messages, VIX/drawdown widgets,
digest 2.0 — parked here from the earlier draft). Not specced; author a spec before pulling
tasks.

---

## 13. Test & eval accounting

Every task adds tests; keep this table updated in the same PR (Definition of Done).
"Current" starts at the baseline and moves only when a PR lands. The right column is the
*planned minimum* per milestone — treat as floors, not ceilings.

| Service | Baseline (2026-07-03) | Current | Planned additions (minimum) |
|---|---|---|---|
| datasets | 148 | 193 (measured) | ✅ OPS-1 (+2 grouping/runner); then ≥32 HL-1/2/3, ≥12 HL-4, ≥9 HL-5, ≥22 QT-1/4, ≥7 EC-1, ≥14 FI-1/2/3, ≥8 HL-8/EC-2 |
| agent-engine | 111 | 119 (measured, incl. skips) | ✅ DK-1 (+5: feed states, citation-drop, degrade); then ≥10 HL-6/7, ≥8 QT-2 (number audit), ≥8 EC-3, ≥6 HL-9 |
| studio-api | 40 | 56 (measured) | ✅ FLAG-1 scheduler gate (+1), ✅ DK-3 (+4: cache/TTL/invalidate/since/degrade); then ≥7 HL-12/14 BFF |
| control-plane | 13 | 13 | ≥1 QT-1 (activated-connectors header forwarding); rest manifest-derived (coverage.sh guards) |
| mcp | 9 | 9 | ≥3 HL-4/QT-1 (new tools listed, unentitled 403) |
| rag | 20 | 20 | ≥4 HL-5 (era_news/dossier doc types), ≥2 FI-1 (section filter) |
| web | TS build only | TS build only | UX-4 adds a vitest runner; then component tests for DK-2 (3 states), HL-7/10/11/12/13, QT-3 (scatter/distribution/계산 근거), EC-4 |
| eval scenarios | 32 | 34 (measured) | ✅ DK-4 (+2 desk-feed, `kind: desk_feed` runner); then +4 HL-6, +2 QT-2, +1 EC-3, +2 M2 flows, +1 FI |

Eval bar: maintain ≥ current score (`eval/RUBRIC.md`); run before every push.

## 14. Non-goals (unchanged)

No forecasts, price targets, momentum scores, or advice — in any milestone, including
History Lab (that's the point). No non-Gemini models. No keyword routers. No client-side
keys. No fabricated gaps: pre-1997 KOSPI, missing transcripts, and un-ingested eras are
**drawn as gaps** with an explanation chip.
