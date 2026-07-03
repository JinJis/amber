# GEMINI.md — Investment-Agent Data Platform

> Engineering rules for Jetski in this repo. This is the active product.
> **The legacy ValueGraph engine (`services/`, `apps/`, CVE, Deep-Research acquisition) has been removed**
> — not a dependency.
>
> **⚠️ Roadmap & UX docs are being rewritten (2026-06-23).** The old roadmap and UX-design docs
> (`ROADMAP.md`, `UX_SPEC.md`, `DESIGN_SYSTEM.md`, `wireframes/`) have moved to
> [`docs/deprecate/`](./docs/deprecate/) — reference only, **not** the source of truth. A **new roadmap**
> (full UX overhaul + MVP) will land in `docs/`; pull tasks from it once it exists.
>
> **Docs map (read before building):**
> - **How the services fit together (current state):** [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
> - **Product idea / vision:** [`docs/IDEA.md`](./docs/IDEA.md)
> - **Data expansion plans:** [`docs/DATA_EXPANSION.md`](./docs/DATA_EXPANSION.md)
> - **The plan (new):** *being authored — full UX overhaul + MVP. Until it lands, the deprecated
>   `docs/deprecate/ROADMAP.md` is history only, not a task source.*
>
> One task at a time, **one task per PR**; tag the task id in branch/commits/PR. Don't mark done until
> every acceptance criterion + the Definition of Done (§7) passes.

---

## 1. The product in one paragraph

A **personal research desk**: the user staffs **standing analysts** (agents) on their own **watchlists**
of companies. Every analyst works **only from licensed, point-in-time, fully-cited data**, renders
figures as **live, sourced artifacts**, and **pushes what changed before being asked** (schedule +
disclosure calendar). It is *not* a chatbot — the differentiators are **trust by construction**,
**pull→push**, and a **clone-from-others ecosystem**. (The new UX spec is being authored; the old one
is in `docs/deprecate/UX_SPEC.md` for reference.)

## 2. Architecture invariants — never violate

These hold across every service; breaking one fails review.

1. **No number without a source.** Every datum/chunk/artifact/brief carries `source` + `as_of` +
   `freshness` (+ `confidence`/interval where derivable). Unsourced → it doesn't ship to the user.
2. **The gateway is the only path to data.** Agents, MCP, and external callers reach `datasets`/`rag`
   **only through the control-plane gateway**, which enforces auth → entitlement → rate-limit →
   meter/audit. Never call the data plane directly from a product service.
3. **Entitlement = activation.** A project may use a connector iff it activated it. Don't bypass.
4. **Keys stay server-side.** Platform upstream keys and the tenant key live server-side (studio-api
   holds the tenant key; the browser only has an Auth.js session). Never client-side.
5. **No forecasting / no advice.** Forecasts, price targets, momentum, scored feeds, buy/sell advice are
   **refused at the agent boundary** — and the refusal/label is **shown** in the UI (it's the trust
   brand, not fine print). The Live Context Feed shows raw items only.
6. **Honesty over fake data.** Unbuilt endpoints return `501`; gaps are **drawn**, never fabricated or
   silently averaged. Reconcile/flag conflicts; don't overwrite.
7. **Gemini only, one router, one tenancy model.** All LLM calls go through the single router; no other
   provider; don't fork auth/tenancy across services.
8. **Two surfaces over one core stay consistent:** the **connector manifest/catalog**
   (`datasets/app/connectors/`) is the single source REST docs, MCP tools, RAG registration, entitlement,
   metering, and the agent's tool list all derive from. Touch the manifest, not forked copies.
9. **Deterministic *data*, not deterministic *logic*.** "Deterministic" describes the **data plane** —
   connectors are API-based, so figures are reproducible and **always accurately sourced**. It is **not**
   license to hardcode reasoning. **Answer quality, routing, and orchestration come from Gemini and
   multi-agent flows — never hand-rolled keyword/heuristic rules.** The platform is **Gemini-only**:
   there is no keyword router anywhere — routing, clarification, decomposition, and answer quality all
   come from Gemini. When a task needs judgment (difficulty, extraction, synthesis, review), reach for an
   LLM/agent, not an `if`-ladder.

## 3. Services (ports = host:container; one `docker compose`, one shared `.env`)

| Service | Host port | Package | Role |
|---|---|---|---|
| `datasets` | 8000 | `app` | data plane: US+KR connectors + ingestion store + `/catalog` |
| `control-plane` | 8010→8001 | `controlplane` | the **gateway** + tenants/keys/activations admin |
| `rag` | 8002 | `rag` | provenance-first chunk→embed→retrieve→rerank |
| `agent-engine` | 8003 | `agentengine` | guardrail→plan (Gemini)→tool loop→citations; `/agent/chat` SSE |
| `studio-api` | 8004 | `studioapi` | Google user→tenant provisioning; conversations; **holds tenant key**; agents/prompts/(watchlists/briefs) |
| `web` | 3000 | Next.js | chat UI + builder + prompt library; `/api/*` BFF (Auth.js session only) |
| `admin` | 8005 | — | out-of-band CRUD/ops console over service DBs (not in the request path) |
| `mcp` | stdio | `mcpserver` | one tool per catalog resource, routed through the gateway |

Request flow (one chat turn): browser → web BFF (session) → studio-api (tenant key) → agent-engine →
**gateway** (entitle+meter) → datasets/rag → upstreams. Full diagram in `docs/ARCHITECTURE.md` §2.

## 4. Where things live (don't fork)
- **Connector + its manifest:** `datasets/app/connectors/` and `datasets/app/routers/`. New data → new
  connector + manifest entry (an integrity test asserts every manifest path is a real route).
- **Tenancy/entitlement/metering:** `control-plane/` (`controlplane`). Gateway is the enforcement point.
- **Agent loop / planner / guardrails:** `agent-engine/` (`agentengine`). Planner via `AGENT_LLM_BACKEND`.
- **Product data model** (users, conversations, agents, prompts, and the new **watchlists / standing
  analysts / briefs / pinned artifacts**): `studio-api/studioapi/models.py`. Extend here; mirror the
  existing **prompt-import pattern** (`community` + `source_id` + idempotent clone) for analyst cloning.
- **UI:** `web/` — chat, builder modal, prompt modal, BFF routes under `web/app/api/`. Read
  `/mnt/skills/public/frontend-design/SKILL.md` before UI work; **never render the graph with DOM nodes**
  (WebGL/R3F + instanced meshes); **no `localStorage`/`sessionStorage`** in preview/artifact contexts.

## 5. Commands
```bash
cp .env.example .env                 # free keys (OPENDART/ECOS/FRED); AUTH_DEV_LOGIN=true; GOOGLE_API_KEY for Gemini
docker compose up --build            # datasets:8000 gateway:8010 rag:8002 agent:8003 studio:8004 web:3000 admin:8005
docker compose up -d --build web     # rebuild one service after a change
docker compose logs -f studio-api    # follow a service
docker compose down                  # stop (-v also wipes SQLite/volumes)

# Tests — only Docker required (no host uv/npm). See README "Run the tests".
bash scripts/test_all.sh             # everything; live e2e+eval need GOOGLE_API_KEY (else skip cleanly)
bash scripts/coverage.sh             # EVERY catalog tool through the gateway
bash scripts/e2e.sh                  # Gemini planner, whole product chain; skips (exit 2) without a key
bash scripts/e2e_functional.sh       # real upstream data + MCP + semantic RAG (oss-cpu)
GOOGLE_API_KEY=... bash scripts/e2e_live.sh   # real Gemini, grounded+cited
python3 eval/run_eval.py             # quality eval (stack up first; skips without GOOGLE_API_KEY)
```
**Definition of Done for a task:** its acceptance criteria pass · unit tests added/updated for the
service(s) touched · the relevant e2e/coverage harness still green · **the quality eval
(`python3 eval/run_eval.py`, deep-model rubric — see `eval/RUBRIC.md`) run before push and still above the
bar; if the task adds a tool / endpoint / feature, add an eval scenario (with `criteria`) for it** ·
the new roadmap's test totals + the task status updated in the same PR.

## 6. Environment (Gemini only; never commit secrets — document new keys in `.env.example`)
```
GOOGLE_API_KEY=                      # one key for all Gemini use — enables the gemini planner + live tests
AGENT_LLM_BACKEND=gemini             # Gemini-only (stub removed); default gemini, requires GOOGLE_API_KEY
AUTH_DEV_LOGIN=true                  # local login without Google
DATABASE_URL=                        # SQLite by default; Postgres in prod
OPENDART_API_KEY= / ECOS_API_KEY= / FRED_API_KEY=    # free KR/US data keys
RAG_EMBEDDING_BACKEND=hash|oss-cpu|oss-gpu|tei|gcp
RAG_RERANKER_BACKEND=none|oss-cpu|oss-gpu|tei|gcp
RAG_VECTOR_STORE=memory|pgvector
X-Admin-Token (dev: dev-admin-token) # control-plane admin
```
Model IDs are env-overridable and Gemini-only; verify exact IDs/SDK details against current Google docs,
not memory.

## 7. Working style
- **Pull the next task from the new roadmap** (being authored — full UX overhaul + MVP); read the UX
  spec section it implements. The old roadmap/UX docs in `docs/deprecate/` are history, not a task source.
- **One task per PR.** Prefer iterative refinement over rewrites; preserve working code and tests.
- **Keep docs in sync in the same PR:** if architecture drifts, update `docs/ARCHITECTURE.md`; if you
  finish/advance a task, update its status + test totals in the new roadmap.
- **Do:** tag every figure (source+as_of+next_update+freshness+confidence) · route all data through the
  gateway · draw gaps & show freshness · show the guardrail label · reuse the manifest/catalog and the
  prompt-import clone pattern.
- **Don't:** build prediction/forecasting · expose unsourced numbers · call the data plane outside the
  gateway · put keys client-side · render the graph with DOM nodes · use a non-Gemini model · fork the
  router/tenancy/schema.
