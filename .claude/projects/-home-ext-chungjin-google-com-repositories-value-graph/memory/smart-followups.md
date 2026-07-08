---
name: smart-followups
description: Follow-up suggestion chips are a core differentiator — capability-aware, scratch the itch, showcase our data
metadata:
  type: feedback
---

The 3-4 follow-up chips under each chat answer are a **core differentiator**, not a nicety. They must (a) scratch the user's curiosity (the "왜?" behind a figure), (b) span beginner→expert, and (c) naturally lead the user into experiencing our differentiated datasources/features (출처·증거, 차트, 수급(KIS), 13F 거장, 백테스트, 밸류에이션 모델, 거시 패널, 반도체 프록시, 컨센서스, 내러티브/밸류체인) — without feeling salesy.

**Why:** the product spans 투자 초보 → 전문 투자자; suggestions are how we surface depth + provenance/evidence/connector/MCP/chart differentiators in conversation. User's example: 하이닉스 폭락 "왜?" → suggest 마이크론 실적/컨센서스, 외국인·기관 수급, 과거 급락 후 10/30/90일 통계, 반도체 PPI/SOX 사이클.

**How to apply:** `agent-engine/agentengine/agent.py::suggest_followups` runs TWO personas in PARALLEL on the deep model (gemini-pro) — "가려운 곳"(curiosity) + "차별화 쇼케이스"(maps to `_CAPABILITY_MENU`) — then `_merge_followups` interleaves+dedups to 3-4 diverse chips. **Keep `_CAPABILITY_MENU` in sync whenever a new connector/tool/feature ships** so suggestions can showcase it. Best-effort (never block the answer). Relates to [[tool-categories-not-apis]] (capabilities) and [[agent-orchestration-flow]].
