# Agent Engine

Run agents over a **tenant's activated connectors + RAG**. The agent's tools are resolved from the
gateway catalog and every tool call goes **through the control-plane gateway with the tenant key**, so
entitlement, metering, and audit apply to agent activity too. Answers carry **provenance citations**,
and a **guardrail** refuses forecasts / price targets / buy-sell advice at the boundary.

```
caller ─▶ /agent/run (tenant key) ─▶ guardrail ─▶ plan → call tool (gateway) → observe → finalize ─▶ answer + citations
```

## Planner (`AGENT_LLM_BACKEND`)
Gemini-only — `AGENT_LLM_BACKEND=gemini` is the sole supported value (default `gemini`; the env var is
kept for compatibility). Real Gemini function-calling (extra `gemini`, needs `GOOGLE_API_KEY`; works with
Vertex too). There is no keyless/keyword-routing planner — routing, clarification, and synthesis all come
from Gemini.

## Endpoints
- `POST /agent/run` — `{task, spec?}` + `X-API-KEY` → `{answer, refused, steps, citations, usage}`
- `POST /agent/compile` — natural-language `{description}` → reusable `AgentSpec` (SDK mode)
- `GET /agent/info` — active planner/config

## Run
```bash
cd agent-engine
uv sync --extra dev
uv run uvicorn agentengine.main:app --reload --port 8003   # AGENT_GATEWAY_URL=http://127.0.0.1:8010
uv run pytest -q
# real LLM: uv sync --extra gemini && export AGENT_LLM_BACKEND=gemini GOOGLE_API_KEY=...

curl -X POST localhost:8003/agent/run -H 'X-API-KEY: vgk_...' -H 'Content-Type: application/json' \
  -d '{"task":"What is AAPL latest revenue?"}'
```
