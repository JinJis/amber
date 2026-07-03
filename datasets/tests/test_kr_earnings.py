"""KR earnings-disclosure ingestion (잠정실적 공정공시 → RAG): resolver filter · doc tagging · ingest.

OpenDART + the RAG POST + the filing-HTML fetch are mocked; no network or key needed. End-to-end live
data (real DART) is exercised by the user once OPENDART_API_KEY is set — this is the KR analog of the
US transcript pipeline (KR has no free earnings-call transcript/audio API).
"""

from __future__ import annotations

import pytest

from app.providers.kr import opendart as OD
from app.store import filing_ingest as FI
from app.store import kr_earnings_ingest as KE
from app.symbols import Market, build_ref

# DART list.json newest-first; mixes 잠정실적 공정공시 with the other 공정공시 (공급계약) + a 정기보고서.
_LIST = {"status": "000", "list": [
    {"report_nm": "연결재무제표기준영업(잠정)실적(공정공시)", "rcept_no": "20260430800083", "rcept_dt": "20260430"},
    {"report_nm": "[기재정정]영업(잠정)실적(공정공시)", "rcept_no": "20260430800097", "rcept_dt": "20260430"},
    {"report_nm": "단일판매ㆍ공급계약체결(공정공시)", "rcept_no": "20260101800001", "rcept_dt": "20260101"},  # 공정공시지만 실적 아님
    {"report_nm": "분기보고서", "rcept_no": "20260515000111", "rcept_dt": "20260515"},                     # 공정공시 아님
]}


@pytest.mark.asyncio
async def test_earnings_disclosures_filters_to_jamjeong(monkeypatch):
    async def fake_corp(ref):
        return "00126380"

    async def fake_list(path, params):
        return _LIST

    monkeypatch.setattr(OD, "_corp_code", fake_corp)
    monkeypatch.setattr(OD, "_dart_json", fake_list)
    discs = await OD.OpenDartProvider().earnings_disclosures(build_ref(Market.KR, "005930"), 4)
    nms = [d["report_nm"] for d in discs]
    assert len(discs) == 2                                   # only the two 실적 공정공시 …
    assert all("실적" in n and "공정공시" in n for n in nms)  # … 공급계약·분기보고서 excluded
    assert discs[0]["rcept_no"] == "20260430800083"
    assert discs[0]["date"] == "2026-04-30"
    assert discs[0]["url"].endswith("rcpNo=20260430800083")


@pytest.mark.asyncio
async def test_ingest_kr_earnings_us_is_noop():
    assert await KE.ingest_kr_earnings_for_ticker("US", "AAPL") == 0   # 잠정실적 공정공시 is KR-only


def test_html_to_docs_tags_doc_type_earnings():
    html = "<html><body>" + ("삼성전자 연결 영업이익 잠정 실적 매출액 단위 백만원 " * 80) + "</body></html>"
    docs = FI._html_to_docs(html, "KR", "005930", "20260430800083", "OpenDART", "http://dart/x", "earnings")
    assert docs and all(d["doc_type"] == "earnings" and d["market"] == "KR" for d in docs)
    assert docs[0]["accession"] == "20260430800083"
    assert docs[0]["doc_id"].startswith("20260430800083:s.")


@pytest.mark.asyncio
async def test_ingest_kr_earnings_indexes_and_warms_viewer(monkeypatch):
    sample = "<html><body>" + ("삼성전자 연결 영업이익 잠정 매출액 단위 백만원 " * 80) + "</body></html>"

    class FakeProv:
        async def earnings_disclosures(self, ref, limit):
            return [{"rcept_no": "20260430800083",
                     "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)",
                     "date": "2026-04-30", "url": "http://dart/x"}]

    captured: dict = {}

    async def fake_html(market, accn, *a, **k):
        captured["accn"] = accn
        return sample                                        # stands in for the cached DART markup

    async def fake_rag(url, docs):
        captured["docs"] = docs
        return len(docs)

    monkeypatch.setattr(KE, "get_financials_provider", lambda m: FakeProv())
    monkeypatch.setattr(KE, "get_filing_html", fake_html)
    monkeypatch.setattr(KE, "_ingest_to_rag", fake_rag)

    n = await KE.ingest_kr_earnings_for_ticker("KR", "005930", rag_url="http://rag.test")
    assert n >= 1 and captured["accn"] == "20260430800083"   # viewer markup fetched (→ cached) by rcept_no
    assert captured["docs"] and all(d["doc_type"] == "earnings" and d["market"] == "KR" for d in captured["docs"])
    assert captured["docs"][0]["accession"] == "20260430800083"
