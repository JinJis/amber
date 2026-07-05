"""M-FACT (FC-1) — fact-check orchestration: gather guide + verdict composition.

Flow: the intake flags ``fact_check=true`` (LLM intent, no regex) → the normal multi-tool
gather runs with ``FACT_CHECK_GUIDE`` steering it to confirm/refute the claim against
PRIMARY records → after synthesis, ONE structured LLM call composes the ``verdict``
artifact (schema-forced JSON) from the answer + the numbered citations. Validation is a
pure function so the composition rules are unit-testable without an LLM:

- every finding must cite an existing [n]; uncited findings are dropped;
- a verdict with zero cited findings degrades to 확인 불가 (never a confident verdict on
  thin evidence — rubric rule);
- '미래 주장(검증 불가)' passes through regardless of findings (we never score the future).
"""

from __future__ import annotations

import json
import logging

from agentengine.config import settings
from agentengine.models import Artifact, VerdictData, VerdictFinding

logger = logging.getLogger(__name__)

# Appended to the system prompt for a fact-check turn — steers the GATHER + prose.
FACT_CHECK_GUIDE = (
    "\n\n[팩트체크 형식] 이 답변은 사용자가 가져온 '주장'의 검증입니다. 주장을 구성 요소(수치·시점·"
    "주체)로 쪼개고, 각 요소를 1차 기록(공시·재무제표·가격·과거 통계·뉴스)과 대조하세요. 답변 구조: "
    "① 주장 인용(따옴표, 원문 그대로) ② 검증 결과 한 줄(사실 / 대체로 사실 / 사실과 다름 / 확인 불가 / "
    "미래 주장) ③ 근거 — 주장을 지지/반박하는 기록을 각각 [n] 인용과 함께 ④ 주장이 틀렸다면 기록상 "
    "실제 값. 미래에 대한 주장이면 검증 불가임을 밝히고, 관련 과거 기록이 있으면 '과거 기록 · 전망 아님' "
    "라벨과 함께 맥락으로만 덧붙이세요. 주장 자체의 실현 가능성 평가·예측·매수/매도 의견은 절대 금지."
)

VERDICTS = ("사실", "대체로 사실", "사실과 다름", "확인 불가", "미래 주장(검증 불가)")

_COMPOSE_PROMPT = (
    "You are composing a FACT-CHECK VERDICT card from a completed, sourced answer. The claim was "
    "verified against primary records; your job is ONLY to structure what the answer established — "
    "add nothing new, never soften or harden the verdict beyond the evidence.\n\n"
    "Rules:\n"
    "- verdict ∈ {{사실, 대체로 사실, 사실과 다름, 확인 불가, 미래 주장(검증 불가)}} — pick what the "
    "answer's evidence supports. A claim about the FUTURE is always 미래 주장(검증 불가).\n"
    "- findings: each a short record fact from the answer, supports=true/false vs the claim, and "
    "citation_idx = the [n] in the answer that backs it. Only use [n] values that appear in the "
    "sources list below. No finding without a citation.\n"
    "- corrected: when the claim is wrong/partly wrong, one sentence of what the record actually "
    "says (with the real figure). Otherwise null.\n"
    "- confidence: high/medium/low — how solid the evidentiary support is (NEVER a probability "
    "about the future).\n"
    "- method: one short line, which kinds of records were checked (SAME LANGUAGE as the claim).\n"
    "- claim: the user's claim VERBATIM (trim to the assertion).\n\n"
    "Claim (user turn): {task}\n\n"
    "Answer (the completed verification):\n{answer}\n\n"
    "Numbered sources:\n{sources}\n\n"
    "Reply JSON only."
)

_COMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "point": {"type": "string"},
            "supports": {"type": "boolean"},
            "citation_idx": {"type": "integer"},
        }, "required": ["point", "supports", "citation_idx"]}},
        "corrected": {"type": "string"},
        "method": {"type": "string"},
    },
    "required": ["claim", "verdict"],
}


def validate_verdict(d: dict, citation_indexes: set[int]) -> VerdictData | None:
    """Pure composition rules (unit-testable, no LLM): drop uncited findings; a scored verdict
    with ZERO cited findings degrades to 확인 불가; the future verdict passes through untouched."""
    if not isinstance(d, dict) or not (d.get("claim") or "").strip():
        return None
    verdict = str(d.get("verdict") or "").strip()
    if verdict not in VERDICTS:
        return None
    findings = []
    for f in d.get("findings") or []:
        if not isinstance(f, dict) or not (f.get("point") or "").strip():
            continue
        try:
            idx = int(f.get("citation_idx"))
        except (TypeError, ValueError):
            continue
        if idx not in citation_indexes:
            continue  # a finding must point at a real [n]
        findings.append(VerdictFinding(point=str(f["point"]).strip()[:300],
                                       supports=bool(f.get("supports")), citation_idx=idx))
    if not findings and verdict != "미래 주장(검증 불가)":
        # no cited evidence → never a confident verdict (rubric: verdict must match evidence)
        verdict = "확인 불가"
    conf = str(d.get("confidence") or "").strip().lower()
    return VerdictData(
        claim=str(d["claim"]).strip()[:500],
        verdict=verdict,
        confidence=(conf if conf in ("high", "medium", "low") else None),
        findings=findings,
        corrected=(str(d.get("corrected") or "").strip()[:500] or None),
        method=(str(d.get("method") or "").strip()[:200] or None),
    )


def verdict_artifact(v: VerdictData, tool_names: list[str] | None = None) -> Artifact:
    """The shareable receipt: a `verdict` artifact (renders as the verdict card, SH pipeline-ready)."""
    return Artifact(kind="verdict", title=f"팩트체크 — {v.verdict}", verdict=v,
                    source=v.method or "1차 기록 대조 검증", tool=(tool_names or [None])[0])


async def compose_verdict(task: str, answer: str, citations: list[dict],
                          backend: str | None = None) -> VerdictData | None:
    """ONE structured flash call (schema-forced) that folds the completed answer into the
    verdict card. Composition failure is never a failure mode — the prose answer stands."""
    if (backend or settings.llm_backend) != "gemini" or not (answer or "").strip():
        return None
    idxs = {c.get("index") for c in citations if isinstance(c.get("index"), int)}
    sources = "\n".join(
        f"[{c['index']}] {c.get('source') or '?'} — {(c.get('snippet') or '')[:120]}"
        for c in citations if isinstance(c.get("index"), int)) or "(no sources)"
    try:
        import asyncio
        from google.genai import types

        from agentengine.gemini_io import genai_client
        client = genai_client()
        resp = await asyncio.to_thread(
            client.models.generate_content, model=settings.budget_model,
            contents=_COMPOSE_PROMPT.format(task=(task or "")[:600], answer=(answer or "")[:4000],
                                            sources=sources[:2000]),
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               response_schema=_COMPOSE_SCHEMA, max_output_tokens=600))
        d = json.loads(getattr(resp, "text", "") or "{}")
        return validate_verdict(d, idxs)
    except Exception as exc:  # noqa: BLE001 — the verdict card is enrichment, never blocks the answer
        logger.warning("verdict composition failed (%s); answer stands without the card", exc)
        return None
