"""Agent Engine request/response models."""

from __future__ import annotations

from pydantic import BaseModel


class AgentSpec(BaseModel):
    """Declarative ('SDK') agent definition a tenant can save and reuse."""

    system: str | None = None
    # restrict to a subset of activated tools. Entries may be full tool names
    # (``yahoo__prices``) or connector ids (``yahoo`` → all of its tools).
    allowed_tools: list[str] | None = None
    max_steps: int | None = None
    backend: str | None = None  # legacy field — ignored; the platform is Gemini-only (invariant #7)


class RunRequest(BaseModel):
    task: str
    spec: AgentSpec | None = None


class Message(BaseModel):
    role: str  # user | assistant | system
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    spec: AgentSpec | None = None


class CompileRequest(BaseModel):
    description: str


class ArtifactRefreshRequest(BaseModel):
    """Re-run a pinned artifact's tool+args to refresh it (U3-03b)."""

    tool: str
    args: dict | None = None
    title: str | None = None  # pick the matching artifact when a tool yields several


class Citation(BaseModel):
    tool: str
    source: str | None = None          # institution / publisher (e.g. 'SEC EDGAR', 'Reuters')
    url: str | None = None
    # --- PH-4/U2: enough to render a type-aware source-preview card ----------
    # ('kind', not 'type' — the SSE event envelope already uses 'type' as its
    # discriminator, so a field named 'type' would collide when flattened.)
    index: int | None = None           # 1-based [n] anchor, assigned after de-dup
    kind: str | None = None            # filing | news | metric | data
    doc_type: str | None = None        # e.g. '10-K', 'news' (from RAG provenance)
    as_of: str | None = None           # ISO date the cited fact is as of
    freshness: str | None = None       # fresh | aging | stale (computed from as_of)
    # periodicity of the source datasource (from the catalog) + its user-facing category.
    # `cadence` gates the pin→alert flow: only a periodic source (cadence != one_shot) can carry
    # a notification bot once pinned. Both ride along so they survive into the pinned widget spec.
    cadence: str | None = None         # intraday|daily|event|scheduled|streaming|one_shot
    category: str | None = None        # market|fundamentals|valuation|filings|gurus|macro|news|…
    snippet: str | None = None         # cited span / headline / computation — the preview body
    ticker: str | None = None
    page: str | None = None            # filing section / accession ref
    # the specific figures this citation actually contributed — rendered as an
    # extracted-data table in the preview (header row first, cited row marked).
    table: list[list[str]] | None = None
    # a datasets `/evidence?…` params URL (market/accession/concept/value/text/cik). The frontend
    # opens the filing in-app from these and highlights the cited element — fetched lazily on
    # viewer-open, so the answer stream is never blocked. (Field name kept for back-compat.)
    evidence_image_url: str | None = None
    # evidence flag: True iff this source actually backed the answer (cited [n] or
    # backs an artifact). The Live Context shows only evidence; consulted-but-unused
    # sources stay in the answer's 도구·출처 list.
    used: bool = False
    # PH-THINK (verify pass): how well this source supports answering the question, scored
    # by the reviewer in one pass (high|medium|low) + a one-line rationale. Descriptive —
    # never a forecast. None when the verify pass didn't run (no key / LLM unavailable).
    confidence: str | None = None
    confidence_why: str | None = None


class ArtifactPoint(BaseModel):
    x: str               # date / report-period label
    y: float | None = None


class ArtifactSeries(BaseModel):
    label: str
    unit: str | None = None      # 'ratio' | currency | None
    points: list[ArtifactPoint] = []


class ArtifactCandle(BaseModel):
    """One OHLCV bar for a candlestick artifact (PH-VIZ-1) — real prices, not synthesized."""

    time: str                    # 'YYYY-MM-DD'
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None


class ArtifactMarker(BaseModel):
    """PH-VIZ-2: a sourced event on the chart's time axis (earnings / dividend / split /
    filing). Clicking it opens the source in the evidence viewer — the chart IS evidence."""

    time: str                    # 'YYYY-MM-DD' (renderer snaps to the nearest bar)
    label: str
    kind: str = "event"          # earnings | dividend | split | filing
    position: str = "aboveBar"   # aboveBar | belowBar
    color: str | None = None
    source: str | None = None
    url: str | None = None
    snippet: str | None = None


class ArtifactPriceLine(BaseModel):
    """PH-VIZ-2: a horizontal reference line (e.g. 52-week high/low) — descriptive, drawn
    from the price data itself."""

    price: float
    label: str
    color: str | None = None


class ChartLine(BaseModel):
    """PH-VIZ-3: a trend/segment line between two HISTORICAL points (never projected)."""

    x1: str            # 'YYYY-MM-DD'
    y1: float
    x2: str
    y2: float
    label: str | None = None
    color: str | None = None


class ChartHLine(BaseModel):
    price: float
    label: str | None = None
    color: str | None = None


class ChartVLine(BaseModel):
    time: str          # 'YYYY-MM-DD'
    label: str | None = None
    color: str | None = None


class ChartZone(BaseModel):
    t0: str
    t1: str
    label: str | None = None
    color: str | None = None


class ChartAnnotations(BaseModel):
    """PH-VIZ-3: agent-authored overlays. Gemini decides WHAT to draw from the question/
    answer; descriptive over historical data only — no future projection / price target."""

    lines: list[ChartLine] = []
    hlines: list[ChartHLine] = []
    vlines: list[ChartVLine] = []
    zones: list[ChartZone] = []
    rebase: bool = False
    note: str | None = None


class OverlayPoint(BaseModel):
    time: str          # 'YYYY-MM-DD'
    value: float


class OverlayLine(BaseModel):
    """One plotted line of a technical indicator (e.g. SMA, Bollinger Upper, MACD Signal)."""

    label: str
    color: str | None = None
    points: list[OverlayPoint] = []


class ChartOverlay(BaseModel):
    """PH-VIZ-4: a descriptive technical indicator rendered ON the price chart
    (``pane='price'`` — SMA/EMA/Bollinger) or in a stacked sub-pane (``pane='sub'`` —
    RSI/MACD/volatility). Computed from prices (PH-DATA-6), so sourced 'computed from
    Yahoo'. Descriptive only — never a trading signal (the agent guardrail still refuses
    advice)."""

    key: str
    name: str
    pane: str = "price"        # price | sub
    unit: str | None = None    # price | ratio_0_100 | percent
    lines: list[OverlayLine] = []
    source: str | None = None


class NarrativeSection(BaseModel):
    """CE-4: one section of a 종목 내러티브 (관전 포인트) — a heading + sourced prose. Built from
    the synthesized answer's markdown; each body keeps its inline [n] anchors to the citations."""

    heading: str
    body: str


class CalcRow(BaseModel):
    """One labelled line in a computation trace — an input, an assumption, or a step result."""
    label: str
    value: str
    source: str | None = None    # where this input came from (e.g. "SEC EDGAR · FY2024")


class Computation(BaseModel):
    """How a self-computed figure was derived. Our figures are either a single sourced datum OR the
    OUTPUT of a formula over sourced inputs; for the latter there is no source *page* to open, so the
    trust envelope is showing the math — what was queried, what was assumed, what formula, what came
    out. Attached to the Artifact and rendered as a '계산 근거' panel. (Not a forecast: assumptions are
    the user's, the base figures are sourced — same honesty contract as the valuation disclaimer.)"""
    method: str                          # the approach (e.g. "2단계 FCF 할인 (DCF)")
    formula: str | None = None           # the formula in words
    inputs: list[CalcRow] = []           # the sourced base figures the calc consumed
    assumptions: list[CalcRow] = []      # the tunable assumptions (growth, discount rate, …)
    steps: list[CalcRow] = []            # intermediate results leading to the figure
    note: str | None = None              # disclaimer / caveat


class Artifact(BaseModel):
    """A typed, connector-backed figure emitted alongside prose (U3). The web renders
    it as an interactive card (TradingView Lightweight Charts); gaps are drawn, never hidden."""

    kind: str                    # timeseries | candlestick | compare | table | kpi | narrative
    # how a timeseries should render: bar (money amounts — revenue/income) vs line (ratios,
    # prices). Set by the artifact builder per the series' nature; the web honors it.
    chart_style: str | None = None  # bar | line | area
    title: str
    series: list[ArtifactSeries] = []
    # for kind=candlestick (prices): real OHLCV bars rendered as candles + a volume pane.
    candles: list[ArtifactCandle] = []
    # PH-VIZ-2: sourced event markers + descriptive reference lines on a price chart.
    markers: list[ArtifactMarker] = []
    pricelines: list[ArtifactPriceLine] = []
    # PH-VIZ-3: agent-authored annotations driven by the question (lines/zones/levels).
    annotations: ChartAnnotations | None = None
    # PH-VIZ-5: drawings the USER added on the chart (trend/horizontal lines + notes). Kept
    # separate from agent `annotations` so a re-answer / refresh never clobbers them; persists
    # with the Board pin (carried across refresh by studio-api). Agent never sets this.
    user_annotations: ChartAnnotations | None = None
    # PH-VIZ-4: descriptive technical indicators overlaid on the price chart (SMA/EMA/
    # Bollinger) + stacked sub-panes (RSI/MACD/volatility). Computed from prices.
    overlays: list[ChartOverlay] = []
    # for kind in {table, kpi}: a header-first matrix (e.g. [["지표","값","기간"], …]).
    # each data row is sourced via the matching Citation → /evidence (PH-DATA-5).
    table: list[list[str]] | None = None
    # for kind=narrative (CE-4): the structured 종목 내러티브 sections (heading + sourced body).
    sections: list[NarrativeSection] = []
    # for self-computed figures (valuation/backtest/screener): the auditable derivation — what was
    # queried, assumed, and the formula → the figure. Rendered as a '계산 근거' panel. (PH-DATA-6)
    computation: Computation | None = None
    source: str | None = None
    as_of: str | None = None
    freshness: str | None = None
    ticker: str | None = None
    has_gap: bool = False
    url: str | None = None       # canonical link to the filing/source the figures came from
    tool: str | None = None      # the tool that produced it (lets a pinned card ↻ refresh)
    args: dict | None = None     # the tool args, so a pinned card can re-fetch (U3-03)
    # periodicity + category of the producing datasource (from the catalog). `cadence` gates the
    # pin→alert flow: a pinned chart/table can carry a notification bot iff cadence != one_shot.
    cadence: str | None = None   # intraday|daily|event|scheduled|streaming|one_shot
    category: str | None = None  # market|fundamentals|valuation|filings|gurus|macro|news|…


class Step(BaseModel):
    tool: str
    args: dict
    status: int


class RunResult(BaseModel):
    answer: str
    refused: bool = False
    steps: list[Step] = []
    citations: list[Citation] = []
    artifacts: list[Artifact] = []
    usage: dict = {}
