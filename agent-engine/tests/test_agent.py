"""Agent Engine unit tests (Gemini-only) with a respx-mocked gateway.

The platform is Gemini-only: there is no stub planner. This keyless unit suite clears the
Gemini key so nothing makes a real LLM call; loop/streaming/provenance tests that need a planner
inject a small deterministic fake planner via monkeypatch. Live routing/quality is covered by the
eval suite, not here."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from agentengine import agent as A
from agentengine import planner as P
from agentengine.config import settings
from agentengine.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Keyless unit suite: clear the Gemini key so nothing makes a real LLM call (tests that
    exercise the loop inject their own fake planner via monkeypatch)."""
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    P._build_planner.cache_clear()
    yield
    P._build_planner.cache_clear()


def _fake_planner(tool, args):
    """A deterministic planner for loop tests: emit one tool call, then finalize."""
    from agentengine.planner import Decision

    class _FP:
        async def plan(self, task, tools, history, system=None, conversation=None,
                       force_final=False, sources=None):
            if history or force_final:
                return Decision(final="AAPL was 100 [1].")
            return Decision(tool=tool, args=args)

        async def plan_batch(self, *a, **k):
            return [await self.plan(*a, **k)]

        async def stream_final(self, *a, **k):
            yield "AAPL was 100 [1]."

    return _FP()

CATALOG = {"connectors": [
    {"id": "yahoo", "resources": [{
        "name": "prices", "method": "GET", "path": "/prices", "description": "EOD prices",
        "params": [{"name": "ticker", "required": True}, {"name": "interval", "required": True},
                   {"name": "start_date", "required": True}, {"name": "end_date", "required": True}, {"name": "market"}],
        "provenance": {"source": "Yahoo Finance"}}]},
    {"id": "sec_edgar", "resources": [{
        "name": "company_facts", "method": "GET", "path": "/company/facts", "description": "facts",
        "params": [{"name": "ticker"}, {"name": "market"}], "provenance": {"source": "SEC EDGAR"}}]},
    {"id": "rag", "resources": [{
        "name": "search", "method": "POST", "path": "/rag/search", "description": "semantic search",
        "params": [{"name": "query", "required": True}], "provenance": {"source": "Platform RAG"}}]},
]}


def _gw(monkeypatch):
    monkeypatch.setattr(settings, "gateway_url", "http://gw.test")


def _catalog():
    respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(200, json=CATALOG))


# --- guardrails -----------------------------------------------------------
async def test_intake_falls_back_to_allow_when_llm_unavailable():
    # No key → the intake can't call Gemini, so it degrades to allow + default budget (never
    # blocks on an unavailable LLM). There is NO keyword/regex guardrail (invariant #9).
    intake = await A.analyze_task("should I buy TSLA?", "gemini")
    assert intake.restricted is False and intake.steps == settings.max_steps and intake.plan is None


def test_citations_extract_urls_from_nested_result():
    tool = {"name": "sec_edgar__filings", "source": "SEC EDGAR"}
    result = {"data": {"filings": [{"filing_url": "https://sec.gov/a"}, {"filing_url": "https://sec.gov/b"}]}}
    cites = A._citations(tool, result)
    urls = {c.url for c in cites}
    assert urls == {"https://sec.gov/a", "https://sec.gov/b"}
    assert all(c.source == "SEC EDGAR" for c in cites)


def test_citations_fallback_to_source_when_no_url():
    tool = {"name": "yahoo__prices", "source": "Yahoo Finance"}
    cites = A._citations(tool, {"data": {"ticker": "AAPL", "prices": []}})
    assert len(cites) == 1 and cites[0].url is None and cites[0].source == "Yahoo Finance"


def test_filing_link_canonical_per_market():
    # KR → DART rcpNo viewer (deterministic); US → SEC index page (needs CIK)
    assert A._filing_link("KR", "20260605000073") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260605000073"
    assert A._filing_link("US", "0000320193-25-000079", "320193") == \
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    assert A._filing_link("US", "0000320193-25-000079", None) is None
    assert A._filing_link("US", None) is None


def test_citations_metrics_show_real_figures_and_canonical_link():
    # a derived-metrics result → ONE evidence card with the actual figures + a table +
    # the canonical filing link built from accession (not a directory listing).
    tool = {"name": "datasets_store__metrics_history", "source": "SEC EDGAR", "connector": "datasets_store"}
    data = {"market": "US", "ticker": "AAPL", "metrics": [
        {"report_period": "2025-12-31", "gross_margin": 0.46, "net_margin": 0.25,
         "accession_number": "0000320193-25-000079", "cik": "320193"},
        {"report_period": "2024-12-31", "gross_margin": 0.44, "net_margin": 0.23,
         "accession_number": "0000320193-24-000123", "cik": "320193"}]}
    cites = A._citations(tool, {"data": data})
    assert len(cites) == 1
    c = cites[0]
    assert c.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm"
    assert "46.0%" in (c.snippet or "")          # the real figure, not "지표 계산값"
    assert c.table and c.table[0][0] == "기간"     # extracted table, header first
    assert "2025-12-31" in c.table[1]
    assert c.page == "0000320193-25-000079"


def test_mark_evidence_only_cited_or_artifact_backing():
    from agentengine.models import Artifact
    cites = [A.Citation(tool="t1", source="A", url="u1", index=1, snippet="x"),
             A.Citation(tool="t2", source="B", url="u2", index=2, snippet="y"),
             A.Citation(tool="t3", source="C", url="u3", index=3)]
    arts = [Artifact(kind="timeseries", title="z", tool="t3")]
    A.mark_evidence(cites, "핵심 수치는 이렇습니다 [1].", arts)
    used = {c.index for c in cites if c.used}
    assert used == {1, 3}            # [1] cited + t3 backs an artifact; t2 consulted-only


def test_valuation_artifact_carries_computation_trace():
    # PH-DATA-6: a DCF result → an Artifact whose `computation` exposes the auditable derivation
    # (sourced inputs, the user's assumptions, the formula, and the intermediate math).
    from agentengine.artifacts import _build_artifacts
    data = {
        "model": "dcf", "ticker": "AAPL", "value_per_share": 175.0, "source": "SEC EDGAR",
        "as_of": "2024-09-28",
        "assumptions": {"growth_rate": 0.08, "discount_rate": 0.10, "years": 5, "terminal_growth": 0.025},
        "inputs": {"base_fcf": 1.0e11, "shares": 1.6e10, "net_debt": -5.0e10},
        "breakdown": {"enterprise_value": 3.0e12, "equity_value": 2.8e12, "pv_explicit": 4.0e11,
                      "pv_terminal": 2.6e12, "terminal_value": 4.0e12,
                      "rows": [{"year": 1, "fcf": 1.08e11, "pv": 9.8e10}]},
        "disclaimer": "예측이 아닙니다.",
    }
    arts = _build_artifacts({"name": "valuation__valuation", "source": "SEC EDGAR"}, {"data": data})
    assert arts and arts[0].computation is not None
    comp = arts[0].computation
    assert comp.method == "2단계 FCF 할인 (DCF)" and comp.formula
    # assumptions are shown as %/years, inputs are sourced, steps include the intermediate math
    assert any(r.label == "할인율" and r.value == "10.0%" for r in comp.assumptions)
    assert any(r.label.startswith("기준 FCF") and r.source == "SEC EDGAR" for r in comp.inputs)
    assert any(r.label == "기업가치 EV" for r in comp.steps)


def test_quant_screen_and_backtest_carry_computation():
    from agentengine.artifacts import _build_artifacts
    # quant screen: criteria + per-factor formulas exposed
    q = {"market": "US", "factors": ["pe", "roe"], "sort": "roe", "order": "desc", "count": 2,
         "applied_filters": [{"field": "pe", "operator": "lte", "value": 15}],
         "results": [{"ticker": "AAPL", "market_cap": 3e12, "pe": 12.0, "pb": 40.0, "roe": 1.5,
                      "return_window": 0.2}]}
    qa = _build_artifacts({"name": "search__quant_screen", "source": "store"}, {"data": q})
    assert qa and qa[0].computation
    assert any(r.value.startswith("≤") for r in qa[0].computation.assumptions)  # PER ≤ 15
    assert any(r.label == "PE" for r in qa[0].computation.steps)                # factor formula

    # backtest: holdings + window as inputs, performance metrics as steps
    b = {"start": "2020-01-01", "end": "2024-01-01", "initial": 1.0e4,
         "holdings": [{"ticker": "AAPL", "weight": 0.6}, {"ticker": "MSFT", "weight": 0.4}],
         "metrics": {"total_return": 0.85, "cagr": 0.17, "max_drawdown": -0.22},
         "curve": [{"date": "2020-01-01", "value": 10000}, {"date": "2024-01-01", "value": 18500}]}
    ba = _build_artifacts({"name": "backtest__backtest", "source": "store"}, {"data": b})
    assert ba and ba[0].computation
    assert any(r.label == "AAPL" for r in ba[0].computation.inputs)
    assert any(r.label == "누적수익" for r in ba[0].computation.steps)


def test_valuation_ddm_and_rim_carry_their_own_trace():
    from agentengine.artifacts import _build_artifacts
    # DDM — dividend-discount; the trace shows the DDM formula, the dividend input + assumptions,
    # and the next-dividend step (D1).
    ddm = {"model": "ddm", "ticker": "KO", "value_per_share": 60.0, "source": "사용자 입력 (D0)",
           "assumptions": {"growth_rate": 0.05, "discount_rate": 0.09, "dividend_per_share": 1.84},
           "inputs": {"dividend_per_share": 1.84}, "breakdown": {"d0": 1.84, "d1": 1.93}}
    a = _build_artifacts({"name": "valuation__valuation", "source": "x"}, {"data": ddm})
    assert a and a[0].computation and a[0].computation.method == "배당할인 (DDM)"
    assert "D1" in (a[0].computation.formula or "")
    assert any(r.label == "차기 배당 D1" for r in a[0].computation.steps)

    # RIM — residual income; the trace shows BVPS/ROE inputs and the residual-income PV step.
    rim = {"model": "rim", "ticker": "MSFT", "value_per_share": 320.0, "source": "SEC EDGAR",
           "assumptions": {"growth_rate": 0.06, "discount_rate": 0.10, "years": 5},
           "inputs": {"bvps": 30.0, "roe": 0.35, "equity": 2.2e11, "shares": 7.4e9},
           "breakdown": {"bvps": 30.0, "pv_residual": 4.2e11, "pv_terminal": 6.0e11,
                         "rows": [{"year": 1, "bvps": 31.8, "residual_income": 9.0, "pv": 8.2}]}}
    b = _build_artifacts({"name": "valuation__valuation", "source": "x"}, {"data": rim})
    assert b and b[0].computation and b[0].computation.method == "잔여이익 (RIM)"
    assert any(r.label == "ROE" for r in b[0].computation.inputs)
    assert any(r.label == "잔여이익 PV 합" for r in b[0].computation.steps)


def test_non_computed_artifact_has_no_computation_trace():
    # a sourced (not self-computed) figure — a price series — is a single datum per point, so it
    # carries NO computation trace (it opens the source instead). Guards against over-attaching.
    from agentengine.artifacts import _build_artifacts
    data = {"ticker": "AAPL", "prices": [{"time": "2024-01-02", "open": 1, "high": 2, "low": 1,
                                          "close": 2, "volume": 100}]}
    arts = _build_artifacts({"name": "yahoo__prices", "source": "Yahoo Finance"}, {"data": data})
    assert arts and all(a.computation is None for a in arts)


def test_valuation_without_value_emits_no_artifact():
    # honesty: when the base data is insufficient (no per-share value, empty breakdown), the DCF
    # handler emits nothing rather than a hollow computation panel.
    from agentengine.artifacts import _build_artifacts
    data = {"model": "dcf", "ticker": "ZZZZ", "value_per_share": None,
            "assumptions": {"growth_rate": 0.08, "discount_rate": 0.10, "years": 5},
            "inputs": {"base_fcf": None, "shares": None}, "breakdown": {}}
    assert _build_artifacts({"name": "valuation__valuation", "source": "x"}, {"data": data}) == []


def test_mark_evidence_fallback_when_no_inline_anchors():
    cites = [A.Citation(tool="t1", source="A", url="u1", index=1, snippet="real"),
             A.Citation(tool="t2", source="B", index=2)]  # bare label, no data
    A.mark_evidence(cites, "근거를 정리했습니다.", [])
    assert cites[0].used and not cites[1].used   # data-bearing is evidence; bare label is not


def test_citations_prices_and_generic_show_real_values():
    # prices → a date/close table + latest-close snippet (no filing link, that's fine)
    tool = {"name": "yahoo__prices", "source": "Yahoo Finance", "connector": "yahoo"}
    data = {"ticker": "AAPL", "prices": [
        {"time": "2026-06-12T00:00:00", "close": 210.5}, {"time": "2026-06-13T00:00:00", "close": 213.0}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.table[0] == ["날짜", "종가"] and c.table[1][0] == "2026-06-13"  # newest first
    assert "213" in (c.snippet or "")
    # generic tabular result → a table of its real values (scales to new data sources)
    tool2 = {"name": "datasets_store__insider", "source": "SEC EDGAR", "connector": "datasets_store"}
    data2 = {"trades": [{"insider": "CEO", "shares": 1000, "filing_url": "https://x"}]}
    c2 = A._citations(tool2, {"data": data2})[0]
    assert c2.table is not None and c2.url == "https://x"


def test_evidence_url_attached_for_us_as_reported_filing():
    # an as-reported (US) result → the citation carries an /evidence URL for the headline
    # figure (PH-PROV2); the frontend fetches the highlighted screenshot lazily.
    tool = {"name": "sec_edgar__as_reported", "source": "SEC EDGAR", "connector": "sec_edgar"}
    data = {"ticker": "AAPL", "periods": [{"report_period": "2024-09-28", "line_items": [
        {"concept": "Revenues", "value": 391035000000.0, "accession_number": "0000320193-24-000123", "cik": "320193"},
        {"concept": "Assets", "value": 352755000000.0, "accession_number": "0000320193-24-000123", "cik": "320193"}]}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.evidence_image_url is not None
    assert "/evidence?" in c.evidence_image_url
    assert "concept=Revenues" in c.evidence_image_url and "accession=0000320193-24-000123" in c.evidence_image_url
    assert "report_period=2024-09-28" in c.evidence_image_url


def test_evidence_url_for_income_statements_uses_candidate_concepts():
    # the common income_statements tool uses OUR normalized fields → reverse-map to candidate
    # us-gaap concepts so the answer's revenue figure still gets an evidence link (PH-PROV2b).
    tool = {"name": "sec_edgar__income_statements", "source": "SEC EDGAR", "connector": "sec_edgar"}
    data = {"income_statements": [
        {"revenue": 391035000000.0, "net_income": 93736000000.0, "report_period": "2024-09-28",
         "accession_number": "0000320193-24-000123"},
        {"revenue": 383285000000.0, "report_period": "2023-09-30", "accession_number": "0000320193-23-000106"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.evidence_image_url and "/evidence?" in c.evidence_image_url
    assert "accession=0000320193-24-000123" in c.evidence_image_url      # newest period
    assert "report_period=2024-09-28" in c.evidence_image_url
    # revenue maps to a candidate list (try each tag in order at lookup time)
    assert "RevenueFromContractWithCustomerExcludingAssessedTax" in c.evidence_image_url
    assert "Revenues" in c.evidence_image_url


def test_evidence_url_for_kr_dart_statements():
    # PH-PROV2d: KR income_statements (OpenDART) → an /evidence URL anchored on the field
    # name (the DART matcher resolves it to the account label); market=KR, no us-gaap concept.
    tool = {"name": "opendart__income_statements", "source": "OpenDART (FSS)", "connector": "opendart"}
    data = {"income_statements": [
        {"revenue": 300870903000000.0, "net_income": 34451351000000.0, "report_period": "2024-12-31",
         "accession_number": "20250311000736"},
        {"revenue": 258935494000000.0, "report_period": "2023-12-31", "accession_number": "20240312000736"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.evidence_image_url and "/evidence?" in c.evidence_image_url
    assert "market=KR" in c.evidence_image_url
    assert "concept=revenue" in c.evidence_image_url          # field name, not a us-gaap tag
    assert "accession=20250311000736" in c.evidence_image_url  # newest period
    assert "report_period=2024-12-31" in c.evidence_image_url


def test_corporate_actions_citation_is_a_dividend_data_card():
    # PH-DATA-3: dividends/splits (Yahoo, no document) → data-card evidence (real values + source).
    tool = {"name": "yahoo__corporate_actions", "source": "Yahoo Finance", "connector": "yahoo"}
    data = {"ticker": "AAPL", "currency": "USD",
            "dividends": [{"ex_date": "2026-02-07", "amount": 0.25}, {"ex_date": "2025-11-07", "amount": 0.25}],
            "splits": [{"date": "2020-08-31", "ratio": "4:1"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.table and c.table[0] == ["배당락일", "배당금"]
    assert c.table[1][0] == "2026-02-07" and "배당" in (c.snippet or "")
    assert c.evidence_image_url is None  # no document → data card only


def test_macro_citation_is_a_clean_data_card():
    # PH-PROV3f: a non-document source (macro rates) → data-card evidence (exact values +
    # source + as_of), no PDF/evidence image.
    tool = {"name": "fred__interest_rates", "source": "BIS / FRED (central-bank policy rates)",
            "connector": "fred"}
    data = {"interest_rates": [
        {"bank": "FED", "name": "U.S. Federal Reserve", "rate": 4.375, "date": "2025-07-08"},
        {"bank": "FED", "name": "U.S. Federal Reserve", "rate": 4.5, "date": "2024-07-08"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.table and c.table[0] == ["기관", "금리", "기준일"]
    assert c.table[1] == ["U.S. Federal Reserve", "4.375%", "2025-07-08"]   # newest first
    assert c.snippet and "4.375%" in c.snippet and c.as_of == "2025-07-08"
    assert c.evidence_image_url is None  # no document → no highlight, just the data card


def test_rag_filing_citation_carries_passage_evidence():
    # PH-PROV3e: a RAG hit from a filing (has an accession) → evidence link in text mode;
    # a news hit (no accession) gets none.
    tool = {"name": "rag__search", "connector": "rag", "source": "RAG"}
    filing = {"hits": [{"text": "TSMC fabricates Apple's custom silicon under a multi-year supply agreement.",
                        "provenance": {"source": "SEC EDGAR", "doc_type": "filing", "market": "US",
                                       "accession": "0000320193-24-000123", "ticker": "AAPL",
                                       "section": "p.12", "url": "https://sec.gov/i"}}]}
    c = A._citations(tool, {"data": filing})[0]
    assert c.evidence_image_url and "text=" in c.evidence_image_url
    assert "accession=0000320193-24-000123" in c.evidence_image_url and "market=US" in c.evidence_image_url

    news = {"hits": [{"text": "Apple shares rose.",
                      "provenance": {"source": "Reuters", "doc_type": "news", "market": "US", "url": "https://r"}}]}
    assert A._citations(tool, {"data": news})[0].evidence_image_url is None


def test_evidence_anchors_on_the_figure_the_answer_cites():
    # PH-PROV3d: evidence highlights the line the ANSWER cites (net income), not always revenue.
    from agentengine.evidence import evidence_url_for_answer

    data = {"income_statements": [
        {"revenue": 391035000000.0, "net_income": 93736000000.0, "report_period": "2024-09-28",
         "accession_number": "0000320193-24-000123"}]}
    u_ni = evidence_url_for_answer(data, "0000320193-24-000123", "320193", "US",
                                   "Apple's net income was $93,736 million in FY2024.")
    assert u_ni and "concept=NetIncomeLoss" in u_ni and "value=93736000000" in u_ni
    u_rev = evidence_url_for_answer(data, "0000320193-24-000123", "320193", "US",
                                    "Total net sales were $391,035 million.")
    assert u_rev and ("Revenue" in u_rev or "concept=Revenues" in u_rev) and "93736000000" not in u_rev
    # no answer → falls back to the representative headline (revenue)
    u_fb = evidence_url_for_answer(data, "0000320193-24-000123", "320193", "US", "")
    assert u_fb and "Revenue" in u_fb


def test_evidence_url_for_balance_sheet_instant_context():
    # PH-PROV2c: the balance_sheets tool (instant XBRL contexts) → evidence link for the
    # headline figure (total_assets → us-gaap:Assets), plus an extracted balance table.
    tool = {"name": "sec_edgar__balance_sheets", "source": "SEC EDGAR", "connector": "sec_edgar"}
    data = {"balance_sheets": [
        {"total_assets": 364980000000.0, "total_liabilities": 308030000000.0,
         "shareholders_equity": 56950000000.0, "report_period": "2025-09-27",
         "accession_number": "0000320193-25-000079"},
        {"total_assets": 364980000000.0, "report_period": "2024-09-28",
         "accession_number": "0000320193-24-000123"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.evidence_image_url and "/evidence?" in c.evidence_image_url
    assert "concept=Assets" in c.evidence_image_url                      # total_assets → Assets
    assert "accession=0000320193-25-000079" in c.evidence_image_url      # newest period
    assert "report_period=2025-09-27" in c.evidence_image_url
    assert c.table and any("자산총계" in row for row in c.table)          # balance table rendered


def test_evidence_url_for_cash_flow_duration_context():
    # PH-PROV2c: the cash_flow_statements tool (duration contexts) → evidence link anchored
    # on operating cash flow (NetCashProvidedByUsedInOperatingActivities).
    tool = {"name": "sec_edgar__cash_flow_statements", "source": "SEC EDGAR", "connector": "sec_edgar"}
    data = {"cash_flow_statements": [
        {"net_cash_flow_from_operations": 118254000000.0, "net_cash_flow_from_investing": 9447000000.0,
         "net_cash_flow_from_financing": -108488000000.0, "report_period": "2025-09-27",
         "accession_number": "0000320193-25-000079"}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.evidence_image_url and "concept=NetCashProvidedByUsedInOperatingActivities" in c.evidence_image_url
    assert "report_period=2025-09-27" in c.evidence_image_url
    assert c.table and any("영업활동CF" in row for row in c.table)        # cash-flow table rendered


def test_evidence_url_none_for_non_filing_or_non_us():
    # prices (no filing) → no evidence URL
    c = A._citations({"name": "yahoo__prices", "source": "Yahoo Finance", "connector": "yahoo"},
                     {"data": {"ticker": "AAPL", "prices": [{"time": "2026-06-13", "close": 1.0}]}})[0]
    assert c.evidence_image_url is None


def test_rag_citation_builds_canonical_link_from_accession():
    # a RAG chunk with no url but a KR accession → DART viewer link (not linkless)
    tool = {"name": "rag__search", "connector": "rag", "source": "RAG"}
    result = {"data": {"hits": [{"text": "메모리 매출원가율 개선", "provenance": {
        "source": "OpenDART", "doc_type": "filing", "market": "KR",
        "accession": "20260605000073", "as_of": "2026-06-05"}}]}}
    cites = A._citations(tool, result)
    assert len(cites) == 1
    assert cites[0].url == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260605000073"
    assert cites[0].kind == "filing" and "메모리" in (cites[0].snippet or "")


def test_dedup_citations_collapses_repeats():
    from agentengine.models import Citation
    cites = [
        Citation(tool="opendart__income_statements", source="OpenDART (FSS)", url=None),
        Citation(tool="opendart__balance_sheets", source="OpenDART (FSS)", url=None),  # dup (src,url)
        Citation(tool="opendart__filings", source="OpenDART (FSS)", url="https://dart/x"),
    ]
    out = A.dedup_citations(cites)
    assert len(out) == 2  # the two same (source=None-url) collapse to one; the url'd one stays
    assert {(c.source, c.url) for c in out} == {("OpenDART (FSS)", None), ("OpenDART (FSS)", "https://dart/x")}


@respx.mock
async def test_fetch_tools_attaches_friendly_connector_name(monkeypatch):
    from agentengine.client import PlatformClient
    _gw(monkeypatch)
    catalog = {"connectors": [{"id": "opendart", "name": "OpenDART (KR)", "markets": ["KR"], "resources": [
        {"name": "income_statements", "method": "GET", "path": "/financials/income-statements",
         "description": "Income statements.", "params": [], "provenance": {"source": "OpenDART (FSS)"}}]}]}
    respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(200, json=catalog))
    tools = await PlatformClient("k").fetch_tools()
    t = tools["opendart__income_statements"]
    # no raw id leaks into the human-facing fields
    assert t["connector_name"] == "OpenDART (KR)"
    assert t["friendly"] == "OpenDART (KR) · Income statements" and "__" not in t["friendly"]


# --- eval-driven fixes ----------------------------------------------------
async def test_intake_guardrail_judges_intent(monkeypatch):
    # PH-THINK: the guardrail lives inside the intake LLM call — it judges INTENT (with a
    # confidence score), so a fact request that NEGATES a restricted term is allowed, while a
    # genuine advice request is refused, and a low-confidence guess never blocks.
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    # genuine buy advice → restricted
    mock_resp.text = '{"restricted": true, "category": "advice", "score": 0.95, "reason": "매수의견", "steps": 3, "plan": ""}'
    intake = await A.analyze_task("엔비디아 지금 사야 할까?", "gemini")
    assert intake.restricted is True and intake.plan is None

    # the reported bug: a FACT request that EXCLUDES targets/forecasts → allowed, with a plan
    mock_resp.text = ('{"restricted": false, "category": "none", "score": 0.05, "reason": "사실 요청", '
                      '"steps": 4, "plan": "야후에서 최근 가격을 조회"}')
    intake = await A.analyze_task("NVDA 최근 가격 흐름만, 목표가·전망은 제시하지 말고", "gemini")
    assert intake.restricted is False and intake.steps == 4 and intake.plan == "야후에서 최근 가격을 조회"

    # a low-confidence restricted guess must NOT block (below the 0.6 threshold)
    mock_resp.text = '{"restricted": true, "category": "forecast", "score": 0.3, "steps": 3}'
    intake = await A.analyze_task("애플 실적 추이 알려줘", "gemini")
    assert intake.restricted is False


async def test_intake_routes_conceptual_vs_data(monkeypatch):
    # The intake routes purely conceptual questions away from the tool loop (needs_data=False),
    # while data questions keep needs_data=True (and default True when the model omits it).
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    mock_resp.text = '{"restricted": false, "score": 0.0, "needs_data": false, "steps": 2, "plan": ""}'
    assert (await A.analyze_task("PER이 뭐야?", "gemini")).needs_data is False

    mock_resp.text = '{"restricted": false, "score": 0.0, "needs_data": true, "steps": 3, "plan": "가격 조회"}'
    assert (await A.analyze_task("AAPL 최근 종가", "gemini")).needs_data is True

    mock_resp.text = '{"restricted": false, "score": 0.0, "steps": 3}'   # omitted → default True
    assert (await A.analyze_task("삼성전자 매출", "gemini")).needs_data is True


async def test_chat_stream_conceptual_skips_tools(monkeypatch):
    # needs_data=False → the chat answers richly without any tool call or data-plane fetch.
    import agentengine.chat as C
    from agentengine.agent import TaskIntake
    from agentengine.chat import stream_chat

    _gw(monkeypatch)

    async def _conceptual(_task, _backend=None, conversation=None):
        return TaskIntake(steps=3, restricted=False, needs_data=False, plan=None)

    monkeypatch.setattr(C, "analyze_task", _conceptual)
    # inject a fake planner so the conceptual synthesis streams a real answer (no GeminiPlanner,
    # which needs the gemini extra + a key).
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fake_planner("rag__search", {"query": "x"}))
    events = [e async for e in stream_chat([{"role": "user", "content": "PER이 뭐야?"}], "vgk_x")]
    assert all(e["type"] != "tool" for e in events)          # no tool call
    assert any(e["type"] == "token" for e in events)         # but a real answer streamed
    sugg = [e for e in events if e["type"] == "suggestions"]
    assert sugg and sugg[0]["items"]                          # chips ALWAYS render (stub → fallback)
    done = events[-1]
    assert done["type"] == "done" and done["refused"] is False and done["citations"] == []


async def test_intake_parses_clarify_options(monkeypatch):
    # CLARIFY: a broad request → clarify=true with ≥2 concrete options (dropped if <2 or restricted).
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    mock_resp.text = ('{"restricted": false, "score": 0.0, "needs_data": true, "clarify": true, '
                      '"clarify_prompt": "무엇을 볼까요?", "multi": true, "steps": 6, '
                      '"options": [{"label": "주가 흐름", "description": "최근 가격"}, '
                      '{"label": "최근 뉴스", "description": "헤드라인"}]}')
    intake = await A.analyze_task("엔비디아 분석해줘", "gemini")
    assert intake.clarify is True and intake.multi is True and len(intake.options) == 2
    assert intake.options[0]["label"] == "주가 흐름" and intake.clarify_prompt == "무엇을 볼까요?"

    # only ONE option → not actionable as a choice → clarify suppressed
    mock_resp.text = ('{"restricted": false, "clarify": true, "steps": 3, '
                      '"options": [{"label": "주가 흐름"}]}')
    assert (await A.analyze_task("엔비디아 분석", "gemini")).clarify is False


async def test_chat_stream_clarify_offers_options(monkeypatch):
    # A clarify intake makes the chat OFFER options (clarify event), call no tool, and finish.
    import agentengine.chat as C
    from agentengine.agent import TaskIntake
    from agentengine.chat import stream_chat

    _gw(monkeypatch)

    async def _clarify(_task, _backend=None, conversation=None):
        return TaskIntake(steps=6, restricted=False, clarify=True, multi=True,
                          clarify_prompt="무엇을 볼까요?",
                          options=[{"label": "주가 흐름"}, {"label": "최근 뉴스"}])

    monkeypatch.setattr(C, "analyze_task", _clarify)
    events = [e async for e in stream_chat([{"role": "user", "content": "엔비디아 분석해줘"}], "vgk_x")]
    assert all(e["type"] != "tool" for e in events)          # nothing executed yet
    clarify = next(e for e in events if e["type"] == "clarify")
    assert clarify["multi"] is True and [o["label"] for o in clarify["options"]] == ["주가 흐름", "최근 뉴스"]
    assert events[-1]["type"] == "done" and events[-1].get("clarify") is True


def test_citations_use_per_hit_rag_provenance():
    # RAG answers must cite each passage's REAL source/url, not the connector's
    # generic label (the eval caught the agent citing "Platform RAG" instead).
    tool = {"name": "rag__search", "connector": "rag", "source": "Platform RAG (filings/news)"}
    result = {"data": {"hits": [
        {"text": "...", "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/aapl"}},
        {"text": "...", "provenance": {"source": "OpenDART (FSS)", "url": "https://dart/x"}},
    ]}}
    cites = A._citations(tool, result)
    assert {c.source for c in cites} == {"SEC EDGAR", "OpenDART (FSS)"}
    assert {c.url for c in cites} == {"https://sec.gov/aapl", "https://dart/x"}
    assert all(c.source != "Platform RAG (filings/news)" for c in cites)


# --- PH-4/U2: enriched citations for type-aware source-preview cards -----------
def test_freshness_buckets_from_as_of():
    from datetime import date

    from agentengine.freshness import compute_freshness

    today = date(2026, 6, 15)
    assert compute_freshness("2026-06-10", today) == "fresh"     # 5d
    assert compute_freshness("2026-05-01", today) == "aging"     # 45d
    assert compute_freshness("2025-01-01", today) == "stale"     # >1y
    assert compute_freshness(None, today) is None                # unknown, not a claim
    assert compute_freshness("not-a-date", today) is None


def test_universal_evidence_text_fragment_links():
    # news + web RAG passages get a #:~:text= deep link so the source opens highlighted;
    # filing passages keep their clean url (they get a PDF screenshot instead).
    assert A.text_fragment_url("https://x/y", "Apple beats on iPhone revenue").startswith(
        "https://x/y#:~:text=Apple%20beats")
    assert A.text_fragment_url("https://x#frag", "phrase") == "https://x#frag"  # don't double-fragment
    assert A.text_fragment_url(None, "p") is None
    # a news citation carries the fragment on its url
    tool = {"name": "google_news__news", "source": "Google News"}
    c = A._citations(tool, {"data": {"news": [
        {"title": "엔비디아 AI 수요 급증", "source": "Reuters", "url": "https://r/a", "date": "2026-06-20"}]}})[0]
    assert c.url.startswith("https://r/a#:~:text=") and c.snippet == "엔비디아 AI 수요 급증"


def test_rag_citation_is_enriched_for_preview_card():
    tool = {"name": "rag__search", "connector": "rag", "source": "Platform RAG"}
    result = {"data": {"hits": [
        {"text": "Apple sources chips from TSMC, a key supplier.",
         "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/aapl", "doc_type": "10-K",
                        "as_of": "2026-06-01", "ticker": "AAPL", "accession": "0000320193-26"}},
        {"text": "Apple beats on iPhone revenue - Reuters",
         "provenance": {"source": "Reuters", "url": "https://r/x", "doc_type": "news", "as_of": "2026-06-12"}},
    ]}}
    filing, news = A._citations(tool, result)
    assert filing.kind == "filing" and filing.doc_type == "10-K" and filing.ticker == "AAPL"
    assert filing.as_of == "2026-06-01" and filing.freshness in {"fresh", "aging", "stale"}
    assert "TSMC" in filing.snippet and filing.page == "0000320193-26"
    assert news.kind == "news" and news.source == "Reuters" and news.snippet.endswith("Reuters")


# --- U3: connector-backed artifacts -------------------------------------------
def test_artifacts_from_prices_timeseries():
    tool = {"name": "yahoo__prices", "source": "Yahoo Finance"}
    # real Price shape: the date is in `time` (no `date` field), value in `close`
    result = {"data": {"ticker": "AAPL", "prices": [
        {"time": "2024-01-02", "close": 185.6}, {"time": "2024-01-03", "close": 184.2}]}}
    arts = A._artifacts(tool, result)
    assert len(arts) == 1
    a = arts[0]
    assert a.kind == "timeseries" and a.ticker == "AAPL" and a.source == "Yahoo Finance" and a.tool == "yahoo__prices"
    assert a.series[0].label == "종가" and len(a.series[0].points) == 2
    assert a.series[0].points[0].x == "2024-01-02" and a.series[0].points[0].y == 185.6
    assert a.as_of == "2024-01-03"


def test_artifacts_from_metrics_history_multi_series():
    tool = {"name": "datasets_store__metrics_history", "source": "ingestion store"}
    result = {"data": {"ticker": "AAPL", "metrics": [
        {"report_period": "2024-09-28", "gross_margin": 0.46, "net_margin": 0.24},
        {"report_period": "2025-09-27", "gross_margin": 0.47, "net_margin": 0.27}]}}
    a = A._artifacts(tool, result)[0]
    labels = {s.label for s in a.series}
    assert a.kind == "timeseries" and {"매출총이익률", "순이익률"} <= labels
    assert a.chart_style is None  # ratios → line, not bar


def test_artifacts_income_statements_render_as_bars():
    # money amounts (매출·순이익) → bar chart; ratios stay line (chart_style differs).
    tool = {"name": "sec_edgar__income_statements", "source": "SEC EDGAR"}
    result = {"data": {"income_statements": [
        {"ticker": "AAPL", "report_period": "2024-09-28", "revenue": 391_000_000_000, "net_income": 93_000_000_000},
        {"ticker": "AAPL", "report_period": "2025-09-27", "revenue": 410_000_000_000, "net_income": 99_000_000_000}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "timeseries" and a.chart_style == "bar"
    assert {s.label for s in a.series} == {"매출", "순이익"}


def test_artifacts_from_guru_trades_table():
    tool = {"name": "datasets_store__guru_trades", "source": "SEC EDGAR 13F"}
    result = {"data": {"guru": {"investor": "Warren Buffett"}, "report_period": "2026-03-31",
                       "filing_date": "2026-05-15", "comparable": True, "trades": [
        {"ticker": "AAPL", "action": "added", "value_usd": 2_000_000_000, "value_change_usd": 500_000_000, "shares_change": 1000},
        {"ticker": "OXY", "action": "exited", "value_usd": 0, "value_change_usd": -300_000_000, "shares_change": -2000}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "Warren Buffett" in a.title
    assert a.table[0] == ["종목", "매매", "보유가치", "가치변동", "주식수 변동"]
    assert a.table[1][1] == "추가" and a.table[1][2] == "$2.00B"
    assert a.table[2][1] == "전량매도" and a.table[2][3] == "-$300.0M"


def test_artifacts_from_guru_common_table():
    tool = {"name": "datasets_store__guru_common", "source": "SEC EDGAR 13F"}
    result = {"data": {"common": [
        {"ticker": "AAPL", "holder_count": 3, "holders": [
            {"investor": "Warren Buffett"}, {"investor": "Bill Ackman"}, {"investor": "Michael Burry"}]}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and a.title == "거장 공통 보유종목"
    assert a.table[1][0] == "AAPL" and a.table[1][1] == "3"
    assert "Warren Buffett" in a.table[1][2]


def test_intake_context_builds_recent_transcript():
    # the intake sees prior turns so a follow-up ('배당률은?') resolves the earlier company.
    convo = [
        {"role": "user", "content": "삼성전자 배당 알려줘"},
        {"role": "assistant", "content": "삼성전자의 최근 배당은 ... 입니다 [1]."},
        {"role": "user", "content": "배당률은 얼마야?"},  # the latest turn → excluded from context
    ]
    ctx = A._intake_context(convo)
    assert "삼성전자 배당 알려줘" in ctx and "사용자:" in ctx and "분석가:" in ctx
    assert "배당률은 얼마야?" not in ctx  # the question itself isn't part of the context block
    # zero or single prior turn → explicit sentinel
    assert A._intake_context([{"role": "user", "content": "hi"}]) == "(no prior turns)"
    assert A._intake_context(None) == "(no prior turns)"


def test_artifacts_none_for_unchartable_result():
    assert A._artifacts({"name": "sec_edgar__filings", "source": "SEC EDGAR"}, {"data": {"filings": []}}) == []


@respx.mock
async def test_run_agent_emits_price_artifact(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "yahoo__prices", {"ticker": "AAPL", "interval": "day", "start_date": "2024-01-02",
                          "end_date": "2024-01-05", "market": "US"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"time": "2024-01-02", "close": 185.6}]},
                                    headers={"x-connector": "yahoo"}))
    res = await A.run_agent("AAPL price chart", "vgk_x")
    assert res.artifacts and res.artifacts[0].kind == "timeseries" and res.artifacts[0].source == "Yahoo Finance"


@respx.mock
async def test_chat_stream_emits_artifact_event(monkeypatch):
    import agentengine.chat as C
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    _fp = _fake_planner("yahoo__prices", {"ticker": "AAPL", "interval": "day",
                                          "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"time": "2024-01-02", "close": 185.6}]},
                                    headers={"x-connector": "yahoo"}))
    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL price chart"}], "vgk_x")]
    art = next((e for e in events if e["type"] == "artifact"), None)
    assert art and art["artifact"]["kind"] == "timeseries"
    assert events[-1]["type"] == "done" and events[-1]["artifacts"]


@respx.mock
async def test_refresh_artifact_refetches(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"time": "2024-02-01", "close": 190.0}]},
                                    headers={"x-connector": "yahoo"}))
    a = await A.refresh_artifact("yahoo__prices",
                                 {"ticker": "AAPL", "interval": "day", "start_date": "2024-01-01", "end_date": "2024-02-01"},
                                 "vgk_x", title="AAPL 종가")
    assert a and a.kind == "timeseries" and a.as_of == "2024-02-01" and a.args["ticker"] == "AAPL"


@respx.mock
async def test_refresh_artifact_unknown_tool_returns_none(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    assert await A.refresh_artifact("nope__x", {}, "vgk_x") is None


def test_datasets_citation_typed_metric_vs_data():
    price = A._citations({"name": "yahoo__prices", "source": "Yahoo Finance"}, {"data": {"prices": []}})
    filings = A._citations({"name": "sec_edgar__filings", "source": "SEC EDGAR"}, {"data": {"x": 1}})
    assert price[0].kind == "metric" and filings[0].kind == "data"


def test_news_citation_uses_publisher_headline_date():
    # /news must cite each article's publisher + headline + date, not "Google News"
    tool = {"name": "google_news__news", "connector": "google_news", "source": "Google News"}
    result = {"data": {"news": [
        {"ticker": "NVDA", "title": "Nvidia chips surge in overnight trading", "source": "Yahoo Finance",
         "date": "2026-06-15", "url": "https://news.google.com/x"},
        {"ticker": "NVDA", "title": "SpaceX growth lifts Nvidia", "source": "Barron's",
         "date": "2026-06-14", "url": "https://news.google.com/y"},
    ]}}
    cites = A._citations(tool, result)
    assert {c.source for c in cites} == {"Yahoo Finance", "Barron's"}  # publisher, not "Google News"
    assert all(c.kind == "news" and c.snippet and c.as_of for c in cites)
    assert cites[0].snippet.startswith("Nvidia chips") and cites[0].freshness == "fresh"


def test_financial_citation_gets_as_of_from_report_period():
    tool = {"name": "opendart__income_statements", "source": "OpenDART (FSS)"}
    result = {"data": {"statements": [
        {"report_period": "2025-12-31", "revenue": 1}, {"report_period": "2026-03-31", "revenue": 2},
    ]}}
    cite = A._citations(tool, result)[0]
    assert cite.as_of == "2026-03-31"  # latest report period becomes the figure's as-of
    assert cite.kind in {"metric", "data"} and cite.freshness is not None


def test_dedup_assigns_one_based_index():
    from agentengine.models import Citation
    out = A.dedup_citations([
        Citation(tool="t1", source="SEC EDGAR", url="https://a"),
        Citation(tool="t2", source="Reuters", url="https://b"),
        Citation(tool="t3", source="SEC EDGAR", url="https://a"),  # dup
    ])
    assert [c.index for c in out] == [1, 2]


@respx.mock
async def test_call_tool_forces_single_market_connector(monkeypatch):
    from agentengine.client import PlatformClient

    monkeypatch.setattr(settings, "gateway_url", "http://gw.test")
    route = respx.route(method="GET", url__regex=r"http://gw\.test/macro/interest-rates/snapshot").mock(
        return_value=httpx.Response(200, json={}, headers={"x-connector": "ecos"})
    )
    ecos = {"name": "ecos__interest_rates_snapshot", "connector": "ecos", "method": "GET",
            "path": "/macro/interest-rates/snapshot", "markets": ["KR"],
            "params": [{"name": "bank"}, {"name": "market"}]}
    # caller omitted market -> the KR-only connector must still route to KR (not FRED/US)
    await PlatformClient("k").call_tool(ecos, {"bank": "BOK"})
    assert "market=KR" in str(route.calls.last.request.url)


@respx.mock
async def test_call_tool_does_not_force_market_for_multi_market_tool(monkeypatch):
    from agentengine.client import PlatformClient

    monkeypatch.setattr(settings, "gateway_url", "http://gw.test")
    route = respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={}, headers={"x-connector": "yahoo"})
    )
    yahoo = {"name": "yahoo__prices", "connector": "yahoo", "method": "GET", "path": "/prices",
             "markets": ["US", "KR"], "params": [{"name": "ticker"}, {"name": "market"}]}
    await PlatformClient("k").call_tool(yahoo, {"ticker": "AAPL"})
    assert "market=" not in str(route.calls.last.request.url)  # multi-market -> caller decides


# --- agent loop -----------------------------------------------------------
@respx.mock
async def test_run_uses_tool_and_cites(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "yahoo__prices", {"ticker": "AAPL", "interval": "day", "start_date": "2024-01-02",
                          "end_date": "2024-01-05", "market": "US"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 185.6}]}, headers={"x-connector": "yahoo"})
    )
    res = await A.run_agent("What is AAPL's price?", "vgk_x")
    assert res.refused is False
    assert res.steps and res.steps[0].tool == "yahoo__prices" and res.steps[0].status == 200
    assert res.citations and res.citations[0].source == "Yahoo Finance"
    # PH-3: the raw tool id must NOT leak into the human-facing answer
    assert "yahoo__prices" not in res.answer and "`" not in res.answer
    assert res.usage["steps"] == 1


# --- PH-4c: inline [n] source anchors -----------------------------------------
def test_anchor_helpers():
    assert A.has_anchors("revenue grew [1].") is True
    assert A.has_anchors("no markers here") is False
    assert A.anchor_markers([1, 2, None, 3]) == "[1][2][3]"  # skips falsy index


def test_number_sources_formats_indexed_block():
    # PH-4e: the numbered block the planner gets so its inline [n] matches the chips
    from agentengine.models import Citation
    block = A.number_sources([
        Citation(tool="t", source="Barron's", snippet="Nvidia and SpaceX", as_of="2026-06-12", index=1),
        Citation(tool="t", source="TipRanks", index=2),
        {"source": "X", "index": None},  # no index -> skipped
    ])
    lines = block.splitlines()
    assert lines[0] == "[1] Barron's · Nvidia and SpaceX · 2026-06-12"
    assert lines[1] == "[2] TipRanks"
    assert len(lines) == 2


@respx.mock
async def test_run_agent_anchors_answer_when_model_omits(monkeypatch):
    # the model's summary carries no [n]; with one citation the answer must end source-anchored
    from agentengine.planner import Decision

    _gw(monkeypatch)
    _catalog()

    class _NoAnchorPlanner:
        async def plan(self, task, tools, history, system=None, conversation=None,
                       force_final=False, sources=None):
            if history or force_final:
                return Decision(final="AAPL was 100.")   # NO [n] anchor → the loop must add one
            return Decision(tool="yahoo__prices", args={"ticker": "AAPL", "interval": "day",
                            "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
        async def plan_batch(self, *a, **k):
            return [await self.plan(*a, **k)]

    monkeypatch.setattr(A, "get_planner", lambda _b=None: _NoAnchorPlanner())
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 185.6}]}, headers={"x-connector": "yahoo"})
    )
    res = await A.run_agent("What is AAPL's price?", "vgk_x")
    assert A.has_anchors(res.answer) and res.answer.rstrip().endswith("[1]")
    assert res.citations[0].index == 1  # anchor matches the citation index


@respx.mock
async def test_chat_stream_emits_trailing_anchor(monkeypatch):
    import agentengine.chat as C
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    _fp = _fake_planner("yahoo__prices", {"ticker": "AAPL", "interval": "day",
                                          "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 185.6}]}, headers={"x-connector": "yahoo"})
    )
    events = [e async for e in stream_chat([{"role": "user", "content": "What is AAPL price?"}], "vgk_x")]
    prose = "".join(e["text"] for e in events if e["type"] == "token")
    assert A.has_anchors(prose) and "[1]" in prose
    cite = next(e for e in events if e["type"] == "citation")
    assert cite["index"] == 1


# --- PH-THINK: the intake (budget + guardrail) clamps the step budget --------------------
async def test_intake_clamps_step_budget(monkeypatch):
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    from agentengine.config import settings
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    mock_resp.text = '{"restricted": false, "score": 0.0, "steps": 11, "plan": "공시 요약"}'
    assert (await A.analyze_task("엔비디아 공급망·리스크 공시 요약", "gemini")).steps == 11
    mock_resp.text = '{"restricted": false, "score": 0.0, "steps": 999}'   # clamped to the cap
    assert (await A.analyze_task("x", "gemini")).steps == settings.max_steps_cap


def test_call_sig_detects_repeat():
    from agentengine.planner import Decision
    a = Decision(tool="sec_edgar__filings", args={"ticker": "NVDA"})
    b = Decision(tool="sec_edgar__filings", args={"ticker": "NVDA"})
    c = Decision(tool="sec_edgar__filings", args={"ticker": "AAPL"})
    assert A.call_sig(a) == A.call_sig(b) and A.call_sig(a) != A.call_sig(c)
    assert A.call_sig(Decision(final="done")) is None


def test_fallback_answer_is_nonempty_and_anchored():
    from agentengine.models import Citation
    out = A.fallback_answer([Citation(tool="t", source="SEC EDGAR", index=1),
                             Citation(tool="t", source="Reuters", index=2)])
    assert out and "Reached the step limit" not in out
    assert "SEC EDGAR" in out and A.has_anchors(out)
    assert A.fallback_answer([])  # still non-empty with no citations


@respx.mock
async def test_run_agent_recovers_from_stuck_planner(monkeypatch):
    # a planner that never finalizes (keeps proposing the same tool) must NOT leak
    # "Reached the step limit." — the repeat-guard + fallback yield a real answer.
    from agentengine.planner import Decision

    _gw(monkeypatch)
    _catalog()
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 1}]}, headers={"x-connector": "yahoo"})
    )

    class StuckPlanner:
        async def plan(self, task, tools, history, system=None, conversation=None,
                       force_final=False, sources=None):
            if force_final:
                return Decision(final="")  # empty even when forced → exercises the fallback
            return Decision(tool="yahoo__prices", args={"ticker": "AAPL"})

    monkeypatch.setattr(A, "get_planner", lambda _b=None: StuckPlanner())
    res = await A.run_agent("AAPL price please", "vgk_x")
    assert res.answer and "Reached the step limit" not in res.answer
    assert A.has_anchors(res.answer)              # fallback is source-anchored
    assert len(res.steps) <= 2                    # repeat-guard stopped the loop early


@respx.mock
async def test_run_refuses_forecast(monkeypatch):
    # The intake (LLM) makes the call; stub it as restricted to assert run_agent refuses at the
    # boundary (no tool steps), without touching the data plane.
    _gw(monkeypatch)

    async def _restricted(_task, _backend=None, conversation=None):
        return A.TaskIntake(steps=3, restricted=True, score=0.95, reason="forecast")

    monkeypatch.setattr(A, "analyze_task", _restricted)
    res = await A.run_agent("Will AAPL go up next week?", "vgk_x")
    assert res.refused is True and res.steps == []


async def test_intake_parses_subtasks(monkeypatch):
    # A2A: a complex request → 2-4 focused subtasks; dropped if <2 or restricted/clarify.
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    mock_resp.text = ('{"restricted": false, "score": 0.0, "needs_data": true, "steps": 10, '
                      '"subtasks": [{"title": "주가", "question": "NVDA 최근 주가 흐름"}, '
                      '{"title": "재무", "question": "NVDA 최근 매출·순이익"}]}')
    intake = await A.analyze_task("엔비디아 종합 분석해줘", "gemini")
    assert len(intake.subtasks) == 2 and intake.subtasks[0]["title"] == "주가"

    # a single subtask isn't a decomposition → dropped
    mock_resp.text = '{"restricted": false, "needs_data": true, "steps": 3, "subtasks": [{"title": "x", "question": "y"}]}'
    assert (await A.analyze_task("NVDA 주가", "gemini")).subtasks == []


@respx.mock
async def test_run_subagent_gathers_evidence(monkeypatch):
    # A sub-agent runs a headless gather loop over the shared tools and returns evidence.
    import agentengine.orchestrator as O
    from agentengine.planner import Decision

    _gw(monkeypatch)
    _catalog()
    respx.route(method="POST", url__regex=r"http://gw\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"text": "...", "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/x"}}]}, headers={"x-connector": "rag"}))

    class OnePlanner:
        def __init__(self):
            self.n = 0

        async def plan_batch(self, task, tools, history, system=None, conversation=None,
                             force_final=False, sources=None):
            if force_final or self.n >= 1:
                return [Decision(final="공시 리스크 정리")]
            self.n += 1
            return [Decision(tool="rag__search", args={"query": "supplier risk"})]

        async def plan(self, *a, **k):
            return (await self.plan_batch(*a, **k))[0]

    monkeypatch.setattr(O, "get_planner", lambda _b=None: OnePlanner())
    from agentengine.client import PlatformClient
    tools = await PlatformClient("vgk_x").fetch_tools()
    res = await O.run_subagent("리스크", "공시상 공급망 리스크", "vgk_x", tools, "gemini", budget=3)
    assert res.title == "리스크" and res.note == "공시 리스크 정리"
    assert any(c.url == "https://sec.gov/x" for c in res.citations) and res.steps >= 1


@respx.mock
async def test_chat_stream_a2a_decomposes_and_combines(monkeypatch):
    # A2A end-to-end: subtasks → parallel sub-agent cards → unified citations → combined answer.
    import agentengine.chat as C
    import agentengine.orchestrator as O
    from agentengine.agent import TaskIntake
    from agentengine.planner import Decision
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "NVDA", "prices": [{"close": 1}]}, headers={"x-connector": "yahoo"}))
    respx.route(method="POST", url__regex=r"http://gw\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"text": "...", "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/x"}}]}, headers={"x-connector": "rag"}))

    async def _intake(_task, _backend=None, conversation=None):
        return TaskIntake(steps=10, needs_data=True, subtasks=[
            {"title": "주가", "question": "NVDA 최근 주가"},
            {"title": "리스크", "question": "NVDA 공시 리스크"}])

    monkeypatch.setattr(C, "analyze_task", _intake)

    # sub-agents: facet 0 hits prices, facet 1 hits rag; both stop after one round
    def _planner_for(tool, arg):
        class P:
            def __init__(self): self.n = 0
            async def plan_batch(self, task, tools, history, system=None, conversation=None, force_final=False, sources=None):
                if force_final or self.n >= 1:
                    return [Decision(final="요약")]
                self.n += 1
                return [Decision(tool=tool, args=arg)]
            async def plan(self, *a, **k): return (await self.plan_batch(*a, **k))[0]
        return P()

    seq = iter([_planner_for("yahoo__prices", {"ticker": "NVDA"}),
                _planner_for("rag__search", {"query": "risk"})])
    monkeypatch.setattr(O, "get_planner", lambda _b=None: next(seq))
    # the COMBINER planner (used by chat.get_planner) writes the final answer
    class Combiner:
        async def plan(self, task, tools, history, system=None, conversation=None, force_final=False, sources=None):
            return Decision(final="엔비디아 종합: 주가와 리스크를 함께 봤어요 [1]")
        async def plan_batch(self, *a, **k): return [await self.plan(*a, **k)]
    monkeypatch.setattr(C, "get_planner", lambda _b=None: Combiner())

    events = [e async for e in stream_chat([{"role": "user", "content": "엔비디아 종합 분석해줘"}], "vgk_x")]
    cards = [e for e in events if e["type"] == "subagent"]
    assert {e["title"] for e in cards} == {"주가", "리스크"}
    assert any(e["status"] == "running" for e in cards) and any(e["status"] == "done" for e in cards)
    done = events[-1]
    assert done["type"] == "done" and "SEC EDGAR" in {c.get("source") for c in done["citations"]}
    prose = "".join(e["text"] for e in events if e["type"] == "token")
    assert "종합" in prose


def test_chunks_preserve_newlines_and_markdown():
    # the markdown-rendering bug: word-splitting collapsed newlines → headings/lists broke.
    from agentengine.chat import _chunks
    md = "## 제목\n\n- a\n- b\n\n본문 [1]"
    assert "".join(_chunks(md, 5)) == md   # char-chunking round-trips EXACTLY (newlines kept)


@respx.mock
async def test_chat_stream_real_token_streaming(monkeypatch):
    # the answer must STREAM token-by-token (planner.stream_final), preserving markdown newlines.
    import agentengine.chat as C
    from agentengine.agent import TaskIntake
    from agentengine.planner import Decision
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 1}]}, headers={"x-connector": "yahoo"}))

    async def _intake(_t, _b=None, conversation=None):
        return TaskIntake(steps=2, needs_data=True)

    async def _no_refine(*a, **k):
        return (None, {})

    class StreamPlanner:
        def __init__(self):
            self.n = 0

        async def plan_batch(self, task, tools, history, system=None, conversation=None, force_final=False, sources=None):
            if force_final or self.n >= 1:
                return [Decision(final="(unused)")]
            self.n += 1
            return [Decision(tool="yahoo__prices", args={"ticker": "AAPL"})]

        async def plan(self, *a, **k):
            return (await self.plan_batch(*a, **k))[0]

        async def stream_final(self, task, tools, history, system=None, conversation=None, sources=None):
            for piece in ["## 제목\n\n", "첫 문장. ", "둘째 [1]"]:
                yield piece

    monkeypatch.setattr(C, "analyze_task", _intake)
    monkeypatch.setattr(C, "refine_evidence", _no_refine)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: StreamPlanner())
    monkeypatch.setattr(C.settings, "llm_backend", "gemini")  # take the real-streaming path

    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL 주가"}], "vgk_x")]
    toks = [e["text"] for e in events if e["type"] == "token"]
    assert "## 제목\n\n" in toks and len(toks) >= 3   # streamed per-delta, not one blob
    prose = "".join(toks)
    assert "## 제목" in prose and "\n\n" in prose      # markdown newlines survive


@respx.mock
async def test_chat_stream_runs_batch_in_parallel(monkeypatch):
    # PH-THINK: when the planner returns multiple independent calls (plan_batch), the chat
    # announces ALL of them, then fetches them CONCURRENTLY in one step (all `tool` events
    # precede the first `tool_result`), collecting every source.
    import agentengine.chat as C
    from agentengine.planner import Decision
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 1}]}, headers={"x-connector": "yahoo"}))
    respx.route(method="POST", url__regex=r"http://gw\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"text": "...", "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/x"}}]}, headers={"x-connector": "rag"}))

    class BatchPlanner:
        def __init__(self):
            self.rounds = 0

        async def plan_batch(self, task, tools, history, system=None, conversation=None,
                             force_final=False, sources=None):
            if force_final or self.rounds >= 1:
                return [Decision(final="주가와 공시를 함께 확인했어요 [1]")]
            self.rounds += 1
            return [Decision(tool="yahoo__prices", args={"ticker": "AAPL"}),
                    Decision(tool="rag__search", args={"query": "supplier risk"})]

        async def plan(self, *a, **k):
            return (await self.plan_batch(*a, **k))[0]

    monkeypatch.setattr(C, "get_planner", lambda _b=None: BatchPlanner())
    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL 주가랑 공시 같이 봐줘"}], "vgk_x")]

    names = [e["name"] for e in events if e["type"] == "tool"]
    assert set(names) == {"yahoo__prices", "rag__search"}      # both calls fanned out
    types_seq = [e["type"] for e in events]
    last_tool = max(i for i, t in enumerate(types_seq) if t == "tool")
    first_result = min(i for i, t in enumerate(types_seq) if t == "tool_result")
    assert last_tool < first_result                            # announced together, then gathered
    done = events[-1]
    assert done["type"] == "done" and "SEC EDGAR" in {c.get("source") for c in done["citations"]}


@respx.mock
async def test_run_routes_to_rag(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "rag__search", {"query": "Apple supplier risk"}))
    respx.route(method="POST", url__regex=r"http://gw\.test/rag/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"text": "...", "provenance": {"source": "SEC EDGAR", "url": "https://sec.gov/x"}}]}, headers={"x-connector": "rag"})
    )
    res = await A.run_agent("What does Apple disclose about supplier risk?", "vgk_x")
    assert res.steps[0].tool == "rag__search" and res.steps[0].status == 200
    assert any(c.url == "https://sec.gov/x" for c in res.citations)


@respx.mock
async def test_run_respects_allowed_tools(monkeypatch):
    from agentengine.models import AgentSpec

    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "sec_edgar__company_facts", {"ticker": "AAPL", "market": "US"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/company/facts").mock(
        return_value=httpx.Response(200, json={"company_facts": {"ticker": "AAPL"}}, headers={"x-connector": "sec_edgar"})
    )
    # only company_facts allowed -> the planner only sees that tool
    res = await A.run_agent("Tell me about AAPL price", "vgk_x", AgentSpec(allowed_tools=["sec_edgar__company_facts"]))
    assert res.steps[0].tool == "sec_edgar__company_facts"


# --- endpoints ------------------------------------------------------------
@respx.mock
def test_endpoint_run(monkeypatch):
    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "yahoo__prices", {"ticker": "AAPL", "interval": "day", "start_date": "2024-01-02",
                          "end_date": "2024-01-05", "market": "US"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": []}, headers={"x-connector": "yahoo"})
    )
    r = client.post("/agent/run", json={"task": "AAPL price?"}, headers={"X-API-KEY": "vgk_x"}).json()
    assert r["steps"][0]["tool"] == "yahoo__prices" and r["citations"]


def test_info_and_compile():
    assert client.get("/agent/info").json()["llm_backend"] == "gemini"
    spec = client.post("/agent/compile", json={"description": "Summarize a ticker's filings"}).json()
    assert spec["system"] == "Summarize a ticker's filings"


# --- streaming chat -------------------------------------------------------
@respx.mock
async def test_chat_stream_uses_tool_and_cites(monkeypatch):
    import agentengine.chat as C
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    _catalog()
    _fp = _fake_planner("yahoo__prices", {"ticker": "AAPL", "interval": "day",
                                          "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"close": 185.6}]}, headers={"x-connector": "yahoo"})
    )
    events = [e async for e in stream_chat([{"role": "user", "content": "What is AAPL price?"}], "vgk_x")]
    types = [e["type"] for e in events]
    assert "tool" in types and "tool_result" in types and "citation" in types
    assert any(e["type"] == "token" for e in events)
    assert events[-1]["type"] == "done" and events[-1]["refused"] is False
    tool_ev = next(e for e in events if e["type"] == "tool")
    assert tool_ev["name"] == "yahoo__prices"
    cite = next(e for e in events if e["type"] == "citation")
    assert cite["source"] == "Yahoo Finance"


@respx.mock
async def test_chat_stream_guardrail_refuses(monkeypatch):
    # The intake (LLM) decides restriction; here we stub it as restricted to assert the chat
    # boundary refuses cleanly (streams the refusal, never calls a tool, marks refused).
    import agentengine.chat as C
    from agentengine.agent import TaskIntake
    from agentengine.chat import stream_chat

    _gw(monkeypatch)

    async def _restricted(_task, _backend=None, conversation=None):
        return TaskIntake(steps=3, restricted=True, score=0.95, reason="advice")

    monkeypatch.setattr(C, "analyze_task", _restricted)
    events = [e async for e in stream_chat([{"role": "user", "content": "should I buy AAPL?"}], "vgk_x")]
    assert all(e["type"] != "tool" for e in events)
    assert events[-1]["type"] == "done" and events[-1]["refused"] is True
    assert any(e["type"] == "token" for e in events)


@respx.mock
async def test_chat_stream_platform_unavailable(monkeypatch):
    import agentengine.chat as C
    from agentengine.chat import stream_chat

    _gw(monkeypatch)
    # a planner is constructed before the catalog fetch; inject a fake one so we don't build
    # GeminiPlanner (which needs the gemini extra + a key) in this keyless suite.
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fake_planner("rag__search", {"query": "x"}))
    respx.get("http://gw.test/catalog").mock(return_value=httpx.Response(503))
    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL price?"}], "vgk_x")]
    assert all(e["type"] != "tool" for e in events)  # no tool could be called
    assert any(e["type"] == "token" for e in events)  # but the user still gets a message
    assert events[-1] == {"type": "done", "citations": [], "artifacts": [], "refused": False}


@respx.mock
async def test_chat_stream_respects_allowed_tools(monkeypatch):
    import agentengine.chat as C
    from agentengine.chat import stream_chat
    from agentengine.models import AgentSpec

    _gw(monkeypatch)
    _catalog()
    # the fake planner asserts it only ever sees the allowed tool (the price tool is filtered out
    # of `tools` before the planner is asked to route).
    from agentengine.planner import Decision

    class _AllowedOnly:
        async def plan(self, task, tools, history, system=None, conversation=None,
                       force_final=False, sources=None):
            assert set(tools) == {"sec_edgar__company_facts"}  # price tool never offered
            if history or force_final:
                return Decision(final="AAPL [1].")
            return Decision(tool="sec_edgar__company_facts", args={"ticker": "AAPL", "market": "US"})
        async def plan_batch(self, *a, **k):
            return [await self.plan(*a, **k)]
        async def stream_final(self, *a, **k):
            yield "AAPL [1]."

    _fp = _AllowedOnly()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    respx.route(method="GET", url__regex=r"http://gw\.test/company/facts").mock(
        return_value=httpx.Response(200, json={"company_facts": {}}, headers={"x-connector": "sec_edgar"})
    )
    spec = AgentSpec(allowed_tools=["sec_edgar__company_facts"])
    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL price?"}], "vgk_x", spec)]
    tool_ev = next(e for e in events if e["type"] == "tool")
    assert tool_ev["name"] == "sec_edgar__company_facts"  # price tool filtered out


# --- stub planner robustness (no bad tool calls) --------------------------
def test_resolve_ticker_names_codes_and_acronyms():
    from agentengine.planner import resolve_ticker

    assert resolve_ticker("삼성전자 최근 실적") == "005930"   # KR name -> code
    assert resolve_ticker("엔비디아 공급망") == "NVDA"          # KR alias for a US name
    assert resolve_ticker("AAPL 최근 주가") == "AAPL"           # explicit symbol
    assert resolve_ticker("005930 공시") == "005930"            # explicit KR code
    assert resolve_ticker("what is EPS and PER?") is None       # acronyms are not tickers
    assert resolve_ticker("artificial intelligence trends") is None  # 'intel' must not fire


def test_chat_chunks_reconstruct_text():
    from agentengine.chat import _chunks

    assert "".join(_chunks("the quick brown fox jumps over the lazy dog", size=3)) == "the quick brown fox jumps over the lazy dog"
    assert _chunks("") == [""]  # empty stays a single chunk, never crashes


# --- F1: agent spec (connector filter / backend / system) -----------------
def test_filter_tools_by_connector_or_tool_name():
    tools = {
        "yahoo__prices": {"category": "market"},
        "sec_edgar__company_facts": {"category": "fundamentals"},
        "sec_edgar__filings": {"category": "filings"},
        "rag__search": {"category": "filings"},
    }
    # full tool name selects exactly one (the new per-tool model)
    assert set(A.filter_tools(tools, ["yahoo__prices"])) == {"yahoo__prices"}
    # category id selects every tool in that category, across connectors
    assert set(A.filter_tools(tools, ["filings"])) == {"sec_edgar__filings", "rag__search"}
    # connector id still selects all of its tools (legacy/back-compat)
    assert set(A.filter_tools(tools, ["sec_edgar"])) == {"sec_edgar__company_facts", "sec_edgar__filings"}
    # entries can mix granularity (tool name + category)
    assert set(A.filter_tools(tools, ["yahoo__prices", "filings"])) == {
        "yahoo__prices", "sec_edgar__filings", "rag__search"}
    # empty/None = no restriction
    assert A.filter_tools(tools, None) == tools
    assert A.filter_tools(tools, []) == tools


@respx.mock
async def test_run_with_data_source_subset_restricts_to_connector(monkeypatch):
    from agentengine.models import AgentSpec

    _gw(monkeypatch)
    _catalog()
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fake_planner(
        "sec_edgar__company_facts", {"ticker": "AAPL", "market": "US"}))
    respx.route(method="GET", url__regex=r"http://gw\.test/company/facts").mock(
        return_value=httpx.Response(200, json={"company_facts": {"ticker": "AAPL"}}, headers={"x-connector": "sec_edgar"})
    )
    # a price question, but the agent's data sources are SEC only -> never calls yahoo
    res = await A.run_agent("AAPL price?", "vgk_x", AgentSpec(allowed_tools=["sec_edgar"]))
    assert res.steps and res.steps[0].tool.startswith("sec_edgar__")
    assert all(not s.tool.startswith("yahoo__") for s in res.steps)


def test_chat_endpoint_sse(monkeypatch):
    import respx as _respx
    import agentengine.chat as C

    _fp = _fake_planner("yahoo__prices", {"ticker": "AAPL", "interval": "day",
                                          "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    with _respx.mock:
        _gw(monkeypatch)
        _catalog()
        _respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
            return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": []}, headers={"x-connector": "yahoo"})
        )
        r = client.post("/agent/chat", json={"messages": [{"role": "user", "content": "AAPL price?"}]}, headers={"X-API-KEY": "vgk_x"})
        assert r.status_code == 200 and "text/event-stream" in r.headers["content-type"]
        assert '"type": "tool"' in r.text and '"type": "done"' in r.text


def test_to_gemini_contents_mapping():
    pytest.importorskip("google.genai")  # needs the `gemini` extra; skip on the dep-free dev run
    from agentengine.planner import _to_gemini_contents, Decision
    from google.genai import types

    conversation = [
        {"role": "user", "content": "What is AAPL price?"},
        {"role": "assistant", "content": "Let me look up the price."}
    ]
    history = [
        (Decision(tool="yahoo__prices", args={"ticker": "AAPL"}, thought_signature=b"sig_data"), {"status": 200, "data": {"close": 180.5}})
    ]
    task = "Is it higher than last week?"

    contents = _to_gemini_contents(conversation, history, task)
    assert len(contents) == 4
    # Turn 0: User message "What is AAPL price?"
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "What is AAPL price?"
    # Turn 1: Assistant message "Let me look up the price."
    assert contents[1].role == "model"
    assert contents[1].parts[0].text == "Let me look up the price."
    # Turn 2: Model function call + thought signature
    assert contents[2].role == "model"
    assert contents[2].parts[0].function_call.name == "yahoo__prices"
    assert contents[2].parts[0].function_call.args == {"ticker": "AAPL"}
    assert contents[2].parts[0].thought_signature == b"sig_data"
    # Turn 3: Tool response
    assert contents[3].role == "tool"
    assert contents[3].parts[0].function_response.name == "yahoo__prices"
    assert contents[3].parts[0].function_response.response == {"close": 180.5}


def test_to_gemini_contents_replays_raw_for_parallel_calls():
    # regression: parallel function calls must replay the model's RAW content verbatim (carries
    # every part's thought_signature) ONCE, then one function_response per call — reconstructing
    # them part-by-part dropped a signature → Gemini 400 (missing thought_signature).
    pytest.importorskip("google.genai")
    from agentengine.planner import _to_gemini_contents, Decision

    raw = object()  # sentinel for the shared model Content (replayed by identity)
    d1 = Decision(tool="yahoo__prices", args={"ticker": "AAPL"}, raw_content=raw)
    d2 = Decision(tool="google_news__news", args={"ticker": "AAPL"}, raw_content=raw)
    history = [(d1, {"data": {"x": 1}}), (d2, {"data": {"y": 2}})]

    contents = _to_gemini_contents(None, history, "AAPL 주가")
    assert contents.count(raw) == 1                       # the batch's model turn emitted exactly once
    tool_turns = [c for c in contents if getattr(c, "role", None) == "tool"]
    assert len(tool_turns) == 2                           # one function_response per parallel call
    assert {t.parts[0].function_response.name for t in tool_turns} == {"yahoo__prices", "google_news__news"}


def test_schema_maps_param_descriptions():
    from agentengine.planner import _schema

    tool = {
        "name": "test_tool",
        "params": [
            {"name": "ticker", "required": True, "description": "Stock symbol (e.g. AAPL)"},
            {"name": "interval", "enum": ["day", "week"]}
        ]
    }
    schema = _schema(tool)
    assert schema["properties"]["ticker"]["description"] == "Stock symbol (e.g. AAPL)"
    assert "description" not in schema["properties"]["interval"]


def test_economic_indicator_citation_data_card():
    # PH-DATA-4: economic indicator (DBnomics) → data-card evidence (values + source).
    tool = {"name": "fred__economic_indicators", "source": "DBnomics", "connector": "fred"}
    data = {"slug": "cpi", "name": "US CPI", "unit": "index", "source": "DBnomics",
            "source_url": "https://db.nomics.world/BLS/cu/CUSR0000SA0",
            "observations": [{"date": "2025-11", "value": 318.0}, {"date": "2025-12", "value": 319.1}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.table and c.table[0] == ["기간", "US CPI"] and c.table[1][0] == "2025-12"  # newest first


def test_technical_indicator_citation_data_card():
    # PH-DATA-6: technical indicators (computed from Yahoo) → descriptive data-card, not a signal.
    tool = {"name": "yahoo__technical_indicators", "connector": "yahoo",
            "source": "Technical indicators (computed from Yahoo Finance)"}
    data = {"ticker": "AAPL", "market": "US", "as_of": "2025-06-01", "note": "Descriptive…",
            "source": "Technical indicators (computed from Yahoo Finance)",
            "indicators": [
                {"key": "sma_20", "name": "SMA(20)", "pane": "price", "unit": "price",
                 "lines": [{"label": "SMA(20)", "latest": 195.5, "points": [{"date": "2025-06-01", "value": 195.5}]}]},
                {"key": "rsi_14", "name": "RSI(14)", "pane": "sub", "unit": "ratio_0_100",
                 "lines": [{"label": "RSI(14)", "latest": 62.3, "points": [{"date": "2025-06-01", "value": 62.3}]}]}]}
    c = A._citations(tool, {"data": data})[0]
    assert c.table and c.table[0] == ["지표", "최신값"]
    assert any(row[0] == "SMA(20)" for row in c.table) and any(row[0] == "RSI(14)" for row in c.table)
    assert "서술적" in (c.snippet or "")  # labeled descriptive, never a trading signal


def test_artifacts_prices_with_ohlc_become_candlestick():
    # PH-VIZ-1: real OHLCV → candlestick artifact (+ a close line kept for the table view).
    tool = {"name": "yahoo__prices", "source": "Yahoo Finance"}
    result = {"data": {"ticker": "AAPL", "prices": [
        {"time": "2024-01-02", "open": 184.0, "high": 186.0, "low": 183.0, "close": 185.6, "volume": 1000},
        {"time": "2024-01-03", "open": 185.0, "high": 185.5, "low": 183.0, "close": 184.2, "volume": 1200}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "candlestick" and len(a.candles) == 2
    assert a.candles[0].open == 184.0 and a.candles[0].volume == 1000
    assert a.series[0].label == "종가" and a.as_of == "2024-01-03"


def test_chart_markers_and_pricelines_from_turn_events():
    # PH-VIZ-2: a price chart gets sourced event markers (this turn's corporate actions /
    # earnings) + descriptive period high/low lines from its own candles.
    from agentengine.artifacts import enrich_chart_markers
    from agentengine.models import Artifact, ArtifactCandle

    a = Artifact(kind="candlestick", title="AAPL 주가", ticker="AAPL", candles=[
        ArtifactCandle(time="2024-01-02", open=180, high=190, low=175, close=185),
        ArtifactCandle(time="2024-03-01", open=185, high=200, low=170, close=195)])

    class _D:
        tool = "yahoo__corporate_actions"

    class _E:
        tool = "sec_edgar__earnings"
    history = [
        (_D(), {"data": {"ticker": "AAPL", "dividends": [{"ex_date": "2024-02-09", "amount": 0.24}],
                         "splits": [{"date": "2024-06-10", "ratio": "4:1"}]}}),
        (_E(), {"data": {"ticker": "AAPL", "market": "US",
                         "earnings": [{"filing_date": "2024-02-01", "filing_url": "https://sec.gov/x"}]}}),
    ]
    enrich_chart_markers([a], history)
    by_kind = {m.kind: m for m in a.markers}
    assert {"dividend", "split", "earnings"} <= set(by_kind)
    assert by_kind["dividend"].time == "2024-02-09" and by_kind["dividend"].source == "Yahoo Finance"
    assert "0.24" in (by_kind["dividend"].snippet or "")
    assert by_kind["earnings"].source == "SEC EDGAR" and by_kind["earnings"].url == "https://sec.gov/x"
    assert {pl.price for pl in a.pricelines} == {200.0, 170.0}  # period high/low from candles


def test_chart_markers_skip_other_ticker():
    from agentengine.artifacts import enrich_chart_markers
    from agentengine.models import Artifact, ArtifactCandle

    a = Artifact(kind="candlestick", title="AAPL 주가", ticker="AAPL",
                 candles=[ArtifactCandle(time="2024-01-02", open=1, high=2, low=1, close=1.5)])

    class _D:
        tool = "yahoo__corporate_actions"
    history = [(_D(), {"data": {"ticker": "MSFT", "dividends": [{"ex_date": "2024-02-09", "amount": 0.75}]}})]
    enrich_chart_markers([a], history)
    assert a.markers == []   # MSFT events never bleed onto the AAPL chart


def _tech_result(ticker="AAPL"):
    # the shape datasets' /technical-indicators returns (PH-DATA-6).
    return {"data": {"ticker": ticker, "market": "US", "interval": "day",
                     "source": "Technical indicators (computed from Yahoo Finance)", "as_of": "2024-01-03",
                     "indicators": [
                         {"key": "sma_20", "name": "SMA(20)", "pane": "price", "unit": "price",
                          "lines": [{"label": "SMA(20)", "points": [
                              {"date": "2024-01-02", "value": 184.1}, {"date": "2024-01-03", "value": 184.6}]}]},
                         {"key": "rsi_14", "name": "RSI(14)", "pane": "sub", "unit": "ratio_0_100",
                          "lines": [{"label": "RSI(14)", "points": [
                              {"date": "2024-01-02", "value": 55.0}, {"date": "2024-01-03", "value": 48.0}]}]},
                     ]}}


def test_artifacts_asset_classes_table():
    # CE-1: cross-asset snapshot → a sourced table card.
    tool = {"name": "yahoo__asset_classes", "source": "Yahoo Finance"}
    result = {"data": {"groups": [{"name": "주가지수", "members": [
        {"label": "S&P 500", "ticker": "^GSPC", "price": 5000.0, "change_percent": 0.5}]}],
        "source": "Yahoo Finance", "as_of": "2024-01-02"}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and a.table[0] == ["자산군", "종목", "현재가", "등락%"]
    assert a.table[1][1] == "S&P 500" and "5,000.00" in a.table[1][2] and "+0.50%" in a.table[1][3]


def test_artifacts_commodities_table():
    # commodity panel → a sourced grouped table (분류·종목·현재가·등락%).
    tool = {"name": "yahoo__commodities", "source": "Yahoo Finance"}
    result = {"data": {"groups": [{"name": "귀금속", "members": [
        {"label": "금", "ticker": "GC=F", "price": 2000.0, "change_percent": 0.8}]}],
        "source": "Yahoo Finance", "as_of": "2024-01-02"}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and a.title == "원자재 시세" and a.table[0][0] == "분류"
    assert a.table[1][1] == "금" and a.table[1][3] == "+0.80%"


def test_artifacts_themes_table():
    # thematic panel → grouped sourced table (분류·종목·현재가·등락%).
    tool = {"name": "yahoo__themes", "source": "Yahoo Finance"}
    result = {"data": {"groups": [{"name": "테크·AI", "members": [
        {"label": "반도체", "ticker": "SOXX", "price": 655.0, "change_percent": 2.4}]}],
        "source": "Yahoo Finance"}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and a.title == "테마·섹터 시세" and a.table[1][1] == "반도체"


def test_artifacts_semiconductor_proxy_table():
    # DRAM-spot proxy panel → grouped table, labelled NOT a spot price (in the title).
    tool = {"name": "yahoo__semiconductor", "source": "Yahoo Finance"}
    result = {"data": {"groups": [{"name": "지수", "members": [
        {"label": "필라델피아 반도체지수(SOX)", "ticker": "^SOX", "price": 14634.7, "change_percent": 2.04}]}],
        "source": "Yahoo Finance"}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "DRAM 현물가 아님" in a.title and a.table[1][3] == "+2.04%"


def test_artifacts_kis_volume_rank_and_flow_tables():
    # CE-12: KR volume ranking + investor flow → sourced tables.
    vr = A._artifacts({"name": "kis__volume_rank", "source": "KIS"},
                      {"data": {"source": "한국투자증권 (KIS)", "results": [
                          {"rank": 1, "ticker": "005930", "name": "삼성전자", "price": 337250,
                           "change_percent": -4.6, "value": 4_160_000_000_000}]}})[0]
    assert vr.kind == "table" and "거래량 순위" in vr.title
    assert vr.table[1][3] == "-4.60%" and "조" in vr.table[1][4]
    fl = A._artifacts({"name": "kis__investor_flow", "source": "KIS"},
                      {"data": {"ticker": "005930", "flows": [
                          {"date": "20260622", "close": 353500, "individual_net": -100,
                           "foreign_net": 5000, "institution_net": -2000}]}})[0]
    assert fl.kind == "table" and "수급" in fl.title and fl.table[1][3] == "+5,000"
    # fluctuation ranking (losers) + ETF NAV
    fr = A._artifacts({"name": "kis__fluctuation_rank", "source": "KIS"},
                      {"data": {"direction": "down", "results": [
                          {"rank": 1, "ticker": "000660", "name": "SK하이닉스", "price": 100000,
                           "change_percent": -9.9, "volume": 555}]}})[0]
    assert fr.kind == "table" and "하락률 순위" in fr.title and fr.table[1][3] == "-9.90%"
    etf = A._artifacts({"name": "kis__etf_nav", "source": "KIS"},
                       {"data": {"ticker": "069500", "name": "KODEX 200", "price": 141675,
                                 "nav": 141792.70, "premium_discount_pct": -0.06,
                                 "price_change_percent": -4.5, "nav_change_percent": -4.4}})[0]
    assert etf.kind == "table" and "ETF NAV" in etf.title and etf.table[3] == ["괴리율", "-0.06%"]
    mc = A._artifacts({"name": "kis__market_cap_rank", "source": "KIS"},
                      {"data": {"results": [{"rank": 1, "ticker": "005930", "name": "삼성전자",
                                             "market_cap_eok": 19526571, "market_weight_pct": 24.03,
                                             "change_percent": -5.52}]}})[0]
    assert mc.kind == "table" and "시가총액 순위" in mc.title and mc.table[1][2] == "1,952.7조"


def test_artifacts_fmp_estimates_and_calendar_tables():
    # CE-11: consensus estimates + earnings calendar → sourced tables (third-party labelled).
    est = A._artifacts({"name": "fmp__consensus_estimates", "source": "FMP"},
                       {"data": {"symbol": "AAPL", "source": "FMP (애널리스트 컨센서스 · 제3자)", "estimates": [
                           {"date": "2026-09-27", "revenue_avg": 4.5e11, "eps_avg": 7.2, "net_income_avg": 1.1e11}]}})[0]
    assert est.kind == "table" and "컨센서스" in est.title and est.table[1][1] == "450.0B"
    cal = A._artifacts({"name": "fmp__earnings_calendar", "source": "FMP"},
                       {"data": {"symbol": "AAPL", "events": [
                           {"date": "2026-04-30", "eps_estimated": 1.95, "eps_actual": 2.01,
                            "eps_surprise": 0.06, "revenue_actual": 1.11e11}]}})[0]
    assert cal.kind == "table" and "실적 캘린더" in cal.title and cal.table[1][3] == "+0.06"


def test_artifacts_news_digest_table():
    # CE-10: recent news → a sourced, pinnable digest table.
    tool = {"name": "google_news__news", "source": "Google News"}
    result = {"data": {"news": [
        {"title": "엔비디아, AI 데이터센터 수요 급증", "source": "Reuters", "date": "2026-06-20", "ticker": "NVDA"},
        {"title": "반도체 업황 회복 신호", "source": "연합뉴스", "date": "2026-06-19", "ticker": "NVDA"}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "뉴스 다이제스트" in a.title and a.ticker == "NVDA"
    assert a.table[0] == ["헤드라인", "발행사", "날짜"]
    assert a.table[1][1] == "Reuters" and a.table[1][2] == "2026-06-20"


def test_artifacts_macro_panel_table():
    # CE-9: 국가경제 패널 → a sourced table (지표·최신·변화·그룹).
    tool = {"name": "fred__macro_panel", "source": "DBnomics"}
    result = {"data": {"region": "US", "source": "DBnomics", "indicators": [
        {"slug": "cpi", "name": "US CPI", "unit": "index", "group": "물가", "latest": 314.5, "change": 0.8, "as_of": "2025-09"},
        {"slug": "unemployment", "name": "US Unemployment", "unit": "%", "group": "고용", "latest": 4.1, "change": -0.1, "as_of": "2025-09"}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "거시경제 패널" in a.title
    assert a.table[0] == ["그룹", "지표", "최신값", "변화", "기준"]
    assert a.table[2][2] == "4.10%" and a.table[2][3] == "-0.10"


def test_artifacts_backtest_equity_curve():
    # CE-7: backtest → an equity-curve timeseries (portfolio + benchmark).
    tool = {"name": "datasets_store__backtest", "source": "ingestion store"}
    result = {"data": {"metrics": {"total_return": 0.21}, "curve": [
        {"date": "2024-01-02", "value": 10000.0}, {"date": "2025-01-02", "value": 12100.0}],
        "benchmark": {"ticker": "SPY", "curve": [
            {"date": "2024-01-02", "value": 10000.0}, {"date": "2025-01-02", "value": 11500.0}]}}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "timeseries" and "백테스트" in a.title and "+21.0%" in a.title
    labels = {s.label for s in a.series}
    assert "포트폴리오" in labels and "SPY" in labels


def test_artifacts_quant_screen_table():
    # CE-6: factor screener → a sourced ranked table.
    tool = {"name": "datasets_store__quant_screen", "source": "ingestion store"}
    result = {"data": {"market": "US", "sort": "roe", "count": 1, "results": [
        {"ticker": "AAPL", "market_cap": 3_000_000_000_000, "pe": 30.0, "pb": 45.0, "roe": 1.5, "return_window": 0.18}]}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "퀀트 스크리너" in a.title
    assert a.table[0] == ["종목", "시총", "PER", "PBR", "ROE", "기간수익"]
    assert a.table[1][0] == "AAPL" and "T" in a.table[1][1] and a.table[1][5] == "+18.0%"


def test_artifacts_valuation_table():
    # CE-5: a valuation calc → a sourced table with the projection + intrinsic value summary.
    tool = {"name": "datasets_store__valuation", "source": "재무제표 기반 모델"}
    result = {"data": {"model": "dcf", "ticker": "AAPL", "value_per_share": 182.5, "as_of": "2025-09-27",
                       "source": "SEC EDGAR", "breakdown": {"rows": [
                           {"year": 1, "fcf": 1100.0, "pv": 1000.0}, {"year": 2, "fcf": 1210.0, "pv": 980.0}]}}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and "DCF" in a.title and "182.5" in a.title
    assert a.table[0] == ["연차", "예상 FCF", "현재가치(PV)"]
    assert a.table[-1][0] == "내재가치 / 주" and a.table[-1][1] == "182.50"


def test_artifacts_sector_heatmap_table():
    # CE-2: sector heatmap → a sourced ranked table card.
    tool = {"name": "yahoo__sector_heatmap", "source": "Yahoo Finance"}
    result = {"data": {"sectors": [
        {"sector": "기술", "ticker": "XLK", "change_percent": 2.5},
        {"sector": "금융", "ticker": "XLF", "change_percent": -1.0}],
        "source": "Yahoo Finance", "as_of": "2024-01-02"}}
    a = A._artifacts(tool, result)[0]
    assert a.kind == "table" and a.table[0] == ["섹터", "ETF", "등락%"]
    assert a.table[1] == ["기술", "XLK", "+2.50%"] and a.table[2][2] == "-1.00%"


def test_artifacts_from_technical_indicators_become_overlay_artifact():
    # PH-VIZ-4: /technical-indicators → a standalone overlay artifact (price-pane + sub-pane).
    tool = {"name": "yahoo__technical_indicators", "source": "Yahoo Finance"}
    a = A._artifacts(tool, _tech_result())[0]
    assert a.ticker == "AAPL" and a.tool == "yahoo__technical_indicators" and not a.candles
    keys = {o.key: o for o in a.overlays}
    assert keys["sma_20"].pane == "price" and keys["rsi_14"].pane == "sub"
    assert keys["sma_20"].lines[0].points[0].value == 184.1 and keys["sma_20"].lines[0].color
    assert a.source.startswith("Technical indicators")


def test_enrich_chart_overlays_merges_onto_price_chart():
    # PH-VIZ-4: the standalone technical artifact folds onto the same-ticker price chart.
    from agentengine.artifacts import enrich_chart_overlays
    from agentengine.models import Artifact, ArtifactCandle
    tool = {"name": "yahoo__technical_indicators", "source": "Yahoo Finance"}
    price = Artifact(kind="candlestick", title="AAPL 주가", ticker="AAPL",
                     candles=[ArtifactCandle(time="2024-01-02", open=1, high=2, low=1, close=1.5)])
    tech = A._artifacts(tool, _tech_result())[0]
    arts = [price, tech]
    enrich_chart_overlays(arts)
    assert arts == [price]                       # standalone merged away
    assert {o.key for o in price.overlays} == {"sma_20", "rsi_14"}


def test_enrich_chart_overlays_standalone_when_no_price_chart():
    from agentengine.artifacts import enrich_chart_overlays
    tool = {"name": "yahoo__technical_indicators", "source": "Yahoo Finance"}
    tech = A._artifacts(tool, _tech_result("MSFT"))[0]
    arts = [tech]
    enrich_chart_overlays(arts)
    assert arts == [tech] and tech.overlays   # no price chart → renders on its own


def test_chart_annotation_validate_drops_future_and_out_of_range():
    # PH-VIZ-3: only historical, in-range, sane-price annotations survive (no projection).
    from agentengine.annotations import _validate
    raw = {
        "lines": [{"x1": "2024-02-01", "y1": 170, "x2": "2024-06-01", "y2": 200, "label": "저점→고점"},
                  {"x1": "2099-01-01", "y1": 170, "x2": "2024-06-01", "y2": 200}],   # future endpoint → dropped
        "hlines": [{"price": 195, "label": "저항"}, {"price": 999999}],               # absurd price → dropped
        "vlines": [{"time": "2024-03-15", "label": "x"}, {"time": "2030-01-01"}],      # future → dropped
    }
    ann = _validate(raw, "2024-01-01", "2024-12-31", 150.0, 210.0)
    assert ann and len(ann.lines) == 1 and ann.lines[0].label == "저점→고점"
    assert len(ann.hlines) == 1 and ann.hlines[0].price == 195
    assert len(ann.vlines) == 1 and ann.vlines[0].time == "2024-03-15"


async def test_annotate_charts_attaches_validated_spec(monkeypatch):
    from agentengine import annotations as AN
    from agentengine.models import Artifact, ArtifactCandle
    a = Artifact(kind="candlestick", title="AAPL 주가", ticker="AAPL", candles=[
        ArtifactCandle(time="2024-01-02", open=180, high=190, low=170, close=185),
        ArtifactCandle(time="2024-06-03", open=185, high=210, low=180, close=200)])

    async def fake(model, q, digest, tk):
        return {"lines": [{"x1": "2024-01-02", "y1": 170, "x2": "2024-06-03", "y2": 210, "label": "L"}], "note": "n"}
    monkeypatch.setattr(AN, "_gemini_annotate", fake)
    await AN.annotate_charts([a], "2024 저점에서 고점까지 선 그어줘", "gemini-x", "gemini")
    assert a.annotations and a.annotations.lines[0].label == "L" and a.annotations.note == "n"


async def test_annotate_charts_noop_without_key():
    # annotate_charts is Gemini-only; with no GOOGLE_API_KEY the LLM call degrades to no
    # annotations (never raises, never fabricates a spec).
    pytest.importorskip("google.genai")  # needs the `gemini` extra; skip on the dep-free dev run
    from agentengine import annotations as AN
    from agentengine.models import Artifact, ArtifactCandle
    a = Artifact(kind="candlestick", title="x", ticker="AAPL",
                 candles=[ArtifactCandle(time="2024-01-02", open=1, high=2, low=1, close=1.5)])
    await AN.annotate_charts([a], "q", "m", "gemini")   # keyless → no LLM judgment available
    assert a.annotations is None


@respx.mock
async def test_chat_stream_emits_thinking_progress(monkeypatch):
    # PH-THINK: the chat stream narrates its reasoning live (analyze → fetch → found → synthesize).
    import agentengine.chat as C
    from agentengine.chat import stream_chat
    _gw(monkeypatch)
    _catalog()
    _fp = _fake_planner("yahoo__prices", {"ticker": "AAPL", "interval": "day",
                                          "start_date": "2024-01-02", "end_date": "2024-01-05", "market": "US"})
    monkeypatch.setattr(A, "get_planner", lambda _b=None: _fp)
    monkeypatch.setattr(C, "get_planner", lambda _b=None: _fp)
    respx.route(method="GET", url__regex=r"http://gw\.test/prices").mock(
        return_value=httpx.Response(200, json={"ticker": "AAPL", "prices": [{"time": "2024-01-02", "close": 185.6}]},
                                    headers={"x-connector": "yahoo"}))
    events = [e async for e in stream_chat([{"role": "user", "content": "AAPL price chart"}], "vgk_x")]
    phases = [e.get("phase") for e in events if e.get("type") == "thinking"]
    assert {"analyze", "fetch", "found", "synthesize"} <= set(phases)
    assert events[0]["type"] == "thinking" and events[0]["phase"] == "analyze"  # narration starts immediately
    found = next(e for e in events if e.get("phase") == "found")
    assert "근거" in found["text"]


async def test_refine_evidence_noop_without_gemini_or_evidence():
    # PH-THINK verify pass: stub backend / no evidence → (no brief, no scores) (never blocks).
    from agentengine.agent import refine_evidence
    assert await refine_evidence("q", [{"index": 1, "source": "SEC", "snippet": "x"}], "m", "stub") == (None, {})
    assert await refine_evidence("q", [], "m", "gemini") == (None, {})


async def test_suggest_followups_parses_and_caps(monkeypatch):
    # PH-THINK: chips ALWAYS render after an answer. stub (no LLM) → deterministic capability-aware
    # fallback (3-4, ticker-aware); empty answer → none; gemini → parsed deep chips (≤4).
    from agentengine.agent import suggest_followups
    stub = await suggest_followups("엔비디아 분석", "an answer", "m", "stub", tickers=["NVDA"])
    assert 3 <= len(stub) <= 4 and any("NVDA" in s for s in stub)   # never empty on stub
    assert await suggest_followups("q", "an answer", "m", "stub") != []  # no ticker → generic fallback
    assert await suggest_followups("q", "", "m", "gemini") == []   # no answer → none

    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai
    mc = MagicMock(); mr = MagicMock()
    mr.text = '{"followups": ["NVDA 데이터센터 매출 비중은?", "AMD 대비 마진 차이는?", "최근 공시상 공급 리스크는?", "5번째", "6번째"]}'
    mc.models.generate_content.return_value = mr
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mc)
    out = await suggest_followups("엔비디아 실적", "매출 X, 순이익 Y …", "gemini-x", "gemini")
    # parallel personas → merged + deduped + capped (both personas mock-identical → 3 unique)
    assert 1 <= len(out) <= 4 and out[0].startswith("NVDA") and len(out) == len(set(out))


def test_fallback_followups_always_nonempty_and_capability_aware():
    # The deterministic safety net: chips are NEVER empty, ticker-aware when we know the company,
    # generic-but-differentiated otherwise, deduped + capped at 4.
    from agentengine.agent import _fallback_followups
    with_tk = _fallback_followups("삼성전자 분석", tickers=["005930", "000660"])
    assert 3 <= len(with_tk) <= 4 and len(with_tk) == len(set(with_tk))
    assert any("005930" in s for s in with_tk) and any("000660" in s for s in with_tk)  # compare chip
    generic = _fallback_followups("금리 인상 영향은?", tickers=[])
    assert 3 <= len(generic) <= 4 and any("출처" in s for s in generic)  # provenance showcase


def test_merge_followups_interleaves_and_dedups():
    from agentengine.agent import _merge_followups
    a = ["엔비디아 매출 비중은?", "마진 추이는?", "공급 리스크는?"]
    b = ["엔비디아 매출 비중은?!", "외국인·기관 수급은?", "DCF 내재가치는?"]  # [0] is a near-dup of a[0]
    out = _merge_followups([a, b], limit=4)
    assert len(out) == 4 and len(out) == len(set(out))
    assert out[0] == "엔비디아 매출 비중은?" and "외국인·기관 수급은?" in out  # interleaved across personas
    assert "엔비디아 매출 비중은?!" not in out  # near-duplicate dropped (normalized)


async def test_refine_evidence_parses_brief_and_confidence(monkeypatch):
    # PH-THINK: one verify pass returns BOTH a grounding brief and per-source confidence.
    pytest.importorskip("google.genai")  # needs the `gemini` extra
    from unittest.mock import MagicMock
    import google.genai
    import agentengine.agent as AG

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = ('{"brief": "출처 [1]이 핵심 수치를 담고 있음.", '
                      '"sources": [{"index": 1, "confidence": "high", "why": "직접 공시"}, '
                      '{"index": 2, "confidence": "bogus"}]}')
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    cites = [{"index": 1, "source": "SEC", "snippet": "revenue 100"},
             {"index": 2, "source": "news", "snippet": "x"}]
    brief, scores = await AG.refine_evidence("매출?", cites, "gemini-x", "gemini")
    assert "핵심" in brief
    assert scores[1] == {"confidence": "high", "why": "직접 공시"}
    assert 2 not in scores   # an invalid confidence value is dropped, never guessed


def test_citations_and_artifacts_carry_cadence_and_category():
    """The catalog tool dict carries periodicity (cadence) + category; both must ride onto every
    citation/artifact so the pin→alert flow can gate on cadence (periodic ⇒ alertable)."""
    tool = {"name": "yahoo__prices", "source": "Yahoo Finance", "connector": "yahoo",
            "cadence": "daily", "category": "market"}
    data = {"ticker": "AAPL", "prices": [
        {"time": "2024-01-02", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10},
        {"time": "2024-01-03", "open": 2, "high": 3, "low": 2, "close": 3, "volume": 12}]}
    result = {"data": data}
    cites = A._citations(tool, result)
    assert cites and all(c.cadence == "daily" and c.category == "market" for c in cites)
    arts = A._artifacts(tool, result)
    assert arts and all(a.cadence == "daily" and a.category == "market" for a in arts)

    # a one-shot source is stamped one_shot → no notification bot once pinned
    tool2 = {"name": "sec_edgar__company_facts", "source": "SEC EDGAR", "connector": "sec_edgar",
             "cadence": "one_shot", "category": "fundamentals"}
    c2 = A._citations(tool2, {"data": {"ticker": "AAPL", "name": "Apple"}})
    assert c2 and all(x.cadence == "one_shot" for x in c2)
