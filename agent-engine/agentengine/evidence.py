"""The datasets `/evidence?…` params URL for a filing-backed figure.

Our normalized statement fields → ordered candidate us-gaap concepts (mirror of the
datasets INCOME/BALANCE/CASHFLOW maps; the same field maps to different tags across
filers, so the viewer tries each in order). We only compose the link deterministically —
the frontend opens the filing in-app and highlights the cited figure lazily, so the
answer stream is never blocked.
"""

from __future__ import annotations

from urllib.parse import urlencode

_FIELD_CONCEPTS: dict[str, list[str]] = {
    # income statement (duration contexts)
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "operating_expense": ["OperatingExpenses", "CostsAndExpenses"],
    "selling_general_and_administrative_expenses": ["SellingGeneralAndAdministrativeExpense"],
    "research_and_development": ["ResearchAndDevelopmentExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "income_tax_expense": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "earnings_per_share": ["EarningsPerShareBasic"],
    "earnings_per_share_diluted": ["EarningsPerShareDiluted"],
    # balance sheet (instant contexts)
    "total_assets": ["Assets"],
    "current_assets": ["AssetsCurrent"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "inventory": ["InventoryNet"],
    "property_plant_and_equipment": ["PropertyPlantAndEquipmentNet"],
    "total_liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "shareholders_equity": ["StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    # cash-flow statement (duration contexts)
    "depreciation_and_amortization": ["DepreciationDepletionAndAmortization",
                                      "DepreciationAmortizationAndAccretionNet"],
    "net_cash_flow_from_operations": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "net_cash_flow_from_investing": ["NetCashProvidedByUsedInInvestingActivities"],
    "net_cash_flow_from_financing": ["NetCashProvidedByUsedInFinancingActivities"],
}
# per-statement result key (datasets shape) → fields we can anchor evidence on, in priority
# order (the representative headline first). PH-PROV3d widened this from 4 headlines to every
# field the statement models expose, so a non-revenue figure the answer cites gets its own
# evidence (not always revenue). Fields without a concept above still anchor by value.
_STATEMENT_HEADLINES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("income_statements", (
        "revenue", "net_income", "operating_income", "gross_profit", "cost_of_revenue",
        "research_and_development", "selling_general_and_administrative_expenses",
        "operating_expense", "income_tax_expense", "interest_expense", "ebit",
        "earnings_per_share", "earnings_per_share_diluted")),
    ("balance_sheets", (
        "total_assets", "total_liabilities", "shareholders_equity", "cash_and_equivalents",
        "current_assets", "current_liabilities", "inventory", "property_plant_and_equipment",
        "retained_earnings", "total_debt", "goodwill_and_intangible_assets")),
    ("cash_flow_statements", (
        "net_cash_flow_from_operations", "net_cash_flow_from_investing", "net_cash_flow_from_financing",
        "free_cash_flow", "capital_expenditure", "depreciation_and_amortization")),
)


def _ev_qs(market, accn, cik, concept, report_period, value) -> str | None:
    if not (accn and concept and report_period):
        return None
    q = {"market": market, "accession": accn, "concept": concept, "report_period": report_period, "value": value}
    if cik:
        q["cik"] = cik
    return "/evidence?" + urlencode(q)


def _statement_url(market, data, accn, cik) -> str | None:
    """Financial statements (income / balance / cash-flow) → /evidence for the newest
    period's first available headline figure. US anchors candidate us-gaap concepts; KR
    anchors the field name directly (the DART matcher resolves it to the account label)."""
    for key, headlines in _STATEMENT_HEADLINES:
        rows = [r for r in (data.get(key) or []) if isinstance(r, dict) and r.get("report_period")]
        rows.sort(key=lambda r: str(r.get("report_period")), reverse=True)
        for r in rows:
            for field in headlines:
                if r.get(field) is not None:
                    concept = field if market == "KR" else ",".join(_FIELD_CONCEPTS[field])
                    return _ev_qs(market, r.get("accession_number") or accn, cik, concept,
                                  r.get("report_period"), r.get(field))
    return None


def rag_evidence_url(market, accession, text: str | None) -> str | None:
    """PH-PROV3e: `/evidence` for a cited filing PASSAGE — highlight the text span in the
    cached PDF (text mode). Only filing hits carry an accession; news/web hits don't."""
    m = (market or "").upper()
    if m not in ("US", "KR") or not accession or not text:
        return None
    return "/evidence?" + urlencode({"market": m, "accession": accession, "text": text[:200]})


def _fmt_variants(value) -> set[str]:
    """How a figure may appear in prose: thousands-comma'd at each unit scale (a $391,035M
    line is written '391,035' or '391.0' etc.). Used to detect which figure the answer cites."""
    out: set[str] = set()
    try:
        v = abs(float(value))
    except (TypeError, ValueError):
        return out
    for scale in (1, 1_000, 1_000_000, 100_000_000, 1_000_000_000_000):
        q = v / scale
        if q >= 1 and abs(q - round(q)) < 0.5:
            out.add(f"{round(q):,}")
    return out


def evidence_url_for_answer(data, accn, cik, market, answer: str | None) -> str | None:
    """PH-PROV3d: anchor evidence on the statement figure the ANSWER actually cites (so a
    net-income / R&D / assets question highlights THAT line, not always revenue). Scans every
    field, newest period first, for one whose value appears in the answer text; falls back to
    the representative headline (`_evidence_url`) when nothing matches."""
    m = (market or "").upper()
    if m not in ("US", "KR") or not accn or not isinstance(data, dict) or not answer:
        return _evidence_url(data, accn, cik, market)
    for key, fields in _STATEMENT_HEADLINES:
        rows = [r for r in (data.get(key) or []) if isinstance(r, dict) and r.get("report_period")]
        rows.sort(key=lambda r: str(r.get("report_period")), reverse=True)
        for r in rows:
            for field in fields:
                v = r.get(field)
                if v is None:
                    continue
                if any(t in answer for t in _fmt_variants(v)):
                    concept = field if m == "KR" else ",".join(_FIELD_CONCEPTS.get(field, [field]))
                    return _ev_qs(m, r.get("accession_number") or accn, cik, concept, r.get("report_period"), v)
    return _evidence_url(data, accn, cik, market)


def _evidence_url(data, accn, cik, market) -> str | None:
    """The datasets `/evidence?…` params URL for a filing-backed result's headline figure.
    The frontend opens the filing in-app and highlights it lazily — we only attach the link
    (deterministic), so the answer stream is never blocked. US handles both the
    as-reported shape (explicit us-gaap concept) and the statement shape; KR uses
    the statement shape, anchored on the field name. None when there's nothing to point at."""
    m = (market or "").upper()
    if m not in ("US", "KR") or not accn or not isinstance(data, dict):
        return None
    if m == "KR":  # DART has no as-reported XBRL — statement figures only
        return _statement_url("KR", data, accn, cik)
    # US as-reported: explicit us-gaap concept per line item
    periods = data.get("periods")
    if isinstance(periods, list) and periods and isinstance(periods[0], dict):
        p = periods[0]
        items = [it for it in (p.get("line_items") or []) if isinstance(it, dict)]
        pick = next((it for it in items if "Revenue" in (it.get("concept") or "")), items[0] if items else None)
        if pick and pick.get("value") is not None and pick.get("concept"):
            return _ev_qs("US", pick.get("accession_number") or accn, cik, pick["concept"],
                          p.get("report_period"), pick["value"])
    return _statement_url("US", data, accn, cik)
