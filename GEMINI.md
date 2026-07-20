# GEMINI.md — Investment-Agent Data Platform

> Engineering rules for Gemini in this repo (mirrors `CLAUDE.md` — keep the two in sync).
>
> **The product is feature-frozen (2026-07-20) — no new feature development.** Work is limited to
> **maintenance**: bug fixes, operational/infra hardening, and keeping the current-state docs accurate.
> Don't build new features, screens, connectors, or roadmap items. If a request looks like new-feature
> work, **flag it** instead of building it.
>
> **The legacy ValueGraph engine (`services/`, `apps/`, CVE, Deep-Research acquisition) has been removed**
> — not a dependency.
>
> **The product roadmap and every feature / UX / idea / audit spec are retired to
> [`docs/deprecate/`](./docs/deprecate/) — reference/history only. Do NOT pull tasks or build new work
> from anything in that folder.** (Retired: the roadmap `ROADMAP_v2.md`, the UX spec `UX_SPEC_v2.md`, and
> `HISTORY_LAB_SPEC` · `QUALITY_SPEC` · `DATA_EXPANSION` · `VIRAL_SPEC` · `NOTEBOOK_SPEC` · `IDEA` ·
> `USER_TODO` (owner launch/ops checklist) · `SCALING_AUDIT`.)
>
> **Docs map (live — the only source of truth now):**
> - **How the services fit together (current state):** [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
> - **GCP 배포·운영 (VM·비용·백업·런북):** [`docs/INFRA.md`](./docs/INFRA.md) (+ [`deploy/`](./deploy/))
> - **브랜드 (finnote — finance+footnote; 수면 위 지느러미 로고·토큰; 로고 경로 데이터 수정 금지):** [`docs/branding/`](./docs/branding/) — 소스 오브 트루스는 `brand.css`·`Logo.tsx`·SVG 4종(`finnote-mark`·`finnote-mark-mono`·`finnote-icon`·`favicon`); 웹은 `web/app/brand.css`·`web/components/Logo.tsx`로 복사돼 있음(둘 다 동기 유지). 디자인 템플릿 전체 명세: [`docs/finnote-design-template_final.html`](./docs/finnote-design-template_final.html)
>
> The **대시보드(board)** and **알림봇(alert bot)** surfaces stay feature-flagged off by default
> (`FEATURE_BOARD`/`FEATURE_ALERTS`) — don't extend them (or anything else: the product is feature-frozen).

---

## 1. The product in one paragraph

A **personal research desk**: the user staffs **standing analysts** (agents) on their own **watchlists**
of companies. Every analyst works **only from licensed, point-in-time, fully-cited data**, renders
figures as **live, sourced artifacts**, and **pushes what changed before being asked** (schedule +
disclosure calendar). It is *not* a chatbot — the differentiators are **trust by construction**,
**pull→push**, and a **clone-from-others ecosystem**.

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
   metering, the agent's tool list, **and the builder's user-facing categories** all derive from. Every
   Resource carries a `category` (set in `catalog.py`'s `_CATEGORY` map — load fails if any tool is
   uncategorized); users pick **individual tools grouped by category**, never whole APIs. Touch the
   manifest, not forked copies.
9. **Deterministic *data*, not deterministic *logic*.** "Deterministic" describes the **data plane** —
   connectors are API-based, so figures are reproducible and **always accurately sourced**. It is **not**
   license to hardcode reasoning. **Answer quality, routing, and orchestration come from Gemini and
   multi-agent flows — never hand-rolled keyword/heuristic rules.** The platform is **Gemini-only**:
   there is no keyword router anywhere — routing, clarification, decomposition, and answer quality all
   come from Gemini. When a task needs judgment (difficulty, extraction, synthesis, review), reach for an
   LLM/agent, not an `if`-ladder.

## 3. Services (ports = host:container; one `docker compose`; config split in `env/*.env`, see `env/README.md`)

| Service | Host port | Package | Role |
|---|---|---|---|
| `datasets` | 8000 | `app` | data plane: US+KR connectors + ingestion store + `/catalog` |
| `worker` | — | `app` (`app.queue`) | Procrastinate worker: runs the cron **sweeps** + processes ingestion jobs (Postgres-backed queue; no Redis) |
| `control-plane` | 8010→8001 | `controlplane` | the **gateway** + tenants/keys/activations admin |
| `rag` | 8002 | `rag` | provenance-first chunk→embed→retrieve→rerank |
| `agent-engine` | 8003 | `agentengine` | guardrail→plan (Gemini)→tool loop→citations; `/agent/chat` SSE |
| `studio-api` | 8004 | `studioapi` | Google user→tenant provisioning; conversations; **holds tenant key**; agents/(watchlists/briefs) |
| `web` | 3000 | Next.js | chat UI + builder; `/api/*` BFF (Auth.js session only) |
| `admin` | 8005 | — | out-of-band CRUD/ops console over service DBs (not in the request path) |
| `mcp` | stdio | `mcpserver` | one tool per catalog resource, routed through the gateway |

Request flow (one chat turn): browser → web BFF (session) → studio-api (tenant key) → agent-engine →
**gateway** (entitle+meter) → datasets/rag → upstreams. Full diagram in `docs/ARCHITECTURE.md` §2.

## 4. Where things live (don't fork)
- **Connector + its manifest:** `datasets/app/connectors/` and `datasets/app/routers/`. New data → new
  connector + manifest entry (an integrity test asserts every manifest path is a real route).
- **Tenancy/entitlement/metering:** `control-plane/` (`controlplane`). Gateway is the enforcement point.
- **Agent loop / planner / guardrails:** `agent-engine/` (`agentengine`). Planner via `AGENT_LLM_BACKEND`.
- **Product data model** (users, conversations, agents, and the **watchlists / standing
  analysts / briefs / pinned artifacts**): `studio-api/studioapi/models.py`. Extend here; mirror the
  **idempotent-clone pattern** (`orm_helpers.idempotent_clone` — `community` + `source_id`) for
  analyst cloning.
- **UI:** `web/` — chat, builder modal, BFF routes under `web/app/api/`. Read
  `/mnt/skills/public/frontend-design/SKILL.md` before UI work; **never render the graph with DOM nodes**
  (WebGL/R3F + instanced meshes); **no `localStorage`/`sessionStorage`** in preview/artifact contexts.

## 5. Commands
```bash
for f in env/*.env.example; do cp "$f" "${f%.example}"; done   # split config by topic (env/README.md);
                                     # fill env/gemini.env (GOOGLE_API_KEY) + env/data-keys.env (OPENDART/ECOS/FRED…).
                                     # A single root .env still works (compose reads both; env/* override).
docker compose up --build            # datasets:8000 gateway:8010 rag:8002 agent:8003 studio:8004 web:3000 admin:8005 (+ worker)
docker compose stop worker           # pause ALL automatic ingestion (the Procrastinate cron sweeps live here)
docker compose up -d --build web     # rebuild one service after a change
docker compose logs -f studio-api    # follow a service
docker compose down                  # stop (-v also wipes the Postgres data + volumes)

# Tests — only Docker required (no host uv/npm). See README "Run the tests".
bash scripts/test_all.sh             # everything; live e2e+eval need GOOGLE_API_KEY (else skip cleanly)
bash scripts/coverage.sh             # EVERY catalog tool through the gateway
bash scripts/e2e.sh                  # Gemini planner, whole product chain; skips (exit 2) without a key
bash scripts/e2e_functional.sh       # real upstream data + MCP + semantic RAG (oss-cpu)
GOOGLE_API_KEY=... bash scripts/e2e_live.sh   # real Gemini, grounded+cited
python3 eval/run_eval.py             # quality eval (stack up first; skips without GOOGLE_API_KEY)
```
**Definition of Done for a change:** its acceptance criteria pass · unit tests added/updated for the
service(s) touched · the relevant e2e/coverage harness still green · **the quality eval
(`python3 eval/run_eval.py`, LLM-judge rubric — see `eval/RUBRIC.md`) run before push and still above the
bar.** Keep the live docs (`ARCHITECTURE.md`/`INFRA.md`) in sync in the same PR when behavior drifts.

## 6. Environment (Gemini only; never commit secrets — document new keys in the right `env/*.env.example`)
```
GOOGLE_API_KEY=                      # one key for all Gemini use — enables the gemini planner + live tests
AGENT_LLM_BACKEND=gemini             # Gemini-only (stub removed); default gemini, requires GOOGLE_API_KEY
AUTH_DEV_LOGIN=true                  # local login without Google
DATABASE_URL=                        # runtime: Postgres (compose sets per-service DBs on the postgres svc); SQLite only for unit tests
OPENDART_API_KEY= / ECOS_API_KEY= / FRED_API_KEY=    # free KR/US data keys
RAG_EMBEDDING_BACKEND=hash|oss-cpu|oss-gpu|tei|gcp
RAG_RERANKER_BACKEND=none|oss-cpu|oss-gpu|tei|gcp
RAG_VECTOR_STORE=memory|pgvector
X-Admin-Token (dev: dev-admin-token) # control-plane admin
```
Model IDs are env-overridable and Gemini-only; verify exact IDs/SDK details against current Google docs,
not memory.

## 7. Working style
- **Maintenance only — no new features.** Fix bugs, harden ops/infra, keep the live docs accurate. Prefer
  the smallest change that fixes the issue. If a request would add a feature/screen/connector or resurrect
  a retired roadmap item, **flag it** rather than building it.
- **Prefer iterative refinement over rewrites;** preserve working code and tests.
- **Keep the live docs in sync in the same PR:** if architecture drifts, update `docs/ARCHITECTURE.md`;
  if deploy/ops change, update `docs/INFRA.md`. (There is no roadmap to update — it's retired.)
- **Do:** tag every figure (source+as_of+next_update+freshness+confidence) · route all data through the
  gateway · draw gaps & show freshness · show the guardrail label · reuse the manifest/catalog.
- **Don't:** build prediction/forecasting · expose unsourced numbers · call the data plane outside the
  gateway · put keys client-side · render the graph with DOM nodes · use a non-Gemini model · fork the
  router/tenancy/schema · **build new features (the product is feature-frozen).**
