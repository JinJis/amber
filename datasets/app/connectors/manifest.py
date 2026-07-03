"""Connector manifest — the platform keystone.

Each data source (connector) publishes a machine-readable manifest describing its
resources, their params/output, the **provenance** outputs carry, freshness/cost,
the upstream credential it needs, and its **license / redistribution policy**.

This single artifact is read by every later surface:
  * REST docs · MCP tool generation · RAG source registration · NL grounding
  * entitlements (a tenant activates a connector) · metering (cost tier)
  * governance (license policy → what may be redistributed to tenants)

P0 adds the descriptor + a catalog endpoint; it changes no endpoint behavior.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class CostTier(str, Enum):
    free = "free"  # keyless / no upstream cost
    low = "low"
    medium = "medium"
    high = "high"


class Category(str, Enum):
    """User-facing tool category — the INTUITIVE grouping the agent builder shows, orthogonal
    to which upstream API a tool comes from. Connectors stay the data-plane routing unit; users
    pick individual tools grouped by these categories (never by API). Every Resource carries one.
    """

    market = "market"  # 금융시장 현황 — 지수·자산군·섹터·시세·기술지표
    fundamentals = "fundamentals"  # 종목 재무분석 — 재무제표·회사정보·기업검색
    valuation = "valuation"  # 밸류에이션·비교 — 지표·동종업계 비교
    filings = "filings"  # 공시·문서 — 공시·실적·문서 의미검색
    gurus = "gurus"  # 투자거장·수급 — 13F·기관/내부자/ETF 보유
    macro = "macro"  # 거시경제 분석 — 금리·물가·고용
    news = "news"  # 뉴스룸 — 최신 헤드라인
    screener = "screener"  # 스크리너·퀀트 — 재무 기준 스크리닝
    portfolio = "portfolio"  # 포트폴리오 — 백테스트·포트폴리오 분석


# Ordered, user-facing metadata for the builder (label + one-line description). The ONLY place
# category names live; the agent builder + catalog endpoint derive everything from this.
CATEGORIES: list[dict] = [
    {"id": "market", "label": "금융시장 현황", "description": "지수·자산군·섹터·시세·기술적 지표"},
    {"id": "fundamentals", "label": "종목 재무분석", "description": "재무제표·회사정보·기업 검색"},
    {"id": "valuation", "label": "밸류에이션·비교", "description": "밸류에이션 지표·동종업계 비교"},
    {"id": "filings", "label": "공시·문서", "description": "공시·실적·문서 의미검색(RAG)"},
    {"id": "gurus", "label": "투자거장·수급", "description": "거장 13F·기관/내부자/ETF 보유"},
    {"id": "macro", "label": "거시경제 분석", "description": "금리·물가·고용 등 거시지표 (미국·한국)"},
    {"id": "news", "label": "뉴스룸", "description": "최신 뉴스 헤드라인"},
    {"id": "screener", "label": "스크리너·퀀트", "description": "재무·팩터 기준 종목 스크리닝"},
    {"id": "portfolio", "label": "포트폴리오 관리", "description": "포트폴리오 백테스트·성과 분석"},
]


class Freshness(str, Enum):
    realtime = "realtime"
    eod = "eod"  # end-of-day / delayed price
    periodic = "periodic"  # as fresh as the last filing/report
    static = "static"


class Cadence(str, Enum):
    """How a datasource RECURS — orthogonal to ``Freshness`` (which is about staleness).

    This is the periodicity axis the product hangs the pin→alert flow on: a widget can carry a
    notification bot **iff** its datasource is periodic (``cadence != one_shot``). The specific
    cadence also picks the natural alert trigger (price threshold / new disclosure / release /
    feed). ``one_shot`` data is a point-in-time value you pin and glance at — no future event to
    push, so no bell. Set centrally via the ``_RESOURCE_META`` map in ``catalog.py`` (load fails if
    any resource is unclassified — same enforcement as ``category``).
    """

    intraday = "intraday"  # realtime quotes / rankings (KIS) — periodic
    daily = "daily"  # end-of-day series (prices, technicals, daily flows) — periodic
    event = "event"  # on disclosure/announcement (filings, earnings, 13F, corp actions) — periodic
    scheduled = "scheduled"  # on a release calendar (rates, macro indicators) — periodic
    streaming = "streaming"  # rolling feed (news) — periodic
    one_shot = "one_shot"  # a value/snapshot with no future update event — NOT periodic

    @property
    def periodic(self) -> bool:
        return self is not Cadence.one_shot


class License(BaseModel):
    """Redistribution policy for a connector's data — drives platform governance."""

    id: str = Field(..., description="Policy id, e.g. 'us-public-domain', 'restricted-byo'.")
    redistribution: bool = Field(..., description="May the platform redistribute this data to tenants?")
    attribution_required: bool = False
    note: str | None = None
    terms_url: str | None = None


class Provenance(BaseModel):
    """Which provenance fields this resource's output carries (the trust envelope)."""

    source: str = Field(..., description="Human name of the upstream source, e.g. 'SEC EDGAR'.")
    as_of_field: str | None = Field(None, description="Output field giving the data's as-of date.")
    source_link_field: str | None = Field(None, description="Output field linking to the source document.")
    confidence: bool = Field(False, description="Whether outputs carry a confidence tier/interval yet.")
    freshness: Freshness = Freshness.periodic


class ResourceParam(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str | None = None
    enum: list[str] | None = None


class Resource(BaseModel):
    name: str = Field(..., description="Resource id within the connector, e.g. 'income_statements'.")
    description: str
    method: str = "GET"
    path: str = Field(..., description="The REST path that serves this resource (must be a real route).")
    params: list[ResourceParam] = []
    output_model: str | None = Field(None, description="Generated response-model class name, when applicable.")
    markets: list[str] = ["US", "KR"]
    cost_tier: CostTier = CostTier.low
    # User-facing category (set centrally in catalog.py via the _RESOURCE_META map; enforced at load
    # — a resource with no mapping fails catalog construction, so every tool stays categorized).
    category: Category | None = None
    # Periodicity class (set centrally in catalog.py via the _RESOURCE_META map; enforced at load).
    # Drives the pin→alert gate: only ``cadence.periodic`` datasources can carry a notification bot.
    cadence: Cadence | None = None
    provenance: Provenance


class UpstreamCredential(BaseModel):
    requires_key: bool
    key_env: str | None = None
    signup_url: str | None = None


class ConnectorManifest(BaseModel):
    id: str = Field(..., description="Stable connector id, e.g. 'sec_edgar'. Unit of activation.")
    name: str
    domain: str = Field(..., description="Primary domain, e.g. 'us-fundamentals', 'prices', 'macro'.")
    description: str
    markets: list[str]
    # Which backend service serves this connector's resources — the gateway proxies accordingly.
    service: str = Field("datasets", description="Backing service: 'datasets' (data plane) or 'rag'.")
    upstream: UpstreamCredential
    license: License
    resources: list[Resource]
