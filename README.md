# Amber — 출처가 보이는 AI 투자 리서치 (investment-agent data platform)

A **personal research desk**: the user staffs **standing analysts** (agents) on their own
**watchlists** of companies. Every analyst works **only from licensed, point-in-time, fully-cited
data**, renders figures as **live, sourced artifacts**, and **pushes what changed before being asked**
(schedule + disclosure calendar). It is *not* a chatbot — the differentiators are **trust by
construction**, **pull→push**, and a **clone-from-others ecosystem**.

> The legacy ValueGraph engine (`/services`, `/apps`, CVE, Deep-Research acquisition) has been removed —
> not a dependency here.

📖 **Docs** (read before building): the engineering rules + docs map live in the repo-root
[`CLAUDE.md`](./CLAUDE.md). Plan/tasks: [`docs/ROADMAP.md`](./docs/ROADMAP.md). Current design:
[`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md). Retired specs are in
[`docs/deprecate/`](./docs/deprecate/) — reference only.

## Services

One `docker compose`; config split across `env/*.env` (a single root `.env` also works).

| Service | Port | Package | Role | Tests |
|---|---|---|---|---|
| `datasets` | 8000 | `app` | data plane: US+KR connectors + ingestion store + `/catalog` | 340 |
| `worker` | — | `app.queue` | Procrastinate worker: cron sweeps + ingestion jobs (Postgres queue, no Redis) | — |
| `control-plane` | 8010→8001 | `controlplane` | the **gateway** (auth → entitlement → rate-limit → meter/audit) + tenants/keys admin | 19 |
| `rag` | 8002 | `rag` | provenance-first chunk→embed→retrieve→rerank | 37 |
| `agent-engine` | 8003 | `agentengine` | guardrail→plan (Gemini)→tool loop→citations; `/agent/chat` SSE; desk-feed / ask-feed | 202 |
| `studio-api` | 8004 | `studioapi` | Google user→tenant provisioning; conversations; **holds tenant key**; watchlists/analysts/briefs | 142 |
| `web` | 3000 | Next.js | chat UI + builder; `/api/*` BFF (Auth.js session only) | 95 |
| `admin` | 8005 | `adminpanel` | out-of-band CRUD/ops console over service DBs (not in the request path) | 70 |
| `mcp` | stdio | `mcpserver` | one tool per catalog resource, routed through the gateway | 9 |

Request flow (one chat turn): browser → web BFF (session) → studio-api (tenant key) → agent-engine →
**gateway** (entitle + meter) → datasets/rag → upstreams. Full diagram in `docs/ARCHITECTURE.md` §2.

## Architecture invariants (never violate — see `CLAUDE.md` §2)

- **No number without a source** — every datum/chunk/artifact carries `source` + `as_of` + `freshness`
  (+ `confidence`/interval where derivable). Unsourced → it doesn't ship.
- **The gateway is the only path to data** — agents/MCP/external callers reach `datasets`/`rag` only
  through the control-plane gateway (auth → entitlement → rate-limit → meter/audit).
- **No forecasting / no advice** — forecasts, price targets, momentum, scored feeds, buy/sell advice are
  refused at the agent boundary, and the refusal/label is **shown** in the UI (trust brand, not fine print).
- **Honesty over fake data** — unbuilt endpoints return `501`; gaps are drawn, never fabricated.
- **Gemini only, one router, one tenancy model** — all LLM calls go through the single router; no other
  provider; don't fork auth/tenancy across services.
- **Deterministic *data*, not deterministic *logic*** — "deterministic" describes the **data plane**
  (API-based connectors → reproducible, sourced figures). Answer quality, routing, and orchestration come
  from Gemini / multi-agent flows — **never hand-rolled keyword/heuristic rules** (no keyword router anywhere).

## Run the whole stack (Docker — recommended)

```bash
for f in env/*.env.example; do cp "$f" "${f%.example}"; done   # split config by topic (env/README.md)
# fill env/gemini.env (GOOGLE_API_KEY) + env/data-keys.env (OPENDART/ECOS/FRED…). A root .env also works.
docker compose up --build     # datasets :8000 · gateway :8010 · rag :8002 · agent :8003 · studio :8004 · web :3000 · admin :8005 (+ worker)
docker compose ps             # health of each service
docker compose logs -f studio-api
docker compose stop worker    # pause ALL automatic ingestion (the Procrastinate cron sweeps)
docker compose down           # stop  (add -v to ALSO wipe Postgres data + volumes)
```

Open <http://localhost:3000> and ask "삼성전자 최근 실적" — the agent answers with sources. The browser
never holds a platform key: web BFF (Auth.js session) → studio-api (tenant key) → agent-engine → tools via
the metered gateway. `AUTH_DEV_LOGIN=true` enables local login without Google. Rebuild one service after a
change: `docker compose up -d --build agent-engine`.

Drive the data plane through the gateway directly:
```bash
# POST /admin/tenants -> /projects -> /keys -> /activations (connector_id), then:
curl -H "X-API-KEY: vgk_..." "http://127.0.0.1:8010/company/facts?ticker=AAPL&market=US"
```

## Run the tests

**Everything in one command — only Docker is required** (no host `uv`/`npm`/`pytest`). Unit suites run in
the `uv` image, the web build is a docker build, and the e2e + eval drive `docker compose`:

```bash
bash scripts/test_all.sh          # GOOGLE_API_KEY in .env enables the live e2e + eval; else they skip cleanly
```

Or run a layer on its own — the **docker** harnesses bring the stack up themselves:

```bash
bash scripts/coverage.sh          # EVERY catalog tool called through the gateway — coverage matrix
bash scripts/e2e.sh               # Gemini planner, whole product chain — skips (exit 2) without a key
bash scripts/e2e_functional.sh    # REAL data + MCP tool calls + semantic RAG (oss-cpu) + entitlement — no key
GOOGLE_API_KEY=... bash scripts/e2e_live.sh   # REAL Gemini: grounded, cited answers

# Quality eval (needs the stack up: `docker compose up -d` first; skips without GOOGLE_API_KEY):
python3 eval/run_eval.py          # 96 scenarios; scores tool-use, grounding, citations, guardrails (+ Gemini judge)

# Unit tests in docker (one service) — no host uv needed:
docker run --rm -v "$PWD/agent-engine:/app" -w /app ghcr.io/astral-sh/uv:python3.11-bookworm-slim \
  sh -lc "uv run --extra dev pytest -q"
```

**~900 unit/component tests** pass + the web build. The **quality eval** (`eval/run_eval.py`, LLM-judge
rubric — see [`eval/RUBRIC.md`](./eval/RUBRIC.md)) runs before every push and must stay above the bar; every
new tool / endpoint / feature adds a scenario. Per-service test totals live in `docs/ROADMAP.md` §13.

The e2e harnesses:
- **`e2e.sh`** — Gemini planner, the whole chain (catalog → tenant → entitlement → data plane + RAG via
  gateway → metering → MCP → studio chat). Skips cleanly (exit 2) without `GOOGLE_API_KEY`.
- **`e2e_functional.sh`** — real upstream numbers (Apple facts, a live AAPL close, Samsung's KRW revenue,
  the BOK rate), MCP real tool calls + schema + entitlement, and **real semantic RAG** (oss-cpu) with provenance.
- **`e2e_live.sh`** — the real Gemini planner answering grounded, cited questions (a "should I buy?" prompt refused).

## Environment (Gemini only; never commit secrets)

```
GOOGLE_API_KEY=                      # one key for all Gemini use — enables the gemini planner + live tests
AGENT_LLM_BACKEND=gemini             # Gemini-only (stub removed); default gemini, requires GOOGLE_API_KEY
AUTH_DEV_LOGIN=true                  # local login without Google
DATABASE_URL=                        # runtime: Postgres (compose sets per-service DBs); SQLite only for unit tests
OPENDART_API_KEY= / ECOS_API_KEY= / FRED_API_KEY=    # free KR/US data keys
RAG_EMBEDDING_BACKEND=hash|oss-cpu|oss-gpu|tei|gcp
RAG_RERANKER_BACKEND=none|oss-cpu|oss-gpu|tei|gcp
RAG_VECTOR_STORE=memory|pgvector
X-Admin-Token (dev: dev-admin-token) # control-plane admin
```
