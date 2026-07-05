"""M-FACT (FC-1/FC-2) — intake flag, verdict composition rules, verdict artifact."""

from __future__ import annotations

import pytest

import agentengine.agent as A
from agentengine.factcheck import FACT_CHECK_GUIDE, validate_verdict, verdict_artifact
from agentengine.intake import _INTAKE_PROMPT
from agentengine.models import VerdictData


# --- FC-1: intake flag (mocked LLM — same pattern as the guardrail tests) ---------------

async def test_intake_parses_fact_check_flag(monkeypatch):
    pytest.importorskip("google.genai")
    from unittest.mock import MagicMock
    import google.genai

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp
    monkeypatch.setattr(google.genai, "Client", lambda *a, **k: mock_client)

    # a pasted claim → fact_check=true, and it suppresses clarify (we know the intent)
    mock_resp.text = ('{"restricted": false, "category": "none", "score": 0.05, "steps": 6, '
                      '"fact_check": true, "clarify": true, "clarify_prompt": "?", '
                      '"options": [{"label": "a"}, {"label": "b"}], "plan": "공시에서 영업이익 확인"}')
    intake = await A.analyze_task("삼성전자 이번 분기 영업이익 15조 넘었대. 맞아?", "gemini")
    assert intake.fact_check is True and intake.clarify is False

    # restricted stays restricted — fact_check never overrides the guardrail
    mock_resp.text = ('{"restricted": true, "category": "advice", "score": 0.95, "steps": 3, '
                      '"fact_check": true}')
    intake = await A.analyze_task("이 주식 사라던데 사야 하지?", "gemini")
    assert intake.restricted is True and intake.fact_check is False


def test_intake_prompt_teaches_fact_check_allowance():
    """Verifying a THIRD-PARTY claim (even a future one) is allowed — the verdict handles the
    future case ('미래 주장'), never the guardrail. Pin the wording so it can't regress."""
    p = _INTAKE_PROMPT.lower()
    assert "fact-checking a third-party claim is allowed" in p
    assert "fact_check" in p
    # future claims are fact-checkable (verdict says unverifiable), not refused
    assert "future claim is allowed to fact-check" in p or "allowed to fact-check" in p


# --- FC-1/FC-2: verdict composition rules (pure, no LLM) --------------------------------

def _d(**kw):
    base = {"claim": "삼성전자 이번 분기 영업이익 15조 넘었대", "verdict": "사실과 다름",
            "confidence": "high", "corrected": "기록상 12.1조", "method": "공시 대조",
            "findings": [{"point": "2026 Q1 영업이익 12.1조", "supports": False, "citation_idx": 1}]}
    base.update(kw)
    return base


def test_validate_verdict_keeps_cited_findings_only():
    v = validate_verdict(_d(findings=[
        {"point": "영업이익 12.1조", "supports": False, "citation_idx": 1},
        {"point": "출처 없는 근거", "supports": True, "citation_idx": 9},   # not a real [n]
        {"point": "인덱스 없음", "supports": True},                          # no citation at all
    ]), citation_indexes={1, 2})
    assert v is not None and len(v.findings) == 1
    assert v.findings[0].citation_idx == 1 and v.findings[0].supports is False
    assert v.verdict == "사실과 다름" and v.corrected == "기록상 12.1조"


def test_validate_verdict_degrades_to_unverified_without_cited_evidence():
    # a confident verdict on thin (uncited) evidence must not survive — rubric rule
    v = validate_verdict(_d(findings=[{"point": "근거", "supports": True, "citation_idx": 7}]),
                         citation_indexes={1})
    assert v is not None and v.findings == [] and v.verdict == "확인 불가"


def test_validate_verdict_future_claim_passes_without_findings():
    v = validate_verdict(_d(verdict="미래 주장(검증 불가)", findings=[]), citation_indexes=set())
    assert v is not None and v.verdict == "미래 주장(검증 불가)"


def test_validate_verdict_rejects_unknown_verdict_or_empty_claim():
    assert validate_verdict(_d(verdict="아마도 사실"), {1}) is None
    assert validate_verdict(_d(claim="  "), {1}) is None


def test_verdict_artifact_shape():
    v = VerdictData(claim="c", verdict="사실", method="공시 대조")
    a = verdict_artifact(v, ["sec_edgar__financials"])
    assert a.kind == "verdict" and a.verdict is v
    assert "팩트체크" in a.title and a.source == "공시 대조"


def test_fact_check_guide_bans_scoring_the_future():
    assert "미래" in FACT_CHECK_GUIDE and "매수/매도 의견은 절대 금지" in FACT_CHECK_GUIDE
    assert "과거 기록 · 전망 아님" in FACT_CHECK_GUIDE
