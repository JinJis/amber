"""EV-PASSAGE — filing-listing citations get REAL passages (accession-matched RAG), not titles."""

from __future__ import annotations

import pytest

from agentengine.passages import enrich_listing_passages, looks_like_title, sentence_for

ANSWER = ("삼성전자는 자기주식 3조원 취득을 결정했어요 [2]. 시장 반응은 긍정적이었어요. "
          "Apple은 공급망 리스크를 새로 공시했어요 [3].")

PASSAGE_KR = ("회사는 주주가치 제고를 위하여 자기주식 3조원 규모의 취득을 이사회에서 결의하였으며, "
              "취득 예정 기간은 2026년 7월부터 2027년 1월까지이다.")


def _rag_tool():
    return {"name": "rag__search", "path": "/rag/search", "method": "POST", "params": []}


def test_looks_like_title_separates_titles_from_passages():
    assert looks_like_title("주요사항보고서(자기주식취득결정)")
    assert looks_like_title("분기보고서")
    assert looks_like_title(None)
    assert not looks_like_title(PASSAGE_KR)


def test_sentence_for_extracts_the_citing_sentence():
    s = sentence_for(ANSWER, 2)
    assert s and "자기주식" in s and "[2]" not in s
    assert sentence_for(ANSWER, 9) is None


@pytest.mark.asyncio
async def test_listing_citation_gains_real_passage_and_highlight():
    cit = {"index": 2, "kind": "filing", "used": True, "page": "20260708000123",
           "snippet": "주요사항보고서(자기주식취득결정)", "doc_type": "주요사항보고서",
           "source": "OpenDART (FSS)"}

    async def call_tool(tool, args):
        assert tool["name"] == "rag__search" and "자기주식" in args["query"]
        return {"status": 200, "data": {"hits": [
            {"text": "무관한 다른 공시 본문입니다. " * 5, "provenance": {"accession": "999"}},
            {"text": PASSAGE_KR, "provenance": {"accession": "2026-0708-000123"}},  # dash variants match
        ]}}

    await enrich_listing_passages(call_tool, _rag_tool(), [(cit, "KR", None)], ANSWER)
    assert "자기주식 3조원" in cit["snippet"] and "주요사항보고서(" not in cit["snippet"]
    assert cit["evidence_image_url"].startswith("/evidence?")
    assert "20260708000123" in cit["evidence_image_url"].replace("-", "")


@pytest.mark.asyncio
async def test_no_accession_match_keeps_title_shape():
    cit = {"index": 3, "kind": "filing", "used": True, "page": "0000320193-26-000001",
           "snippet": "8-K", "doc_type": "8-K", "source": "SEC EDGAR"}

    async def call_tool(tool, args):
        return {"status": 200, "data": {"hits": [
            {"text": "some other filing body " * 5, "provenance": {"accession": "not-it"}}]}}

    await enrich_listing_passages(call_tool, _rag_tool(), [(cit, "US", None)], ANSWER)
    assert cit["snippet"] == "8-K"          # unchanged — never fabricate a passage
    assert "evidence_image_url" not in cit


@pytest.mark.asyncio
async def test_rag_failure_is_swallowed():
    cit = {"index": 2, "kind": "filing", "used": True, "page": "X1", "snippet": "분기보고서"}

    async def call_tool(tool, args):
        raise RuntimeError("rag down")

    await enrich_listing_passages(call_tool, _rag_tool(), [(cit, "KR", None)], ANSWER)
    assert cit["snippet"] == "분기보고서"    # best-effort: turn never fails


@pytest.mark.asyncio
async def test_v9_on_demand_ingest_fallback_via_filing_search():
    """V-9: rag 미스 → filing_search(온디맨드 인제스트 내장) 폴백에서 accession 매칭."""
    cit = {"index": 3, "kind": "filing", "used": True, "page": "0000320193-26-000001",
           "snippet": "8-K", "doc_type": "8-K", "source": "SEC EDGAR"}
    ft = {"name": "datasets_store__filing_search", "path": "/filings/search", "method": "GET", "params": []}
    calls = []

    async def call_tool(tool, args):
        calls.append(tool["name"])
        if tool["name"] == "rag__search":
            return {"status": 200, "data": {"hits": []}}   # 코퍼스에 없음
        assert args["ticker"] == "AAPL" and args["market"] == "US"
        return {"status": 200, "data": {"hits": [
            {"text": "Apple disclosed new supply-chain risks in this report. " * 3,
             "provenance": {"accession": "0000320193-26-000001"}}]}}

    await enrich_listing_passages(call_tool, _rag_tool(), [(cit, "US", "AAPL")], ANSWER,
                                  search_tool=ft)
    assert calls == ["rag__search", "datasets_store__filing_search"]
    assert "supply-chain risks" in cit["snippet"]
    assert cit["evidence_image_url"].startswith("/evidence?")
