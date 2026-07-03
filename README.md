# Platform — Investment-Agent Data Platform

> A fresh start. This workspace is the **new service**, built up from the `datasets/` data API.
> **The legacy ValueGraph engine (`/services`, `/apps`, CVE, Deep-Research data acquisition) is treated
> as nonexistent** — none of it is a dependency here.

The goal: a **multi-tenant platform for investment agents** — a data-source layer that tenants activate
to their needs, exposed as a **REST API, an MCP server, a RAG server, and an Agent Engine**, where
builders develop against a defined interface or via natural language.

📖 **Docs:** [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) (detailed design + progress). The roadmap
& UX-design docs are being rewritten (full UX overhaul + MVP); the old ones are in
[`docs/deprecate/`](./docs/deprecate/) for reference.

## Status

| Component | Path | Status | Tests |
|---|---|---|---|
| Data plane (US+KR financial API) | `datasets/` | ✅ | 63 |
| Connector catalog/manifests (P0) | `datasets/app/connectors/` | ✅ | — |
| Control plane (tenancy, entitlements, gateway, metering) | `control-plane/` | ✅ P1 | 12 |
| MCP server (tools from catalog) | `mcp/` | ✅ P2 | 9 |
| RAG (pluggable CPU-OSS / GCP / GPU; routed via gateway + MCP) | `rag/` | ✅ P3 | 14 |
| Agent Engine (tools + RAG via gateway, guardrails, citations, streaming chat) | `agent-engine/` | ✅ P4 | 21 |
| Studio API (provisioning, conversations, chat BFF, **agent builder**, **prompt library**) | `studio-api/` | ✅ | 24 |
| Web — chat UI + **agent builder** + **prompt library** (Next.js + Auth.js Google) | `web/` | ✅ F1·F2 | build |
| **End-to-end** (full stack via compose, incl. chat) | `scripts/e2e.sh` | ✅ | — |

## Layout

```
.                  # repo root
  datasets/        # ✅ DATA PLANE — US+KR financial data API (the foundation; built & tested)
                   #    connectors (SEC/DART/Yahoo/FRED/ECOS/news) · point-in-time ingestion store
                   #    · bulk/deep backfill · Procrastinate queue+worker · self-test · catalog (P0)
  control-plane/   # ✅ CONTROL PLANE — tenants · scoped API keys · connector activation/entitlements
                   #    · metering · audit · rate-limit · gateway in front of the data plane (P1)
  mcp/             # ✅ MCP SERVER — tenant-scoped tools auto-derived from the catalog, routed through
                   #    the gateway with the tenant key (entitlement + metering enforced) (P2)
  rag/             # ✅ RAG SERVICE — provenance-first chunk→embed→store→retrieve→rerank, with
                   #    pluggable backends (CPU-OSS / GCP-Vertex / GPU) selected by .env (P3)
  agent-engine/    # ✅ AGENT ENGINE — run/stream agents over activated connectors + RAG via the gateway;
                   #    guardrails (no advice/forecasting) + provenance citations; Gemini planner (P4)
  studio-api/      # ✅ STUDIO API — Google user→tenant provisioning, conversations, chat BFF (holds the key)
  web/             # ✅ WEB — chat UI (Next.js + Auth.js); tools & sources panel + agent builder (F1)
                   #    + prompt library / community import (F2)
  # next phase: Telegram/Slack messengers (F3)
```

## Principles

- **Deterministic *data*, not deterministic *logic*** — connectors are API-based, so figures are
  structured, fast, reproducible, and always accurately sourced (Deep Research is at most one optional
  tool). This is about the **data plane**, not the reasoning: answer quality and orchestration come from
  Gemini / multi-agent flows, **never hardcoded keyword/heuristic rules** — the platform is Gemini-only,
  with no keyword router anywhere.
- **Provenance/trust envelope everywhere** — every datum/chunk/agent output carries source + as-of +
  freshness (+ confidence where derivable). No number without a source.
- **Platform holds upstream keys, meters usage, bills tenants** — so a per-connector license /
  redistribution policy is mandatory (SEC/DART/FRED are redistribution-safe; restricted feeds use
  BYO-key).
- **One Gemini router, one tenancy model** — don't fork.

## Roadmap

The platform (PH hardening + connector waves) is built (see the status table above). The roadmap is
being rewritten for a **full UX overhaul + MVP**; the old task tracker is archived in
[`docs/deprecate/ROADMAP.md`](./docs/deprecate/ROADMAP.md).

## Run the whole stack (Docker — recommended)

A single `docker compose` brings up **all six services** from one shared `.env`:

```bash
cp .env.example .env          # free keys (OPENDART/ECOS/FRED); AUTH_DEV_LOGIN=true; GOOGLE_API_KEY for Gemini
docker compose up --build     # datasets :8000 · gateway :8010 · rag :8002 · agent :8003 · studio :8004 · web :3000
docker compose ps             # health of each service
docker compose logs -f web    # follow a service's logs
docker compose down           # stop  (add -v to also wipe the SQLite/volume state)
```

| Service | Port | What |
|---|---|---|
| `datasets` | 8000 | data plane (US+KR financial API, `/docs`) |
| `control-plane` | 8010 | tenant gateway (auth · entitlement · metering) |
| `rag` | 8002 | retrieval (hash default; `RAG_EMBEDDING_BACKEND=oss-cpu` for real embeddings) |
| `agent-engine` | 8003 | agent loop (Gemini; `AGENT_LLM_BACKEND=gemini`, needs `GOOGLE_API_KEY`) |
| `studio-api` | 8004 | provisioning · conversations · chat BFF |
| `web` | 3000 | chat UI (open <http://localhost:3000>) |
| `admin` | 8005 | Django-admin-style CRUD over every service DB + ops console (login `admin`/`admin`) |

Rebuild one service after a code change: `docker compose up -d --build agent-engine`.
Drive the data plane through the gateway:
```bash
A='-H X-Admin-Token:dev-admin-token'
# POST /admin/tenants -> /projects -> /keys -> /activations (connector_id), then:
curl -H "X-API-KEY: vgk_..." "http://127.0.0.1:8010/company/facts?ticker=AAPL&market=US"
```
Without Docker you can still run any service with `uv run uvicorn <pkg>.main:app --port <p>` (each reads `../.env`).

## Run the tests

**Everything in one command — only Docker is required** (no host `uv`/`npm`/`pytest`). Unit tests run in
the `uv` image, the web build is a docker build, and the e2e + eval drive `docker compose`:

```bash
bash scripts/test_all.sh          # GOOGLE_API_KEY in .env enables the live e2e + eval; else they skip cleanly
```

Or run a layer on its own — the **docker** harnesses bring the stack up themselves:

```bash
# Docker end-to-end (each spins the stack via docker compose, then tears it down):
bash scripts/coverage.sh          # EVERY catalog tool (all 29) called through the gateway — coverage matrix
bash scripts/e2e.sh               # Gemini planner, whole product chain — skips cleanly (exit 2) without a key
bash scripts/e2e_functional.sh    # REAL data + MCP tool calls + semantic RAG (oss-cpu) + entitlement — no key
GOOGLE_API_KEY=... bash scripts/e2e_live.sh   # REAL Gemini: grounded, cited answers (skips cleanly w/o a key)

# Quality eval (needs the stack up: `docker compose up -d` first; skips without GOOGLE_API_KEY):
python3 eval/run_eval.py          # 14 scenarios across every source; scores tool-use, grounding, citations, guardrails

# Unit tests in docker (one service) — no host uv needed:
docker run --rm -v "$PWD/datasets:/app" -w /app ghcr.io/astral-sh/uv:python3.11-bookworm-slim \
  sh -lc "uv run --extra dev pytest -q"
# (host shortcut, only if you have uv: cd datasets && uv run --extra dev pytest -q)
```

**156 unit tests** pass + web build. Three docker e2e harnesses + a scenario-based **quality eval**:
- **`e2e.sh`** — Gemini planner, the whole chain (catalog → tenant → entitlement → data plane + RAG via
  gateway → metering → MCP → studio chat → agent builder → prompt import). Skips cleanly (exit 2) without
  a `GOOGLE_API_KEY`.
- **`e2e_functional.sh`** — real upstream numbers (Apple facts, a live AAPL close, Samsung's KRW revenue,
  the BOK rate), MCP real tool calls + schema + entitlement, and **real semantic RAG** (oss-cpu) with provenance.
- **`e2e_live.sh`** — the real Gemini planner answering grounded, cited questions (Apple FY2025 rev cited
  to SEC EDGAR; Samsung ₩333.6T to OpenDART; a "should I buy?" prompt refused).
- **`eval/run_eval.py`** — builds agents with chosen data sources and scores answer quality across 14
  scenarios (right source called · grounded figure · correct citation · restriction honoured · guardrail ·
  multi-turn context) + an optional Gemini judge. Latest: 59/59 checks · judge 5.00/5. See [`eval/README.md`](./eval/README.md).

## The product (chat UI)

```bash
cp .env.example .env                       # AUTH_DEV_LOGIN=true for local login without Google
docker compose up --build                  # whole stack incl. web on :3000
# open http://localhost:3000 — ask "삼성전자 최근 실적"; the agent answers with sources.
```
The browser never holds a platform key: web BFF (Auth.js session) → studio-api (holds the tenant key) →
agent-engine → tools via the metered gateway. Guardrails refuse advice/forecasting. For real token
streaming set `AGENT_LLM_BACKEND=gemini` + `GOOGLE_API_KEY`.
