/**
 * Shared schema contracts for the chat / artifact / citation surfaces (FE-01).
 *
 * Single source of truth for the shapes the agent-engine streams and the UI renders. Previously these
 * were defined inside the components that happened to render them (Artifact in ArtifactCard, Citation
 * in SourceCard, the chat/SSE types in Chat), so importing a *type* dragged in a *component*. They now
 * live here; the components re-export them for back-compat, so existing imports keep working.
 *
 * NOTE: keep these in sync with the backend emitters (agent-engine `models.py`,
 * `citations.py`/`artifacts.py`) — every figure carries source + as_of + freshness + cadence.
 */

// --- artifacts (live, sourced figure cards) ---------------------------------------------------
export type ArtifactPoint = { x: string; y: number | null };
export type ArtifactSeries = { label: string; unit?: string | null; points: ArtifactPoint[] };
export type ArtifactCandle = {
  time: string; open?: number | null; high?: number | null; low?: number | null;
  close?: number | null; volume?: number | null;
};
export type ArtifactMarker = {
  time: string; label: string; kind?: string; position?: string;
  color?: string | null; source?: string | null; url?: string | null; snippet?: string | null;
};
export type ArtifactPriceLine = { price: number; label: string; color?: string | null };
export type ChartAnnotations = {
  lines?: { x1: string; y1: number; x2: string; y2: number; label?: string | null; color?: string | null }[];
  hlines?: { price: number; label?: string | null; color?: string | null }[];
  vlines?: { time: string; label?: string | null; color?: string | null }[];
  zones?: { t0: string; t1: string; label?: string | null; color?: string | null }[];
  rebase?: boolean;
  note?: string | null;
};
export type OverlayLine = {
  label: string; color?: string | null; points: { time: string; value: number }[];
};
export type ChartOverlay = {
  key: string; name: string; pane?: string; unit?: string | null;
  lines: OverlayLine[]; source?: string | null;
};
// PH-DATA-6: the auditable derivation of a self-computed figure (valuation/backtest/screener) —
// what was queried, what was assumed, the formula, and the intermediate steps. Shown as a panel.
export type CalcRow = { label: string; value: string; source?: string | null };
// PH-DEMO widget item shapes.
export type StatItem = {
  label: string; value: number; unit?: string | null; delta?: number | null;  // delta in %
  fmt?: "num" | "pct" | "won" | "usd"; gauge?: boolean;  // gauge: 0-100 fear/greed dial
};
export type HeatCell = { label: string; sub?: string | null; value?: number | null; pct: number };
export type FeedItem = { time?: string | null; text: string; tag?: string | null };
export type CalEvent = { date: string; label: string; tag?: string | null };
export type Computation = {
  method: string;
  formula?: string | null;
  inputs?: CalcRow[];
  assumptions?: CalcRow[];
  steps?: CalcRow[];
  note?: string | null;
};
export type Artifact = {
  kind: string;
  chart_style?: string | null;  // "bar" for money amounts (revenue/income); else line
  title: string;
  series: ArtifactSeries[];
  candles?: ArtifactCandle[];  // kind=candlestick (prices): real OHLCV → candles + volume
  markers?: ArtifactMarker[];  // PH-VIZ-2: sourced events on the time axis (click → evidence)
  pricelines?: ArtifactPriceLine[];  // PH-VIZ-2: descriptive period high/low lines
  annotations?: ChartAnnotations | null;  // PH-VIZ-3: agent-authored lines/levels/zones
  user_annotations?: ChartAnnotations | null;  // PH-VIZ-5: the user's own drawings (persisted on pin)
  overlays?: ChartOverlay[];   // PH-VIZ-4: technical indicators (price-pane + sub-panes)
  table?: string[][] | null;   // kind in {table, kpi}: header-first matrix (each row sourced)
  sections?: { heading: string; body: string }[];  // kind=narrative (CE-4): 종목 내러티브 sections
  // PH-DEMO: high-impact dashboard widgets (Datadog-style). `live` enables a client-side ticking
  // simulation (demo only). stat = big hero KPI tiles; heatmap = sector tiles; feed = live news
  // stream; calendar = upcoming events.
  live?: boolean;
  stats?: StatItem[];     // kind=stat: a row of big-number KPI tiles
  cells?: HeatCell[];     // kind=heatmap: sector tiles colored by % change
  items?: FeedItem[];     // kind=feed: a vertical live news/event stream
  events?: CalEvent[];    // kind=calendar: upcoming dated events
  computation?: Computation | null;  // PH-DATA-6: how a self-computed figure was derived
  source?: string | null;
  as_of?: string | null;
  freshness?: string | null;
  cadence?: string | null;   // intraday|daily|event|scheduled|streaming|one_shot — periodic ⇒ alertable
  category?: string | null;  // market|fundamentals|valuation|filings|gurus|macro|news|…
  ticker?: string | null;
  has_gap?: boolean;
  tool?: string | null;
  args?: ({ market?: string } & Record<string, unknown>) | null;  // tool args (for re-fetch / market)
};

// --- citations (provenance for a cited figure / passage) --------------------------------------
export type Citation = {
  tool?: string;
  source?: string;
  url?: string;
  index?: number;
  kind?: string; // filing | news | metric | data
  doc_type?: string;
  as_of?: string;
  freshness?: string; // fresh | aging | stale | gap
  cadence?: string;   // intraday|daily|event|scheduled|streaming|one_shot — periodic ⇒ alertable
  category?: string;  // market|fundamentals|valuation|filings|gurus|macro|news|…
  snippet?: string;
  ticker?: string;
  page?: string;
  table?: string[][];   // extracted figures (header row first, cited row = first data row)
  used?: boolean;       // evidence flag (set from the answer's [n] / artifact backing)
  evidence_image_url?: string;  // /evidence?… params (market/accession/concept/value/text/cik) → in-app filing viewer
  confidence?: string;  // PH-THINK verify pass: high | medium | low (evidentiary support)
  confidence_why?: string;
};

// --- chat / SSE stream events (rendered into a Msg) -------------------------------------------
export type ToolUse = { name: string; label?: string };
export type Think = { phase: string; text: string };
export type ClarifyOption = { label: string; description?: string | null };
export type Clarify = { prompt: string; options: ClarifyOption[]; multi: boolean; origin: string };
export type SubAgent = { id: number; title: string; status: string; sources?: number; steps?: number };
export type Msg = {
  role: "user" | "assistant";
  content: string;
  tools?: ToolUse[];
  citations?: Citation[];
  artifacts?: Artifact[];
  refused?: boolean;
  used?: number[];
  thinking?: Think[];
  clarify?: Clarify;
  subagents?: SubAgent[];
  suggestions?: string[];
};

// --- dashboard widget (a pinned artifact OR citation OR a text note) --------------------------
// The pin spec is a free-form JSON blob; a widget is one of these three. (BoardCanvas adopts this in
// FE-10 — defined here now so the board/alert code can share it.)
export type WidgetSpec =
  | (Artifact & { kind: string })
  | Citation
  | { kind: "text"; title?: string; text: string };
