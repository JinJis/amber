"""Shared catalog vocabulary: reusable resource params, license policies, and
provenance specs referenced across the connector manifests.

Extracted from ``catalog.py`` so the governance/policy constants (who may
redistribute what, where each figure's source + as-of comes from) live apart from
the connector/resource listing. ``catalog.py`` imports these to build ``CONNECTORS``.
"""

from __future__ import annotations

from app.connectors.manifest import (
    Freshness,
    License,
    Provenance,
    ResourceParam,
)

# --- reusable params -----------------------------------------------------
P_TICKER = ResourceParam(name="ticker", description="Ticker symbol (US: AAPL; KR: 005930).")
# Same field, but required — for data pulls that are meaningless without a company
# (prices, financial statements). company_facts/news keep the optional P_TICKER.
P_TICKER_REQ = ResourceParam(name="ticker", required=True, description="Ticker symbol (US: AAPL; KR: 005930).")
P_MARKET = ResourceParam(name="market", enum=["US", "KR"], description="Market (default US).")
P_PERIOD = ResourceParam(name="period", required=True, enum=["annual", "quarterly", "ttm"], description="Reporting period.")
P_LIMIT = ResourceParam(name="limit", type="integer", description="Max rows.")

# --- license policies ----------------------------------------------------
LIC_SEC = License(id="us-public-domain", redistribution=True, attribution_required=False,
                  note="U.S. government works (SEC EDGAR) are public domain.")
LIC_FRED = License(id="fred-terms", redistribution=True, attribution_required=True,
                   note="FRED and BIS central-bank policy rates (the latter served keyless via DBnomics) are "
                        "freely usable with attribution; some FRED series carry source-specific restrictions — check per series.",
                   terms_url="https://fred.stlouisfed.org/legal/")
LIC_DART = License(id="kr-opendart", redistribution=True, attribution_required=True,
                   note="OpenDART (FSS) public disclosure data; attribute the source.",
                   terms_url="https://opendart.fss.or.kr/")
LIC_ECOS = License(id="kr-bok-ecos", redistribution=True, attribution_required=True,
                   note="Bank of Korea ECOS economic statistics; attribute the source.",
                   terms_url="https://ecos.bok.or.kr/")
LIC_YAHOO = License(id="restricted-byo", redistribution=False, attribution_required=True,
                    note="Yahoo Finance ToS restricts redistribution — internal/BYO-key use; not redistributable to tenants.")
LIC_NEWS = License(id="restricted-headlines", redistribution=False, attribution_required=True,
                   note="Publisher copyright — store links + minimal quoting only; not redistributable as full text.")
LIC_DERIVED = License(id="derived-public", redistribution=True, attribution_required=True,
                      note="Derived from public SEC/DART filings held in the ingestion store.")

# --- provenance specs ----------------------------------------------------
PROV_SEC = Provenance(source="SEC EDGAR", as_of_field="report_period", source_link_field="filing_url", freshness=Freshness.periodic)
PROV_SEC_FILINGS = Provenance(source="SEC EDGAR", as_of_field="filing_date", source_link_field="url", freshness=Freshness.periodic)
PROV_YAHOO = Provenance(source="Yahoo Finance", as_of_field="time", freshness=Freshness.eod)
# IMP-15: prices/snapshot ride a fallback chain (Yahoo → Stooq/KIS); the response's own
# `source` field names the upstream that actually served, so citations stay honest.
PROV_PRICES = Provenance(source="Yahoo Finance (폴백: US Stooq · KR KIS — 응답 source 필드가 실제 제공자)",
                         as_of_field="time", freshness=Freshness.eod)
PROV_FRED = Provenance(source="BIS / FRED (central-bank policy rates)", as_of_field="date", freshness=Freshness.periodic)
PROV_DART = Provenance(source="OpenDART (FSS)", as_of_field="report_period", source_link_field="filing_url", freshness=Freshness.periodic)
PROV_ECOS = Provenance(source="Bank of Korea ECOS", as_of_field="date", freshness=Freshness.periodic)
PROV_DBNOMICS = Provenance(source="DBnomics", as_of_field="date", source_link_field="source_url", freshness=Freshness.periodic)
PROV_TECHNICAL = Provenance(source="Technical indicators (computed from Yahoo Finance)", as_of_field="as_of", freshness=Freshness.eod)
PROV_NEWS = Provenance(source="Google News", as_of_field="date", source_link_field="url", freshness=Freshness.realtime)
PROV_DART_FILINGS = Provenance(source="OpenDART (FSS)", as_of_field="filing_date", source_link_field="url", freshness=Freshness.periodic)
