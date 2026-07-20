# HISTORY LAB — Technical Specification (M0–M2)

> Companion to [`ROADMAP.md`](./ROADMAP.md) §4–§6 (tasks HL-1 … HL-14). This document is the
> implementation contract: schemas, algorithms, API shapes, artifact protocol, and agent
> integration. UX/screens live in [`UX_SPEC.md`](./UX_SPEC.md) §5.
>
> Everything here is **descriptive analytics over ingested, sourced data**. No forecasting.
> Every response carries `label: "과거 기록 · 전망 아님"` (ROADMAP §2 invariants).

---

## 1. Feature summary

Given today's market state, show — visually, instantly — where it sits in history:

1. **Drawdown context**: current peak-to-now decline vs every historical episode.
2. **Volatility context**: current realized vol / VIX vs its own full-history distribution.
3. **Base rates**: for a user-defined past event ("일간 −5% 이상 하락"), the descriptive
   record of what followed over fixed horizons — n, median, quartiles, extremes, share
   positive — with the event dates enumerable and clickable.
4. **Analogues**: the k most similar historical price paths to the recent window, overlaid.
5. **Era replay**: pick a regime (닷컴버블, GFC, IMF 외환위기 …), scrub through it day by
   day, and see the news, macro readings, and disclosures **as of that day**.

## 2. Component map

```
datasets/
  app/analytics/                ← NEW (HL-3): pure functions, no I/O, no LLM
    drawdown.py                    underwater series + episode detection
    volatility.py                  realized vol, percentiles, VIX context
    base_rates.py                  event → forward-window descriptive stats
    analogue.py                    window similarity search
    regimes_seed.py                curated regime reference data (sourced)
  app/routers/history.py        ← NEW (HL-4): /history/* endpoints
  app/connectors/market_history*  NEW (HL-4): manifest (one resource per endpoint)
  app/connectors/nyt_archive*   ← NEW (HL-5)
  app/connectors/gdelt*         ← NEW (HL-5)
  app/pipelines.py              ← EXTEND (HL-1/2/5): history universe, episodes sweep,
                                   era-news one-shot ingest
  (store models module)         ← EXTEND (HL-2): DrawdownEpisode, MarketRegime

agent-engine/                   ← EXTEND (HL-6/7/9): intake framing, artifact builders,
                                   capability menu, decompose recipes
rag/                            ← doc types era_news / regime_dossier (no code change
                                   expected; chunk metadata already generic)
web/
  lib/types.ts                  ← EXTEND (HL-7): base_rates / analogue artifact kinds
  components/ArtifactCard.tsx   ← EXTEND (HL-7)
  components/TradeChart.tsx     ← EXTEND (HL-8): underwater pane, regime zones, vol ribbon
  components/HistoryLab/*       ← NEW (HL-10..14): view shell, ribbon, split, scrubber,
                                   era rail, base-rate builder
studio-api/                     ← EXTEND (HL-12/14): BFF passthroughs only (all data flows
                                   through the gateway as usual)
```

Request path is unchanged: web → studio-api → agent-engine (chat) or web → studio-api →
gateway (direct widget fetch) → datasets. `market_history` is a normal catalog connector —
entitlement, metering, MCP, and the builder category (`시장 히스토리`) all derive from the
manifest.

## 3. Data (HL-1, HL-2, HL-5)

### 3.1 History universe

`HISTORY_UNIVERSE` (env, comma-separated Yahoo symbols; default):

```
^GSPC   S&P 500          daily since 1927   US anchor
^IXIC   NASDAQ Composite daily since 1971   dot-com anchor
^DJI    Dow Jones        (Yahoo depth varies — ingest what returns; draw gaps)
^VIX    CBOE VIX         daily since 1990   vol anchor (level series, no OHLC volume)
^KS11   KOSPI            Yahoo depth ~1997+ KR anchor (pre-1997 = drawn gap)
^KQ11   KOSDAQ           Yahoo depth ~1998+
^TNX    US 10Y yield ×10
^N225 / ^HSI             regional context
GC=F / CL=F              gold / WTI (futures proxies — label as proxy)
KRW=X                    USD/KRW
```

Rules:
- Universe symbols: `range=max` on first ingest, then the normal incremental daily sweep.
- Watchlist tickers: `PRICES_BACKFILL_YEARS` (default now **10**).
- `^VIX` is a level index: volume 0/None is valid; never fabricate OHLC.
- Depth is whatever the upstream returns — **record it, draw the gap before it** (e.g. KOSPI
  pre-1997). A later task may add an ECOS long-series complement for KR; out of scope here.

### 3.2 Store models (datasets store, next to `PriceBar`)

```python
class DrawdownEpisode(Base):            # DERIVED — recomputed idempotently by pipeline
    id: int
    market: str          # US | KR | GLOBAL
    ticker: str          # anchor symbol, e.g. ^GSPC
    peak_date: date
    peak_close: float
    trough_date: date
    trough_close: float
    depth_pct: float     # (trough/peak − 1) * 100, negative
    decline_days: int    # calendar days peak→trough
    recovery_date: date | None   # first close ≥ peak_close after trough
    recovery_days: int | None    # calendar days trough→recovery
    is_open: bool        # episode not yet recovered (includes "still declining")
    threshold_pct: float # detection threshold this row belongs to (10.0 / 20.0)
    method_version: str  # e.g. "dd-v1" — bump when the algorithm changes
    source: str          # "derived: yahoo prices (close), method dd-v1"
    computed_at: datetime
    # UQ (market, ticker, threshold_pct, peak_date, method_version)

class MarketRegime(Base):               # CURATED reference data — seeded, sourced
    id: int
    slug: str            # "dotcom-bust", "gfc-2008", "covid-crash", "kr-imf-1997", ...
    name_kr: str; name_en: str
    market: str          # US | KR | GLOBAL
    anchor_ticker: str   # ^IXIC for dot-com, ^GSPC for GFC, ^KS11 for KR ...
    kind: str            # bubble | crisis | bear | rate_cycle | recovery
    start_date: date; end_date: date    # narrative window (wider than the episode)
    peak_date: date | None; trough_date: date | None  # cross-checked vs DrawdownEpisode
    description: str     # 2–4 sentences, factual
    sources: JSON        # [{title, url, publisher}] — every regime cites ≥1 source
    # UQ slug
```

Seed (~15 regimes, `regimes_seed.py`; dates below are the *narrative windows* — exact
peak/trough come from the derived episodes and are cross-checked at load, mismatch → warn
log + admin activity row, never silent overwrite):

US: black-monday-1987 · gulf-war-1990 · dotcom-bust (2000–2002, ^IXIC) · sept-11-2001 ·
gfc-2008 (2007–2009) · flash-crash-2010 · euro-crisis-2011 · taper-tantrum-2013 ·
vol-spike-2018 · covid-crash-2020 · inflation-bear-2022.
KR: kr-imf-1997 · kr-card-crisis-2003 · kr-gfc-2008 · kr-covid-2020.

### 3.3 Era news (HL-5)

- **`nyt_archive` connector**: `GET /svc/archive/v1/{year}/{month}.json` (NYT Archive API,
  free key). Budget: ~500 req/day, ≤5 req/min — enforce a client-side limiter (token bucket,
  unit-tested). Resource `era_news(from, to, query?)`: iterate months, filter locally
  (section ∈ Business/Financial or query match on headline/abstract). Store only
  headline + abstract + pub_date + web_url + section (metadata-scale; link out for body).
- **Era ingest pipeline** (one-shot per regime, manual-run card in admin): for each
  `MarketRegime`, ingest its `[start_date − 30d, end_date + 90d]` window into RAG:
  `doc_type="era_news"`, `source="The New York Times"`, `as_of=pub_date`,
  `ticker=""`, `market=regime.market`, `section=regime.slug`, `url=web_url`.
  Chunk = `headline + "\n" + abstract` (short docs; one chunk each — chunker handles it).
- **`gdelt` connector** (keyless, coverage 2017+): `news_timeline(query, from, to)` →
  `timelinevol`/`timelinetone` series (chartable overlay: "뉴스 볼륨/톤"), and
  `news_search(query, from, to)` → article list. Label coverage boundary (pre-2017 = gap).
- **Regime dossiers**: admin-triggered Gemini synthesis per regime over *ingested* era_news
  chunks + FRED series + derived episode stats → 400–700-word factual narrative, every claim
  `[n]`-cited to those sources, stored as `doc_type="regime_dossier"` (one doc, chunked
  normally). Regeneration is manual-only; the dossier footer records model + generated_at +
  citation list.
- **KR era news (HL-5K, parked)**: BigKinds is the leading candidate (Korean news archive to
  1990). Until a provider is chosen, KR regimes show era news as a **drawn gap** with an
  explanation chip — never substitute translated US news.

## 4. Analytics engine (HL-3) — `datasets/app/analytics/`

Pure functions over `bars: list[(date, close)]` (and full OHLC where noted). No I/O, no
LLM, no randomness. All returns include `method`, `params`, and `label` fields (§5 shapes).
Use closes for cross-era comparability (pre-1962 ^GSPC has no reliable OHLC); log returns
throughout: `r_t = ln(C_t / C_{t−1})`.

### 4.1 Underwater series

```
peak_t = max(C_0..t);  dd_t = C_t/peak_t − 1
```
Output: `[(date, dd_pct)]` + `current: {dd_pct, peak_date, days_since_peak}`.
Edge cases: leading NaNs dropped; gaps (missing dates) are simply absent points (the chart
draws the gap); series shorter than 2 bars → 422.

### 4.2 Episode detection (`dd-v1`)

```
state: peak = C_0, peak_date = d_0, in_episode = False
for each bar t:
  if C_t > peak and not in_episode: peak, peak_date = C_t, d_t
  dd = C_t/peak − 1
  if not in_episode and dd <= −threshold:        # episode opens (threshold: 10% or 20%)
      in_episode = True; trough, trough_date = C_t, d_t
  if in_episode:
      if C_t < trough: trough, trough_date = C_t, d_t
      if C_t >= peak:                            # full recovery closes the episode
          emit(peak_date, trough_date, recovery_date=d_t); in_episode = False
          peak, peak_date = C_t, d_t
at end: if in_episode: emit(..., recovery_date=None, is_open=True)
```
Notes: threshold on **close**; depth is trough-close based (intraday extremes differ —
state the method in `source`). Run for thresholds {10, 20}; store both (`threshold_pct`).
Known-answer tests: synthetic sawtooth; and (integration fixture) ^GSPC GFC ≈ −56.8%,
COVID-2020 ≈ −33.9%, both ±0.5pt.

### 4.3 Volatility context

- Realized vol: `rv_w = stdev(r, w) * sqrt(252) * 100` for w ∈ {20, 60, 252}.
- Percentile: `pct = rank(rv_w_today among all historical rv_w) / count` (inclusive rank,
  full available history; report the history span used).
- VIX context (when ticker=^VIX or `include_vix=true`): current level, full-history
  percentile, and per-regime medians (join episodes: bars inside episode windows).
- Output includes the distribution summary (p5/p25/p50/p75/p95) so the UI can draw the
  strip without a second call.

### 4.4 Base rates

Input: `event` spec + `horizons` (trading days, default [1, 5, 20, 60, 120]).

Event conditions (v1 — extend by adding a dataclass, never a keyword parser):
- `daily_return_lte: x` (e.g. −5.0 %)
- `drawdown_gte: y` (first day an episode's dd crosses −y% — one event per episode)
- `vol_pct_gte: p` (first day rv_20 percentile crosses p — clustered, see below)
- `regime: slug` (all trading days inside the regime window — for conditional stats)

Clustering guard: for threshold-crossing conditions, require `min_gap_days` (default 10)
between events, keeping the **first** of each cluster — otherwise "−5% 일간 하락" during
2008-10 counts 8 near-duplicate events and the stats mislead. The response reports both raw
and clustered n.

For each event date t and horizon h: `F_h(t) = C_{t+h}/C_t − 1` (skip events with fewer
than h remaining bars; report `n_h` per horizon). Aggregate **descriptively only**:

```
{h, n, median, p25, p75, min, max, pos_share}
```

plus `event_dates: [d1…dn]` (always returned — enumerability is the trust feature) and
`histogram: {h_ref, bins: [{lo, hi, count}]}` for the default horizon. **Never** output the
word "probability of rising"; the field is `pos_share` ("상승 마감 비율(과거)").

### 4.5 Analogue search

Input: ticker + `window` W (default 120 trading days) + `k` (default 5) + optional
`universe` (default: same ticker + history-universe anchors).

```
query  q = z-normalize(cum log-return path of the last W bars)
for each candidate start s in each candidate series (step = 5 bars):
    c = z-normalize(path of W bars from s); skip if window overlaps "now" or a better-
        scoring window within W/2 bars (non-max suppression)
    score = pearson(q, c)                       # primary, O(W) per window
rank desc; return top-k with:
    {ticker, start_date, end_date, score, regime_slug?,     # regime join if inside one
     path: rebased-to-100 closes for W bars,
     aftermath: rebased closes for the following W bars}    # drawn as history, labeled
```

Scale check: ^GSPC ≈ 25k bars → ≈5k windows at step 5; a few series → tens of thousands of
O(W) correlations per request — milliseconds in numpy. No precomputation or caching in v1;
if latency demands it later, cache per (ticker, window, day) — data changes daily.
The `aftermath` array exists so the UI can draw "그 뒤 실제로 일어난 일" — as a historical
line, right of the day-0 divider, under the `HistoricalLabel`. It is never blended into a
single "expected path"; **no averaging across analogues** (that would manufacture a
forecast-looking line — invariant §2).

### 4.6 Regime compare

`regime_compare(ticker, slug, anchor="peak")`: align current episode (from live underwater
state) and the regime's episode at day 0 = peak (or trough). Return both rebased close
paths + a comparison block: `{depth_now, depth_then, elapsed_days_now, decline_days_then,
recovery_days_then}`. Purely a convenience composition of §4.1/§4.2 — implement in the
router, not a new module.

## 5. API (HL-4) — router `datasets/app/routers/history.py`

Common response envelope (every endpoint):

```json
{
  "source": "derived: yahoo prices (close) via market_history",
  "method": "dd-v1", "params": {…}, "as_of": "2026-07-02",
  "freshness": "fresh", "cadence": "daily",
  "label": "과거 기록 · 전망 아님",
  "history_span": {"from": "1927-12-30", "to": "2026-07-02"},
  "data": { … endpoint-specific … }
}
```

| Endpoint | Params | `data` |
|---|---|---|
| `GET /history/drawdowns` | market, ticker | underwater series + current block (§4.1) |
| `GET /history/episodes` | market, ticker, threshold=20 | episode rows (§3.2) sorted by depth |
| `GET /history/vol-context` | market, ticker, include_vix=true | §4.3 block |
| `GET /history/base-rates` | market, ticker, event (JSON), horizons, min_gap_days | §4.4 block |
| `GET /history/analogues` | market, ticker, window=120, k=5 | §4.5 block |
| `GET /history/regimes` | market? | regime rows (+ per-regime episode join) |
| `GET /history/regime-compare` | market, ticker, slug, anchor=peak | §4.6 block |

Errors: unknown ticker/no bars → 404 with a drawn-gap hint; insufficient history for the
requested window → 422 with `required_bars`/`available_bars` (UI draws the gap chip);
unbuilt condition kinds → 501 (honesty invariant).

Manifest: connector `market_history`, category `시장 히스토리`, cost tier `low`, cadence
`daily`, one resource per endpoint. Registering the manifest auto-derives REST docs, MCP
tools, gateway entitlement paths (`catalog_index`), metering, and the builder category —
keep the manifest-path integrity test green.

## 6. Artifact protocol extensions (HL-7)

`web/lib/types.ts` (+ agent-engine builder):

```ts
// kind: "base_rates"
{
  kind: "base_rates", title, ticker, market,
  event: { text: string;        // human sentence: "일간 수익률 ≤ −5%"
           spec: object },      // machine spec (round-trips to the API)
  horizons: { h: number; n: number; median: number; p25: number; p75: number;
              min: number; max: number; pos_share: number }[],
  event_dates: string[],        // clickable chips → History Lab scrubber
  histogram?: { h_ref: number; bins: { lo: number; hi: number; count: number }[] },
  label: string,                // mandatory — renderer refuses to draw without it
  source, as_of, freshness, cadence, tool, args   // existing provenance block
}

// kind: "analogue"
{
  kind: "analogue", title, ticker, market,
  window: number, anchor: "now" | "peak",
  current: { label: string; points: {x,y}[] },      // rebased=100
  matches: { ticker: string; start: string; end: string; score: number;
             regime_slug?: string; regime_name?: string;
             path: {x,y}[]; aftermath: {x,y}[] }[],
  label: string, source, as_of, freshness, cadence, tool, args
}
```

Rendering (ArtifactCard → TradeChart/mini-renderers):
- `analogue`: single pane, x = day offset (−W…+W), day-0 divider; current path `--ink` 2px,
  matches muted grays (`#B9B9BE`→`#8A8A90` by rank), aftermath segments dashed; hover a path
  → tooltip (dates, score, regime chip) + "히스토리 랩에서 열기". No averaged/consensus line.
- `base_rates`: header = event sentence + `n=87 (clustered)` mono chip; table of horizons;
  distribution strip (histogram, gray bars, zero-line marked); event-date chips (first 12 +
  "+75 more" expander). `HistoricalLabel` pinned to the footer next to ProvenanceFooter.
- Both carry `tool+args` so any surface can re-fetch them live. (Board pinning sits behind
  `FEATURE_BOARD`, off by default — chat-first decision, ROADMAP §0. No board work here.)

SSE: no protocol change — artifacts already stream via the `artifact` event.

## 7. Agent integration (HL-6, HL-9)

- **Intake prompt additions** (concept, LLM-judged — no regex): historical descriptive
  statistics are in-scope and encouraged; transform future-probability asks into their
  descriptive-history equivalent when possible ("내일 반등 확률?" → offer the historical
  record of similar days, with the label, plus the visible guardrail note that we don't
  forecast); refuse only when the user insists on a prediction/advice.
- **Synthesis rules**: history-tool answers must state event definition + n + span in prose;
  past tense only; section ends with `과거 기록 · 전망 아님`. Quote counts, medians, and
  pos_share exactly as returned (no rounding beyond display).
- **Planner teaching** (resource descriptions in the manifest — this is where composition is
  learned): episodes → pick comparable regime → regime-compare → base-rates → era-news RAG
  search (`doc_type: era_news`, section=slug) → cite. Decompose recipe for "지금이 X 때랑
  비슷해?": [낙폭/변동성 비교] ∥ [밸류에이션 퍼센타일] ∥ [그 시기 뉴스·공시 맥락] sub-agents.
- **Follow-up chips** (`_CAPABILITY_MENU`): "과거 약세장들과 겹쳐 보기" ·
  "그 시기 뉴스 보기" · "이 조건의 과거 기록(베이스레이트) 보기" · "히스토리 랩에서 열기".

## 8. Point-in-time replay contract (HL-11/12)

The scrubber date D is client state; panels query with explicit `as_of_lte=D`:
- era news: RAG search filtered `as_of ≤ D` (metadata filter, already supported), sorted
  descending, window [D−14d, D].
- macro snapshot: FRED/ECOS series value at the **latest release date ≤ D** (the existing
  macro endpoints accept a date bound; if any doesn't, extend it in HL-12 — do not
  approximate with interpolation).
- disclosures: filings index filtered `filing_date ≤ D` for the anchor ticker.
- charts: THEN chart clips at D visually (marker), but the full episode path stays visible
  (grayed right of D) — the user is *replaying*, not being tricked.
Unit-test the selector: a fixture item with `as_of > D` must never be returned.

## 9. Performance & ops

- Bars for MAX-range anchors (~25k points) exceed chart comfort: server may downsample to
  weekly for ribbon rendering (`interval=week` already exists in the prices pipeline) —
  the ribbon uses weekly bars, analytics always use daily.
- `/history/*` endpoints are read-only over the local store: target p95 < 300ms (analogue
  search < 800ms). No upstream calls at request time.
- Episodes sweep runs weekly after prices (cron ordering in the queue); manual run button
  in admin per the manual-pipeline card pattern.
- Backfill volume: entire universe ≈ 300k rows — trivial for Postgres; one-time ingest jobs
  visible in the admin queue with progress.

## 10. Testing matrix (minimum)

| Area | Tests |
|---|---|
| analytics/drawdown | sawtooth known-answer; open episode; multiple episodes; threshold variants; NaN/gap handling |
| analytics/base_rates | hand-computed fixture (12 bars); clustering guard; short-tail horizons (`n_h` drops); regime-conditional |
| analytics/analogue | self-match excluded; known synthetic best-match; non-overlap suppression; z-norm degenerate (flat window) |
| analytics/volatility | rv known-answer; percentile rank inclusive; VIX join |
| router/history | envelope fields incl. `label`; 404/422/501 paths; regime-compare composition |
| manifest/gateway | catalog integrity; coverage.sh hits all 7 tools; unentitled 403; metering rows |
| pipelines | universe resolution; range=max parse; episode idempotency (re-run = same rows); NYT rate limiter; era ingest chunk metadata |
| rag | era_news/dossier doc-type filter; as_of≤D selector |
| agent | guardrail allow (descriptive) / deny (prediction insistence); synthesis label present; artifact builders |
| web | renderers refuse without `label`; event-chip → scrubber routing; THEN\|NOW alignment math; point-in-time rail selector |
| eval | 4 scenarios (ROADMAP HL-6) |
