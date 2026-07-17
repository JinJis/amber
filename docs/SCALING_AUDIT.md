# Production Scaling Audit (SCALING_AUDIT)

> **⚠️ 진행 상태 (2026-07-17 갱신 — 본문은 감사 시점 기준이라 대부분 "open bug"처럼 읽히지만 상당수 처리됨).**
> ROADMAP M-SCALE 기준: **SC-0 ✅ · SC-1 ✅(12/12) · SC-2 ✅(6/6: 크로스노드 잠금·단일비행·advisory
> lock) · SC-3 대부분 ✅(SC-3.1/3.2/3.3/3.4, ME-6 부분)**. 아래 §2 CRITICAL·§3 HIGH 다수는 이미 수정됨 —
> 각 항목의 최신 상태는 ROADMAP M-SCALE 행을 기준으로 보세요. **여전히 열린 tail**만 이 문서의 유효
> 착수 대상: rag→AlloyDB 분리 + 관리형 PITR(CR-8/CR-11), 인제스트 job 샤딩 + COPY inserts(HI-12),
> 워커 healthcheck/리소스 제한/전용 pg 노드(ME-13), 잔여 ME-6 스토리지 분리(인라인 아티팩트 JSON→사이드
> 테이블, og_image base64→오브젝트 스토리지, 조회수 hot-row), SC-3.3 request-id 전파 잔여.
>
> **2026-07-12 · development @ 8541a88.** Four independent code-first audits (request path /
> data plane / product layer / infra + cost) were run against the actual source, and the highest-impact
> claims (planner singleton race, billing lock scope, gateway synchronous commits) were re-verified
> directly in mainline code. Live measurements (EXPLAIN ANALYZE, pg_stat, docker stats) were taken on
> the local dev stack — **the code is the evidence, not the docs** (every finding cites file:line).
>
> Verdict in one paragraph: **the current stack is a well-built "one process per service" dev
> topology.** At hundreds of concurrent users the first things to collapse are ① agent-engine's
> ~12-thread Gemini bottleneck + single-API-key 429s, ② the gateway event loop (2 synchronous commits
> per proxied call), ③ rag's pool-less connection storm. The moment you scale any service to 2
> replicas, chat resume/stop, alerts, billing, and rate limiting break or duplicate. The security
> gates (admin default credentials, unguarded dev secrets, zero backups) are mandatory before any
> public exposure, independent of traffic.

- Severity: **CRITICAL** = breaks / loses data / double-charges in production · **HIGH** = degrades badly · **MEDIUM** = cost/latency · **LOW** = minor
- Remediation plan: [§6 roadmap](#6-remediation-roadmap--m-scale) (SC-0 security & data loss → SC-1 single node → SC-2 multi-replica → SC-3 data growth & observability)

---

## 1. Measured baseline (2026-07-12, local dev stack)

| Item | Measurement |
|---|---|
| One Postgres hosting 5 workloads | rag **20 GB** · datasets 331 MB · controlplane 17 MB · studio 9 MB · procrastinate queue |
| `rag_chunks` | **1,105,784 rows / 20 GB** — HNSW **8.4 GB** · trgm GIN 1.1 GB · tsv GIN 469 MB · TOAST 8.8 GB |
| HNSW vs memory | index 8.4 GB = **4.2×** shared_buffers (2 GB); dense top-64 **6.2 s cold** / 10 ms warm |
| trgm leg (queries ≤3 tokens) | **22.0 s cold / 2.0 s warm — 0 rows returned** (all 11,744 candidates discarded on recheck) |
| HNSW filtered recall | with `ticker='AAPL'` filter, ef_search=40 → **12 rows returned for LIMIT 64** |
| Buffer contention | `buffers_backend` 18.5 M vs `buffers_checkpoint` 2.4 M — backends evict their own buffers |
| Host | single 31 GiB node — postgres RSS 7.3 GiB · worker 782 MiB · rag 475 MiB; disk 52 % of 99 GB |
| audit/usage growth | 7 dev days = `usage_events` 26.7 k + `audit_log` 26.8 k rows — at 100 req/s that's **~2.6 GB/day** |
| Process model | all 7 services run 1 uvicorn worker / 1 Node process (no `--workers` flag, verified in every Dockerfile) |

---

## 2. CRITICAL

### CR-1 · The chat path is pinned to process memory — in-memory `RunManager`
- **Evidence:** `studio-api/studioapi/runs.py:36-39,128-129` (`_runs`/`_active` dicts, module singleton — the docstring itself says "In-memory (per studio-api process)"). Consumers: `/chat/stream` · `/active-run` · `/runs/{id}/stream` · `/stop` (`studioapi/main.py:179-272`).
- **Symptom at scale:** N replicas behind a round-robin LB → resume/stop/active-run **fail 404/no-op with probability (N-1)/N**. A deploy/restart kills every in-flight run, and **the turn quota was already consumed before the run started** (`chat.py:183`) — the user pays for a turn that produced no answer. Wedged runs stay "running" forever (HI-9), so they never get cleaned up either.
- **Fix direction:** externalize run events to Postgres (or Redis Streams) keyed by run_id + refund the quota for dead runs. Sticky routing is the fallback, but it still loses runs on deploy.

### CR-2 · The billing advisory lock does **not cover the charge phase** — duplicate-charge-attempt risk
- **Evidence (re-verified):** `studio-api/studioapi/billing.py:360-379` — `pg_try_advisory_lock` wraps **only the SELECT** of due subscriptions/retries and is released in `finally` immediately after. The actual charge loops (`:381-432`, up to 15 s per charge) run unlocked. The protection claimed by the docstring ("복수 레플리카는 pg_advisory_lock으로 직렬화", `:356`) does not exist.
- **Symptom at scale:** two replicas' hourly ticks select the same due subscription concurrently → both call `gateway().charge()`. The remaining defenses are the deterministic orderId + Toss-side idempotency + a TOCTOU `status=="paid"` check (`:213-214`) — concurrent in-flight charges with the same orderId are undefined-behavior territory at the payment gateway, and `FakeGateway` happily double-charges, so tests can't catch it. The same race exists between admin manual retry and the tick (`billing_api.py:119-138`).
- **Fix direction:** hold the lock for the whole tick, or claim per subscription with `SELECT … FOR UPDATE SKIP LOCKED`; make the invoice transition atomic with a status guard (`UPDATE invoices SET status='charging' WHERE … AND status IN ('pending','failed')`) before calling the PG.

### CR-3 · Every background loop runs on every replica — duplicate alert delivery, duplicate LLM spend
- **Evidence:** lifespan starts these per process: alerts tick (`studioapi/scheduler.py:46-78,107` — **no lock**; only billing takes `pg_try_advisory_lock`, `billing.py:362-366`) · ask-feed refresh loop (`askfeed.py:113-129`) · read-through/onboarding kicks are process-local globals (`askfeed.py:135-157,232`). The alert fire path has zero dedup (`alerts_render.py:265-292`).
- **Symptom at scale:** N replicas → alerts delivered **N times** (duplicate Telegram/Slack/email — user-visible), feed refreshes run N times. The on-demand ticker feed has no single-flight at all (`askfeed.py:211-226`): M users tapping the same cold ticker = **M concurrent Gemini generations of the same cards**, plus racing commits on one `AskFeedCache` row (the first-insert IntegrityError is outside the try → 500, `askfeed.py:89-97`).
- **Fix direction:** move periodic work onto the existing Procrastinate worker, or wrap each tick in an advisory lock held for the tick duration (the billing pattern); feeds need per-scope single-flight (in-process asyncio lock + cross-node advisory lock) and an `ON CONFLICT` upsert.

### CR-4 · Gateway: 4 synchronous DB round-trips (incl. 2 commits) per proxied call, on the event loop
- **Evidence (re-verified):** `control-plane/controlplane/gateway.py` — auth SELECT (`:155-159`) + entitlement SELECT (`:121-131`) + `_meter` INSERT+COMMIT (`:74-78` → `:102`) + `_audit` INSERT+COMMIT (`:68-71` → `:103`), all **synchronous SQLAlchemy inside the async handler**. Single uvicorn worker (`control-plane/Dockerfile:20`).
- **Symptom at scale:** every tool call of every user passes through one event loop that stalls ~10-20 ms serially per call (including 2 fsyncs) → a **platform-wide ceiling of ~50-100 req/s** regardless of upstream speed. Side effect: `usage_events` + `audit_log` append 2 rows per call, unbounded (§HI-13).
- **Fix direction:** batch meter/audit through an in-memory queue → periodic bulk INSERT (or async engine / fire-and-forget); TTL-cache auth + entitlement (the plan already is); scale `--workers` after that.

### CR-5 · Gemini: one shared key + default ~12-thread pool + zero 429 retry on the critical path
- **Evidence:** every LLM call is `asyncio.to_thread(client.models.generate_content…)` — plan (`agentengine/planner.py:257`), streaming synthesis **parks a thread per chunk poll** (`planner.py:202-213`), intake (`intake.py:213`), follow-ups (`enrichment.py:227`), etc. No custom executor anywhere (repo-wide grep) → default `min(32, cpu+4)` ≈ 12 threads. One key for the whole platform (`gemini_io.py:13-24`; tenant keys go only to the gateway). `genai_client()` sets no retry_options → the SDK makes 1 attempt; **429 retry exists only for follow-up chips** (`enrichment.py:224-246`) — a 429 on intake/plan/synthesis is an immediate user-visible failure ("답변 생성 중 문제가 발생했어요", `chat.py:459-463`).
- **Calls per turn:** intake 1 + plan ≤8 (cap 14, `config.py:43-44`) + refine 1 + **pro-tier synthesis 1** + chart annotation ≤1 + follow-ups 2 personas + hook 1 ≈ **9-10 calls/turn**; ~22 with A2A decomposition. Plan context carries the accumulated tool results **untruncated** (`gemini_io.py:75-81`) — cost grows superlinearly with steps.
- **Symptom at scale:** concurrent-Gemini ceiling ≈ 12 per process, and executor queue wait sits **outside** the 90 s timeout (`gemini_io.py:22-24`) so it is unbounded. Against shared-key RPM (especially the pro tier), expect **brownouts at ~30-60 concurrent turns/min** — synthesis fails first. Estimated cost at 1,000 turns/day: ~$50-100/day.
- **Fix direction:** the genai async client (`client.aio`) or a dedicated sized executor + **per-tier semaphore/queue**; 429 backoff on intake/plan/synthesis; truncate/summarize plan context; multiple keys/projects or provisioned throughput.

### CR-6 · The planner singleton is mutated per turn — cross-user tier crosstalk (a live bug today)
- **Evidence (re-verified):** `planner.py:277-281` `@cache def _build_planner(model)` → **one instance shared by all requests**, yet `chat.py:171-172` sets `planner.synthesis_override = spec.synthesis_model` per turn; synthesis reads it at call time (`planner.py:201,242`). The comment claiming "the planner instance is per-turn, so no cross-talk" is false.
- **Symptom at scale:** a guest/free turn's `flash` override **downgrades the synthesis of a concurrently running pro user** (or vice versa) — plan-tier monetization silently breaks. Reproducible from 2 concurrent users.
- **Fix direction:** pass the override as a call argument (remove the state). Smallest possible diff; fix first.

### CR-7 · rag: a brand-new psycopg connection per query — no pool
- **Evidence:** `rag/rag/store.py:233-236` `_connect()` = `psycopg.connect(dsn)` + `register_vector`, called by every operation (search `:277` · lexical `:312,323,337` · upsert `:244` · replace `:405`). With `multi_query=True` (default), one search = 3 queries × dense+lexical legs = **6-12 fresh connections**.
- **Symptom at scale:** 50 concurrent searches = 300-600 connection setups against `max_connections=100` — **shared** with datasets (pool 15), procrastinate (8), control-plane/studio/admin → `too many clients` → the search legs swallow the exception (`search.py:87-97`) and **RAG evidence silently disappears from answers**.
- **Fix direction:** one `psycopg_pool.ConnectionPool` per process (register_vector once in the configure hook) — smallest diff, biggest stability win. PgBouncer in front of the shared instance comes after.

### CR-8 · Three pgvector search pathologies: 22 s trgm · HNSW recall starvation · index > memory
- **Evidence:**
  - **trgm leg** — fires for every query ≤3 tokens (ticker/company names = the most common shape) (`store.py:328-345`): measured **22.0 s cold / 2.0 s warm, 0 rows** (short query vs multi-KB chunk text never crosses similarity 0.3; all 11,744 candidates discarded on recheck). No timeout on the lexical leg + Postgres `statement_timeout=0`.
  - **HNSW recall starvation** — no `hnsw.ef_search`/`iterative_scan` anywhere (grep: 0 hits) → default ef_search=40 < `candidate_k=64` (`rag/config.py:47`); measured with `ticker='AAPL'` filter: **12 rows returned for 64 requested**. As the corpus grows, single-ticker selectivity (~0.5 % today) shrinks → dense recall for the platform's core query shape trends to zero.
  - **Growth vs memory** — HNSW 8.4 GB (up 30 % from the 6.5 GB noted in the ING-1 compose comment) vs shared_buffers 2 GB; dense cold search 6.2 s. At the current universe alone: **+2-3 M chunks/yr ≈ HNSW +15-23 GB/yr, DB +40-60 GB/yr**; a `us_all/kr_all` universe multiplies by ×6-9. No retention/expiry path exists anywhere (news included).
- **Fix direction:** drop the trgm leg (or restrict it to short fields) + `SET hnsw.iterative_scan=relaxed_order`, `ef_search≥candidate_k`, `SET LOCAL statement_timeout` per search connection; news age-out; size RAM against the index (production = split to AlloyDB, `USER_TODO.md` 1-10).

### CR-9 · Yahoo live proxy with zero caching — per-turn calls from one IP; open breaker = platform-wide price outage
- **Evidence:** `datasets/app/providers/us/yahoo.py` has no cache; `/prices` · `/prices/snapshot` (`routers/prices.py:26-51`) hit Yahoo live on every agent tool call (KR tries `.KS` → `.KQ` = 2×, `yahoo.py:24-27`). Market-overview fan-outs: themes 32 · sectors 13 · asset-classes 10 · commodities 7 symbols concurrently (`store/cross_asset.py:47-51`); `/prices/snapshot/market` gathers **up to 100 symbols unbounded** (`routers/_common.py:41-55`, no semaphore). 429s are retried 4× with 5.5 s of sleeps in the request path (`app/http.py:19-58`), ignoring Retry-After.
- **Symptom at scale:** 50 users → thousands of calls/hour from a single egress IP → Yahoo 429/blocks → the circuit breaker opens **provider-wide for 60 s** (`http.py:26-33`) = every user's price features go down simultaneously (the Stooq fallback is EOD-only).
- **Fix direction:** a 30-120 s shared TTL cache keyed by symbol+range (reuse the news dedup pattern, `providers/news.py:87-105`) + fan-out semaphore + per-provider client-side token bucket; serve from the ingested `price_bars` store where possible.

### CR-10 · Three security gates — traffic-independent, mandatory before exposure
- **admin :8005** — default `admin/admin` + a public dev session secret (**cookies are forgeable**), zero login rate limiting, **no production guard at all**, full CRUD over every service DB on a 0.0.0.0-published port (`admin/adminpanel/config.py:15-17`, `main.py:77-98`, compose `${ADMINUI_USERNAME:-admin}`).
- **Dev secrets pass unguarded in 5 of 7 services** — guards exist only in control-plane (admin_token) and studio-api. **No guard on `AUTH_SECRET=dev-secret-change-me` → Auth.js JWT forgery**; datasets **accepts any non-empty key** when `DATASETS_API_KEYS` is unset (`datasets/app/deps.py:19-29`); rag/agent-engine silently accept dev tokens; `web/lib/studio.ts:6-13` checks only unset, not the dev **value**; postgres `rag:rag` is hardcoded. The claim in `env/secrets.env.example:3-5` ("services refuse to start on dev defaults") is true for 2 of 7.
- **Conversation IDOR** — `GET /conversations/{id}/messages` (`studioapi/main.py:229-243`) and `POST /conversations/{id}/stop` (`:179-184`) have **no ownership check** (active-run has one, `:256-259`) — IDs are unguessable, but a leaked ID allows reading/stopping another user's conversation.
- **Fix direction:** a shared `assert_production_secrets` in all 7 services (enumerating every dev default) + private-bind/allowlist/rate-limit the admin + add `get_owned` to both endpoints.

### CR-11 · Zero backups — one lost disk = every tenant, key, and billing ledger
- **Evidence:** `archive_mode=off`; repo-wide zero hits for pg_dump/wal-g/pgbackrest; persistence = one local volume `pg_data` (22 GB) on a single host. Invoices, credit ledger, and subscriptions exist only there.
- **Fix direction:** (owner action, USER_TODO 1-11) at minimum a nightly pg_dump + off-host copy **now**; production should move controlplane+studio (billing) to managed Postgres with PITR first. The rag corpus is re-derivable but costs money to re-embed — snapshot it too.

---

## 3. HIGH

### HI-1 · studio-api: sync SQLAlchemy on the event loop in every handler + sessions held across network calls
`studioapi/db.py:45-53` sync engine (default pool 5+10, **no pre-ping** — control-plane identical, `controlplane/db.py:45-48`). Every `async def` handler does sync DB on the loop (throughout `main.py`; 3-5 quota count queries per turn, `quotas.py:111-165`; `ensure_user` on **every authenticated request**, `provision.py:45-66`). askfeed holds a session **across a 45 s LLM call** (`askfeed.py:218-226`); standing holds one across N serial gateway probes (`standing.py:132-160`). This same loop pumps all live SSE streams → token streaming stutters; 15-connection exhaustion = 500s after the 30 s pool timeout. **Fix:** async engine (or consistent `to_thread`) + pool sizing/pre-ping + never hold a session across HTTP.

### HI-2 · Gateway in-memory rate limiter + plan cache — multiply by N replicas
`controlplane/ratelimit.py:12-26` (docstring self-describes as "single-process dev/staging") + plan cache `gateway.py:39-52`. N replicas → N× the intended limit; resets on restart. `settings.redis_url` (`config.py:22`) is **wired to nothing**. **Fix:** Redis/PG token bucket (the interface is already backend-neutral).

### HI-3 · Process-local caches that are semantically global — **already wrong today with web+worker**
`datasets` already runs the same code in 2 processes (web + worker): OpenDART quota blocks `_blocked_keys` (`providers/kr/opendart.py:101-128` — the worker marks a key spent, the web process keeps burning it) · circuit breaker (`app/http.py:23`) · KIS OAuth token (issuance rate ~1/min, `kr/kis.py:19-45`) · the 2-min news dedup (`providers/news.py:90-105` — the "N-user storm → 1 upstream call" guarantee only holds within one process) · TTLCache (multi-MB SEC/DART payloads downloaded per process). datasets `redis_url` (`app/config.py:92`) also unwired. **Fix:** move these four to Redis first: quota blocks, KIS token, breaker, news dedup.

### HI-4 · TTLCache never evicts expired entries — an OOM path
`app/cache.py:32-51` — expired entries are only replaced on refresh; `_inflight` locks accumulate forever. `sec:facts:{cik}` holds a parsed companyfacts dict of **tens of MB** (`sec_edgar.py:102-110`); a 500-ticker sweep pins gigabytes in the worker, and the web process builds its own copy. **Fix:** bounded LRU + expiry sweep; cache extracted rows, not the full parsed payload.

### HI-5 · New httpx client per call + catalog fetched every turn
agent-engine builds a fresh client per tool call/catalog fetch (`agentengine/client.py:23,74` — ~30 TCP handshakes per turn); studio-api in ~20 places (`chat.py:116` with **timeout=None**); rag telemetry spawns a new client + detached task per embed batch (`rag/embeddings.py:142-165`, `rerank.py:53-73`). The near-static catalog is **fetched from the gateway every turn** (`chat.py:296`, with 3 retries). The gateway's shared client runs default Limits (100 connections) — which becomes the concurrency cap (`gateway.py:24`). **Fix:** module-level shared `AsyncClient` with explicit Limits per service; TTL-cache the catalog; batch telemetry.

### HI-6 · Turn-quota check-then-insert race + missing composite index
`quotas.py:111-165` COUNT→INSERT with no lock (+ guest `turns_used` read-modify-write, `:158-164`) — K parallel turns overshoot the cap by K-1. No `(user_email, day)` composite index on `turn_usage` (`models.py:203-208`), plus 2 extra IP-sibling queries per guest turn (`quotas.py:54-70`). **Fix:** atomic INSERT…SELECT guard (or FOR UPDATE on the user row) + composite indexes.

### HI-7 · `filing_search` does synchronous ingest in the request path (≤1200 s) — no single-flight
`app/routers/filings.py:66-71` — zero hits triggers a live filing list + HTML fetch + hundreds of chunks embedded. Two users on the same cold ticker = **duplicate embedding spend** (the replace_scope lock only serializes the DB swap). Tickers with legitimately no filings (ETFs) re-trigger on every search. **Fix:** single-flight per (market,ticker) + defer to the queue (honest "indexing" response) + negative cache.

### HI-8 · One RAG search = 2-4 Gemini calls + 1 billed Vertex rerank — zero result caching
Query expansion 1 (flash-lite, `search.py:31-58`) + up to 3 `embed_query` calls (one per variant, `:84,113-116`) + rerank (`rerank.py:39-50`). Worst-case latency stack ~26 s, plus embed-RPM quota contention. **Fix:** short shared (query,filters)→hits cache + embedding cache by text hash + batch the 3 variant embeds into **one** call.

### HI-9 · Wedged runs are retained forever + unbounded concurrent run starts
`studioapi/chat.py:116` `timeout=None` → a wedged engine leaves the run "running" forever; `_prune` removes only finished runs (`runs.py:60-65`), and finished runs retain 300 events including the full citations/artifacts payload in the done event. No global cap on run starts (quota is per-user counting only) → 500 concurrent turns = 500 driver tasks + 500 buffers. **Fix:** whole-run deadline watchdog + prune expired running runs + drop done payloads from the retained tail (already persisted in messages) + a global semaphore.

### HI-10 · No pagination anywhere — conversations, messages, and the alerts tick full-scan
`main.py:190-192` returns all conversations; `:231-243` all messages with full artifact JSON (100s of KB per assistant message). The alerts tick loads every active alert every 60 s (`scheduler.py:51-63`, no `next_fire_at` filter/index), then fires serially (up to 8 sequential fetches per digest, `alerts_render.py:117-137`) — once tick > 60 s, delivery lag grows unboundedly. **Fix:** LIMIT+cursor; `WHERE next_fire_at <= now` index + SKIP LOCKED claims + worker fan-out.

### HI-11 · Zero observability — a different request-id at every hop, no metrics, no tracing
Every service **generates** its own request id and never reads inbound `X-Request-ID` (`*/logging_config.py:121,135`) → one turn has 4 ids across 4 hops; cross-service grep is impossible. The only persisted latency is the gateway→data-plane hop. **Fix:** propagate the id (the gateway `_HOP` strip-list already lets it through) + per-stage durations on SSE + a minimal Prometheus exporter per service.

### HI-12 · Full-universe ingest is serial per ticker AND per accession — first full run 1-8 days
`filing_ingest.py:212-234` (serial tickers) · `:164-183` (serial accessions); the procrastinate lock `pipe:{pipeline}:{market}` (`queue.py:101-104`) means worker concurrency=4 buys nothing within a pipeline. Measured 0.15-1.5 s/row embed+insert → 500 tickers ≈ 500 k chunks. Delta mode rescues steady state, but any re-chunking change forces the full path. **Fix:** shard into per-ticker-batch jobs (locks per batch) + `COPY`/pipeline-mode inserts (current `executemany` is one round-trip per row, `store.py:245-249`).

### HI-13 · `usage_events` + `audit_log`: 2 rows per call, unbounded — no retention, partitioning, or rollup
At 100 req/s → **~2.6 GB/day**, on the same instance as OLTP. Admin `/costs` scans 30 days with no `ts` index (`admin/adminpanel/main.py:812-814`); the settings-page usage aggregate scans the project's **entire history** (`controlplane/admin.py:202-216`). **Fix:** monthly partitions + drop job + nightly rollup + `(project_id, ts)` index (one package with the CR-4 batching).

---

## 4. MEDIUM

| # | Finding | Evidence | Direction |
|---|---|---|---|
| ME-1 | Desk-feed has no single-flight (45 s generation inside the request; 2 tabs = 2×) + standing probes run serially inline | `deskfeed.py:97-148`, `standing.py:132-172` | per-scope lock + background generation / serve-stale |
| ME-2 | Post-deploy reconcile herd — `_reconciled` is process-local; every user's first request fires 9-11 activation POSTs | `provision.py:40-67`, `plans.py:123-135` | `connectors_reconciled_at` column (keyed by plan version) |
| ME-3 | Boot-time seeding/`create_all`/`ALTER` race — simultaneous replica boots can IntegrityError-crash-loop | `main.py:50-52`, `db.py:56-104`, `agents.py:79-100` | advisory lock or a separate migration job |
| ME-4 | Guests: a cookie per root visit → unbounded `User` rows (crawlers included), no TTL cleanup; all guests share **one global key at 240/min** (a product-wide ceiling); CGNAT → thousands-entry IP-sibling IN clause | `web/middleware.ts:9-15`, `guest.py:99-116`, `plans.py:40-41`, `quotas.py:54-70` | cookie on first chat only + TTL cleanup job + JOIN-based count + per-IP-bucket rates |
| ME-5 | Background feeds are billed to **an arbitrary user's tenant key** (`_any_api_key`) — feed dies if that key is revoked | `askfeed.py:51-52,100-110,239-245` | dedicated service tenant/key (reuse the guest.py pattern) |
| ME-6 | Message/share payload bloat: inline artifact JSON Text; `og_image` base64 ≤4 MB as a DB row; public share **reads perform writes** (referral ensure + hot-row views UPDATE) | `models.py:136,226-237`, `shares.py:138-181` | side table / object storage; referral at creation time; views into a counter table / sampled |
| ME-7 | `market.py` unbounded per-user in-memory caches — the **globally identical** pulse cached per user; watch = 8 snapshot calls per user per 120 s | `market.py:25-93` | global cache + LRU bound + shared per-ticker cache |
| ME-8 | Admin panel: `/db` index runs `COUNT(*)` on every table of every DB (minutes at scale) + rows addressed by **ORDER BY pk OFFSET** (concurrent inserts can make an edit/delete hit the **wrong row**) + 3 DBs reflected at import | `admin/adminpanel/state.py:39-55`, `db_browser.py:95-202` | reltuples estimates + PK addressing + lazy reflect |
| ME-9 | Unbounded logs: no compose `logging:` caps (json-file, same disk as pg); secret redaction only in datasets' formatter | `docker-compose.yml` (all services), `datasets/app/logging_config.py:30-32` | max-size/max-file now + shared redaction |
| ME-10 | No GC for evidence_docs on disk (a file per cited URL; deck PDFs 40 MB cap × universe; dead files leak on version-key changes) | `store/source_html.py:101-173`, `deck_ingest.py:31-48` | atime-LRU sweeper + directory budget |
| ME-11 | No client-side SEC rate limiter (10 req/s guideline — sweep + user turns overlapping gets the IP blocked); `fetch_bytes` bypasses **both** retry and breaker | `sec_edgar.py`, `app/http.py:102-112` | per-provider token bucket + unify the fetch_bytes path |
| ME-12 | Unmanaged Gemini cost levers: plan context untruncated (superlinear with steps), synthesis always pro, a new `genai.Client` + fire-and-forget usage POST per LLM call | `gemini_io.py:75-81`, `enrichment.py:318,412,471`, `usage.py:32-54` | truncate/summarize + client reuse + batched telemetry |
| ME-13 | No healthchecks on worker/web/admin (**the ingestion worker can wedge silently**); zero container resource limits (one runaway service OOMs the host) | compose (all) | procrastinate heartbeat probe + deploy.resources |
| ME-14 | Toss guards activate only when `TOSS_SECRET_KEY` is set — a production deploy that forgets the key **silently boots on FakeGateway**; `BILLING_ENC_KEY` has a hardcoded fallback | `config.py:81-85`, `billing.py:47-50` | fold into the CR-10 shared assert |
| ME-15 | Billing tick charges serially (≤15 s each) — thousands of same-day renewals = a multi-hour tick, dunning drift | `billing.py:381-432` | bounded-concurrency charging + the CR-2 status guard |
| ME-16 | News corpus has no retention (doc_id=url upserts only; old chunks live in the hybrid indexes forever) — the recency boost hides, not removes | `news_ingest.py:38`, `search.py:123-134` | age-out delete job |

## 5. LOW / healthy parts

- **The web BFF is stateless and a pure proxy** — the only tier that can scale horizontally today (`web/lib/studio.ts`, JWT cookie, no polling loops, logos at `max-age=604800`). BFF caching of semi-static JSON (connectors/templates) is free headroom.
- **Embedding cost is a non-issue** — the full 1.1 M-chunk corpus ≈ single-digit dollars (measured 56.5 M tokens); query embeds are ~free.
- **Queue mechanics are healthy** — procrastinate LISTEN/NOTIFY + 5 s poll fallback, negligible DB load; the only issue is co-location (§CR-8).
- **Key hashing is sane** (SHA-256 + compare_digest — no bcrypt-per-request trap) · **webhooks idempotent** (event_id UNIQUE) · no CORS configured anywhere (harmless under the BFF architecture).
- Known-and-acceptable: small unpruned tables (email_otp · webhook_events · card_taps · turn_usage), gateway buffering full response bodies, `existing_texts` single ANY(%s), lexical OR double-execution (0.75 s warm).

---

## 6. Remediation roadmap — M-SCALE

> Principle: **the order is the risk order.** SC-0 is mandatory even at zero traffic (security & data
> loss), SC-1 raises the ceiling for first user traffic, and **no service may go to 2 replicas before
> SC-2 is complete** (duplicate deliveries, duplicate charge attempts, and 404s appear immediately).
> Per ROADMAP rules: one task per PR.

### SC-0 · Security & data-loss (mandatory pre-exposure — small diffs)
| Item | Covers |
|---|---|
| Shared `assert_production_secrets` in all 7 services (AUTH_SECRET · SERVICE_TOKEN value · ADMINUI_* · datasets keys · BILLING_ENC_KEY · Toss presence · parameterized pg password) | CR-10, ME-14 |
| Lock down admin :8005 — private bind/allowlist + login rate limit + refuse dev credentials | CR-10 |
| Ownership checks (`get_owned`) on `messages`/`stop` | CR-10 |
| Nightly pg_dump + off-host copy (owner action → USER_TODO 1-11) | CR-11 |
| Compose log caps (max-size/max-file) + shared redaction | ME-9 |

### SC-1 · Single-node throughput (before first traffic)
| Item | Covers |
|---|---|
| Planner `synthesis_override` → call argument (one-liner, **do first**) | CR-6 |
| Gemini: per-tier semaphore + 429 backoff on intake/plan/synthesis + plan-context truncation + `client.aio` (or sized executor) + genai client reuse | CR-5, ME-12 |
| Gateway meter/audit batching queue + auth/entitlement TTL cache | CR-4 |
| rag `psycopg_pool` + drop trgm leg + `ef_search`/`iterative_scan` + `statement_timeout` | CR-7, CR-8 |
| Yahoo TTL cache + fan-out semaphore + per-provider token bucket (incl. SEC) + unify fetch_bytes | CR-9, ME-11 |
| Shared httpx clients (agent-engine · studio · rag telemetry) + catalog TTL cache | HI-5 |
| studio/control-plane pool sizing + pre-ping + session-vs-network separation (askfeed/standing first) | HI-1 |
| Atomic quota + `(user_email, day)` composite index | HI-6 |
| filing_search single-flight + queue deferral + negative cache | HI-7 |
| Bounded-LRU TTLCache + expiry sweep (cache extracted rows for large payloads) | HI-4 |
| Run deadline watchdog + running-run prune + drop done payloads + global concurrent-run cap | HI-9 |
| RAG search result/embedding caches + single batched embed for the 3 variants | HI-8 |

### SC-2 · Multi-replica readiness (ALL required before adding replicas)
| Item | Covers |
|---|---|
| Externalize run state (PG event table / Redis Streams) + quota refund for dead runs | CR-1 |
| Billing lock held for the whole tick or `FOR UPDATE SKIP LOCKED` + atomic invoice `status='charging'` guard | CR-2, ME-15 |
| alerts/askfeed/onboarding kicks → advisory lock or move to procrastinate + per-scope feed single-flight + `ON CONFLICT` | CR-3, ME-1 |
| Wire Redis: rate limiter + OpenDART quota blocks · KIS token · circuit breaker · news dedup | HI-2, HI-3 |
| Advisory lock around boot seeding/migration | ME-3 |
| Reconcile column + guest TTL cleanup + dedicated background tenant key | ME-2, ME-4, ME-5 |

### SC-3 · Data growth, observability, cost (in parallel with the traffic ramp)
| Item | Covers |
|---|---|
| usage/audit monthly partitions + retention drop + nightly rollup + `(project_id, ts)` index | HI-13 |
| evidence_docs GC + news age-out + og_image/artifact storage split | ME-10, ME-16, ME-6 |
| request-id propagation + stage durations + minimal metrics exporters | HI-11 |
| rag → AlloyDB split (USER_TODO 1-10) + managed PITR for controlplane/studio | CR-8, CR-11 |
| Ingest job sharding + COPY inserts | HI-12 |
| Pagination (conversations · messages) + alerts tick index/claims | HI-10 |
| worker healthcheck + resource limits + dedicated pg node | ME-13 |

### Multi-replica readiness verdict (one line each)
- **The only tier safe to replicate today: web.**
- studio-api: **forbidden** until CR-1 · CR-2 · CR-3 · ME-3 are all fixed (duplicate charge attempts, duplicate alerts, resume 404s).
- control-plane: rate limiting is voided until HI-2.
- datasets/worker: already desynced across its 2 processes today — wire Redis (HI-3) first.
- agent-engine · rag: stateless, but without CR-5/CR-7 extra replicas merely share the same bottleneck (key RPM, pg connections).
