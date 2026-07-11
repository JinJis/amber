# Backend Refactoring Plan (RF-01 … RF-18)

> **✅ COMPLETE — all 18 tasks done** (Phase 0–3), each its own commit on `development`, every
> touched service's unit suite green (datasets 147 · agent-engine 99 · rag 20 · studio-api 51 ·
> control-plane 14 · admin 20 · mcp 10) and key paths live-verified. No behavior change. A recurring
> theme: several audit findings were **overstated or wrong** (RF-06 caches already shared, RF-16
> gateway already clean, RF-17 suggestions would harm deliberate design, RF-18 gaps mostly already
> tested) — each was verified against the real code before acting, so no churn was spent on
> non-problems.

> **Goal:** reduce structural debt in the backend services **without changing behavior**.
> Pure structure/maintainability work — every task ships with the touched service's unit tests
> green + the relevant e2e/coverage harness still passing (Definition of Done, CLAUDE.md §5).
>
> **Process:** one task per PR/commit; **tag the `RF-id`** in the branch/commit/PR. Pull tasks in
> phase order (0 → 1 → 2 → 3). Update the **Status** column in the same change. This doc is the
> task source for the backend refactor — derived from a 6-service parallel audit (2026-06-25).

## Guardrails — refactors must PRESERVE these invariants (CLAUDE.md §2)

A refactor that touches any of these must keep the behavior identical:

- **Manifest/catalog is the single source of truth** — REST docs, MCP tools, RAG registration,
  entitlement, metering, agent tool list, builder categories all derive from it. Every Resource
  carries `category` + `cadence` (load fails if missing). Don't fork it.
- **Gateway is the only path to data** (auth → entitlement → rate-limit → meter/audit). Don't bypass
  or split the enforcement point. Entitlement = activation.
- **Keys server-side only.** **Gemini-only, one router** — no other provider, no forked tenancy.
- **No keyword/heuristic/if-ladder judgment** — routing, clarification, decomposition, difficulty,
  guardrail intent are LLM-judged. A refactor must not (re)introduce keyword logic.
- **No number without a source** (`source`+`as_of`+`freshness`+`cadence`); unbuilt → 501, never fabricate.
- **No forecasting/advice** (refused at the agent boundary, label shown). Alerts/briefs stay facts-only.
- **Search stays fail-safe** (a reranker/store failure falls back, never 500s the user).

## Status legend

`TODO` · `WIP` · `DONE` · `BLOCKED` · `DROPPED`. Severity = maintainability impact. Effort:
S(<1h) · M(half-day) · L(multi-day).

---

## Phase 0 — Quick wins (safe, small, do first)

| ID | Task | Service | Sev | Eff | Status |
|---|---|---|---|---|---|
| **RF-01** | Dead-code + config-bug sweep. **DONE:** removed duplicate KIS keys in `config.py`; deleted ~90 LOC of legacy keyword-routing dead code from agent-engine `routing.py` (kept only the live `resolve_ticker`/`_user_text`). **Verified NOT dead (kept):** `opendart.as_reported()` (KR route `financials.py:108` calls it — honest empty per invariant #6), `store/ingest.py` `_STATEMENTS`/`_SKIP` (used by `ingest_ticker`/KR bulk), `jobs.py` type hints (already present), `evidence.py` (already documented). | datasets, agent-engine | high | S | DONE |
| **RF-02** | Shared provider number-parsing: new `providers/_parse_utils.py` (`parse_int`/`parse_float`, comma-aware) replacing `_num` (sec_edgar + fmp), `_i`/`_f` (kis) via import-alias (zero call-site churn). | datasets | high | S | DONE |
| **RF-03** | Single provenance field tuple: collapsed `_PROV` (`store.py`) + `_PROVENANCE` (`models.py`) into one shared `models.PROVENANCE_FIELDS`. | rag | med | S | DONE |

## Phase 1 — Cross-cutting foundations (highest value; other refactors build on these)

| ID | Task | Service | Sev | Eff | Status |
|---|---|---|---|---|---|
| **RF-04** | Router toolkit `routers/_common.py` (`validate_interval`/`validate_period` + `INTERVALS`/`PERIODS`, `gather_best_effort`, `tickers_response`). Migrated 8 routers (prices, technical, financials, metrics, gurus, company, filings, earnings) — removed the duplicated `_INTERVALS`/`_PERIODS`/`_check_period`, the `_one`+gather+filter pattern (×3), and the `list_tickers→TickersResponse` one-liners (×5). Verified live: identical 400 messages + responses. | datasets | high | M | DONE |
| **RF-05** | `jobs.run_ticker_job(kind, market, spec, tickers, ingest_one)` — the per-ticker best-effort job lifecycle (start→progress→finish, -1 on failure, success/error finalize, to_thread-wrapped). Migrated `run_prices_ingest` + `run_corp_actions_ingest` onto it (byte-identical behavior). Split `prices_ingest.py` → `prices_ingest.py` (prices + `price_coverage`) + `corp_actions_ingest.py` + shared `_ingest_helpers.py`. **news/filing left as-is** — their job shapes differ (single-pass / total-sum, no failed-note), migrating would change the admin job record. Verified live. | datasets | high | M | DONE |
| **RF-06** | Upstream loader cache. **Audit premise was mostly wrong**: SEC `_ticker_index` + DART `_corp_map` *already* share `app.cache.TTLCache.get_or_set`; only KIS's token rolled its own (dict + lock + 23h TTL) because `TTLCache` loaded the factory **outside** the lock (a cold-cache thundering herd KIS couldn't afford at ~1/min issuance). **Did:** upgraded `TTLCache` to **single-flight per key** (factory runs once under concurrent cold access; different keys still parallel), then migrated the KIS token onto a dedicated `TTLCache(23h-60s)` — dropped the bespoke dict/lock/double-check. Upstream errors already go through `app.errors` (`upstream_error`) + the `fetch_*` helpers — left as-is. Verified live (SEC cached path) + a single-flight unit test. | datasets | high | M | DONE |
| **RF-07** | Catalog single-source: merged the two parallel `_CATEGORY` + `_CADENCE` maps (60 entries each) into one `_RESOURCE_META: (cid,name) → (Category, Cadence)` and the two near-identical `_apply_*` fns into one `_apply_resource_meta` (keeps the load-time missing/stale assertion). Adding a tool now touches 2 places, not 3, and category+cadence sit on one line (can't drift). Chose the meta-map merge over inlining on all 60 Resources to avoid risky transcription edits to invariant #8's single source. Merge generated programmatically; **proved byte-identical** (60 tools' category+cadence unchanged) in-process + live `/catalog`. | datasets | med | M | DONE |

## Phase 2 — God-file structural splits

| ID | Task | Service | Sev | Eff | Status |
|---|---|---|---|---|---|
| **RF-08** | `artifacts.py`: replaced the 367-line, 19-branch `_build_artifacts` `if name.endswith(...)` ladder with a `tool-suffix → handler` registry (`_BUILDERS`) + a 20-line dispatcher + 20 named, self-guarded handlers (each returns `[]` when the shape doesn't fit). Adding a chartable tool is now one handler + one registry row. Behavior proven by the suite (all 23 tool suffixes tested → 99 pass). The `enrich_artifacts()` extraction moved to **RF-10** (it touches chat/agent call sites). | agent-engine | high | M | DONE |
| **RF-09** | Split `agent.py` 809→282: extracted `intake.py` (235: `TaskIntake`, `analyze_task` + guardrail, intake prompt/schema) and `enrichment.py` (351: `suggest_followups`/`refine_evidence` + their prompts/schemas) out of the orchestration loop. Used the file's existing **back-compat re-export** pattern (agent.py re-exports the moved names) → **zero importer churn** across chat/main/orchestrator/tests. Prompts/schemas **co-located with their cluster** (cohesion) rather than a central `prompts.py`. `results.py` chat↔agent dedup → **RF-10** (chat.py work). agent-engine 99 pass, 0 breakage. | agent-engine | high | L | DONE |
| **RF-10** | Extracted `artifacts.enrich_artifacts(artifacts, history, task, model, backend, *, annotate_timeout=None)` — the PH-VIZ post-loop sequence (markers + price lines → technical overlays → bounded Gemini annotation) that `run_agent` and the chat stream both ran inline (the dedup deferred from RF-08/09). Streaming passes `annotate_timeout` so annotation never delays `done`; `run_agent` awaits directly — same behavior, one definition (also the bounded-Gemini wrapper for the annotate path). **Consciously skipped** the SSE event-builder + streaming citation/artifact-assembly dedup: cosmetic / high-risk against the live SSE contract, and the two assembly blocks genuinely diverge (Citation objects vs `_citations()`+args). agent-engine 99 pass. | agent-engine | med | M | DONE |
| **RF-11** | Ownership DRY: new `orm_helpers.py` with `check_owned(entity, …)` + `get_owned(db, model, id, email, detail, *, allow_global=False)` — the load-by-PK + 404-unless-owned guard that every CRUD route repeated. Migrated 10 sites (agents update/delete, alerts `_owned`, board rename/delete/update_pin/annotate/refresh/unpin, main run_stream). **Scoped out** (risk/value): the full list/get/create/update/delete **route factory** (restructures FastAPI routes — risky, routes have varied logic), `JsonColumn`/`to_dict`/`safe_json_list` (the `_out`s carry module-specific computed fields; `json.loads(x or "[]")` is a clear 1-liner — marginal). studio-api 51 pass. | studio-api | high | M | DONE |
| **RF-12** | Split `alerts.py` 565→ routes (304) + `alerts_render.py` (295, pure domain): moved `render_message`/`fire_alert`/`compute_next_fire` + their helpers (`_board_periodic_specs`/`_fetch_artifact`/`_widget_fresh_payload`/`_factual_guard`/`_target_label`) and the `_TRIGGER_META`/`_FORBIDDEN`/`TRIGGER_TYPES` constants out of the HTTP layer — the domain now imports neither FastAPI nor the routes, so it's unit-testable on its own. alerts.py imports+re-exports the names scheduler.py (`fire_alert`) and the tests (`compute_next_fire`/`render_message`) use → zero importer churn. Dropped the now-unused imports (httpx/logging/settings/timedelta/PinnedArtifact). `_TRIGGER_META` already IS the single trigger-metadata map (the "TRIGGERS table"); render templates stay cohesive inside `render_message`. studio-api 51 pass. | studio-api | high | L | DONE |
| **RF-13** | Extracted `orm_helpers.idempotent_clone(db, model, source, user_email, *, fields, overrides)` from `prompts.import_prompt` — the community + `source_id` + idempotent clone pattern CLAUDE.md says to reuse for analyst/agent cloning. Returns `(entity, created)` (caller commits). Rewired `import_prompt` onto it (one caller today; the reusable primitive is now in place). studio-api 51 pass. | studio-api | med | M | DONE |
| **RF-14** | Split `admin/main.py` 986→709: extracted `state.py` (the reflected-DB registries `DB_STATUS`/`ENGINES`/`TABLES` + `_mount_database` + the import-time mount loop + `_table_counts`/`_query`/`_has` — the stateful layer, now off the request handlers), `clients.py` (`_safe_get`/`_ok` httpx fetch helpers), and `db_browser.py` (the `/db/*` styled-CRUD section as its own `APIRouter`, ~240 LOC). main.py re-exports `DB_STATUS` (test imports it) and `app.include_router`s the browser. The page routes (overview/catalog/pipelines/queue/upstream/data/users + ops) stay cohesive in main.py; their HTML-builder dedup is RF-15. Verified live (Postgres reflection → 29 tables, /db + overview render). admin 20 pass. | admin | high | M | DONE |
| **RF-15** | Moved the `tile` component builder (8 uses) into the `views.py` component lib (next to `badge`/`sdot`/`progress`/`page`), and consolidated the status→class/label maps there: `JOB_STATUS_CLASS` (deduped the inline `{success/error/running}` dict, 2×), `QUEUE_STATUS_CLASS`/`QUEUE_STATUS_LABEL`, `UPSTREAM_DOT`/`UPSTREAM_LABEL` — all status presentation now lives in one place. Deferred the card/table-builder extraction: the cards are heterogeneous (varied content), so a generic builder adds indirection without much dedup; Jinja2 is a separate decision. admin 20 pass + live-smoked (/, /queue, /upstream, /pipelines render). | admin | med | M | DONE |

## Phase 3 — Gateway / RAG hardening + test gaps

| ID | Task | Service | Sev | Eff | Status |
|---|---|---|---|---|---|
| **RF-16** | **Audit premise mostly overstated** — `gateway()` was already a clean numbered pipeline (0-4) and the entitlement logic already lives in `catalog_index` (`candidate_connectors`/`is_governed`/`cost_units`). **Did:** named the audit actions (`ACTION_ACCESS`/`DENIED`/`ERROR` constants, was bare literals) and extracted `_resolve_entitlement(project_id, key_id, method, path, market)` — the invariant #2/#3 "which activated connector serves this" decision — out of `gateway()` into a named, isolated function (gateway now reads auth→resolve_entitlement→ratelimit→proxy). **Skipped:** `byo_credentials` validation (stored but never read by the gateway — a feature gap, not a refactor target) and the rate-limit TTL rework (the opportunistic-at-10k cleanup is fine for the single-process limiter). control-plane 13 pass. | control-plane | med | M | DONE |
| **RF-17** | **Audit premise rejected** — every structural suggestion would harm a *deliberate* design property: a central BackendRegistry **reduces cohesion** (the 3 `@cache` factories sit with their own backend impls); a shared filter-matcher is **impossible** (MemoryStore is a Python predicate, PgVectorStore is a SQL WHERE — can't share code); startup eager-init/validation **fights the lazy, no-key-tolerant startup** (`get_embedder` is Gemini-only and must not need a key at boot) and the **fail-safe reranker** (validating it at boot defeats graceful degradation). **Did the safe, design-respecting bits:** documented the tenant/filter semantics authoritatively on the `VectorStore.search` Protocol (one spec both impls conform to) and named the exception type in the reranker-failure log (config/auth vs transient). Store-parity is covered as a **test in RF-18**. rag 20 pass. | rag | med | M | DONE |
| **RF-18** | **Most claimed gaps were already covered** — verified RAG tenant isolation (`test_tenant_cannot_see_another_tenants_docs` + global-visible + not-in-provenance), reranker fail-safe (`test_search_survives_reranker_failure`), and store market-filter all exist; ingest job lifecycle is covered by RF-05's `run_ticker_job` test. **Added the 2 real gaps:** control-plane `test_ratelimit_key_isolation_and_window_reset` (key A exhausting doesn't affect key B; minute-boundary resets — faked clock) and MCP `test_build_tools_covers_every_catalog_resource` (exactly one tool per catalog resource, nothing hand-added/dropped — invariant #8). Memory↔Pg parity stays e2e (the unit suite is MemoryStore-only); the RF-17 Protocol docstring is the shared spec. mcp 10 + control-plane 14 pass. | all | med | L | DONE |

---

## Deferred / rejected (value vs. risk)

- **asyncpg migration** (async DB everywhere) — behavior-risky, large scope; not a pure refactor.
- **hypothesis property/fuzz tests** — net-new test infra, not refactoring.
- **Jinja2 for the admin panel** — medium value, real effort; revisit after RF-14/15 land.

## Per-task Definition of Done

Touched service's unit tests added/updated and green · the relevant `scripts/e2e*.sh`/`coverage.sh`
still passing · no behavior change (same responses/contracts) · docs updated if a structure moved ·
this file's **Status** flipped to `DONE` in the same commit, tagged with the `RF-id`.
