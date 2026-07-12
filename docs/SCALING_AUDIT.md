# 프로덕션 스케일링 감사 (SCALING_AUDIT)

> **2026-07-12 · development @ 8541a88 기준.** 4개 독립 감사(요청 경로 / 데이터 플레인 / 프로덕트 레이어 /
> 인프라·비용)를 코드 정독으로 수행하고, 핵심 주장(플래너 싱글턴 레이스·빌링 락 범위·게이트웨이 동기 커밋)은
> 본선 코드에서 재검증했다. 라이브 계측(EXPLAIN ANALYZE, pg_stat, docker stats)은 로컬 dev 스택 기준 —
> **문서가 아니라 코드가 근거다** (모든 발견은 file:line 인용).
>
> 판정 요약: **지금 스택은 잘 만든 "서비스당 1프로세스" dev 토폴로지다.** 수백 동시 유저에서 먼저 무너지는 곳은
> ① agent-engine의 ~12스레드 Gemini 병목 + 단일 API 키 429, ② 게이트웨이 이벤트 루프(콜당 동기 커밋 2회),
> ③ rag의 무풀 커넥션 폭풍. 레플리카를 2대로 늘리는 순간 챗 재개/중단·알림·빌링·레이트리밋이 깨지거나 중복된다.
> 보안 게이트(admin 기본 크레덴셜·데브 시크릿 무가드·백업 전무)는 트래픽과 무관하게 공개 전 필수.

- 심각도: **CRITICAL** = 프로덕션에서 깨짐/유실/중복과금 · **HIGH** = 심하게 열화 · **MEDIUM** = 비용/지연 · **LOW** = 경미
- 조치 계획: [§6 로드맵](#6-조치-로드맵--m-scale) (SC-0 보안·유실 → SC-1 단일 노드 → SC-2 멀티 레플리카 → SC-3 데이터 증식·관측)

---

## 1. 측정 베이스라인 (2026-07-12, 로컬 dev 스택)

| 항목 | 측정치 |
|---|---|
| Postgres 1대가 5개 워크로드 | rag **20 GB** · datasets 331 MB · controlplane 17 MB · studio 9 MB · procrastinate 큐 |
| `rag_chunks` | **1,105,784행 / 20 GB** — HNSW **8.4 GB** · trgm GIN 1.1 GB · tsv GIN 469 MB · TOAST 8.8 GB |
| HNSW vs 메모리 | 인덱스 8.4 GB = shared_buffers(2 GB)의 **4.2×**; dense top-64 **콜드 6.2 s** / 웜 10 ms |
| trgm 레그 (≤3토큰 쿼리) | **콜드 22.0 s / 웜 2.0 s — 결과 0행** (11,744 후보 전부 recheck 탈락) |
| HNSW 필터 회수 | `ticker='AAPL'` 필터 시 ef_search=40 → **LIMIT 64에 12행 반환** |
| 버퍼 경합 | `buffers_backend` 18.5 M vs `buffers_checkpoint` 2.4 M — 백엔드가 자기 버퍼를 직접 축출 |
| 호스트 | 31 GiB 단일 노드 — postgres RSS 7.3 GiB · worker 782 MiB · rag 475 MiB; 디스크 99 GB 중 52 % |
| audit/usage 증식 | dev 7일에 `usage_events` 26.7 k + `audit_log` 26.8 k행 — 100 req/s면 **~2.6 GB/일** |
| 프로세스 모델 | 7개 서비스 전부 uvicorn 1워커/Node 1프로세스 (`--workers` 플래그 없음, 전 Dockerfile 확인) |

---

## 2. CRITICAL

### CR-1 · 챗 경로가 프로세스 메모리에 고정 — 인메모리 `RunManager`
- **증거:** `studio-api/studioapi/runs.py:36-39,128-129` (`_runs`/`_active` dict, 모듈 싱글턴 — 독스트링 스스로 "In-memory (per studio-api process)"). 소비처: `/chat/stream`·`/active-run`·`/runs/{id}/stream`·`/stop` (`studioapi/main.py:179-272`).
- **스케일 증상:** 레플리카 N대 + 라운드로빈 LB → 재개/중단/active-run이 **(N-1)/N 확률로 404/no-op**. 배포/재시작은 진행 중인 모든 런을 죽이는데, **턴 쿼터는 런 시작 전에 이미 소비됨**(`chat.py:183`) — 유저가 답 없는 턴에 과금된다. 스턱 런은 영원히 "running"(HI-9)이라 정리도 안 됨.
- **해결 방향:** 런 이벤트를 Postgres(또는 Redis Streams)에 run_id 키로 외부화 + 죽은 런 쿼터 환불. 차선책 sticky 라우팅은 배포 시 유실은 못 막는다.

### CR-2 · 빌링 advisory lock이 **청구 단계를 안 덮음** — 중복 청구 시도 위험
- **증거(재검증):** `studio-api/studioapi/billing.py:360-379` — `pg_try_advisory_lock`은 due 구독/재시도 **SELECT만 감싸고 `finally`에서 즉시 해제**. 실제 청구 루프(`:381-432`, 건당 최대 15 s)는 무락. 독스트링("복수 레플리카는 pg_advisory_lock으로 직렬화", `:356`)이 주는 보호는 실재하지 않는다.
- **스케일 증상:** 레플리카 2대의 시간별 틱이 같은 due 구독을 동시에 선택 → 둘 다 `gateway().charge()`. 남는 방어는 결정적 orderId + 토스 측 멱등 + TOCTOU `status=="paid"` 체크(`:213-214`)뿐 — 동시 in-flight 동일 orderId는 PG 정의되지 않은 영역이고, `FakeGateway`는 그냥 중복 청구되므로 테스트가 못 잡는다. 어드민 수동 재시도 vs 틱도 같은 레이스(`billing_api.py:119-138`).
- **해결 방향:** 락을 틱 전체로 확장하거나 구독 단위 `SELECT … FOR UPDATE SKIP LOCKED`; PG 호출 전 `UPDATE invoices SET status='charging' WHERE … AND status IN ('pending','failed')` 상태 가드로 원자 전이.

### CR-3 · 백그라운드 루프가 레플리카마다 전부 실행 — 알림 중복 발송·LLM 중복 지출
- **증거:** lifespan이 프로세스마다 시작: 알림 틱(`studioapi/scheduler.py:46-78,107` — **락 없음**; 빌링만 `pg_try_advisory_lock`, `billing.py:362-366`) · ask-feed 갱신 루프(`askfeed.py:113-129`) · read-through 킥/온보딩 킥은 프로세스-로컬 글로벌(`askfeed.py:135-157,232`). 알림 fire 경로는 dedup 0(`alerts_render.py:265-292`).
- **스케일 증상:** N대 → 알림 **N중 발송**(텔레그램/슬랙/메일 중복 — 유저 가시), 피드 갱신 N중 실행. 온디맨드 티커 피드는 single-flight가 아예 없어(`askfeed.py:211-226`) 같은 콜드 티커를 M명이 탭하면 **동일 카드 M회 Gemini 생성** + `AskFeedCache` 한 행에 커밋 경쟁(첫 insert IntegrityError가 try 밖 → 500, `askfeed.py:89-97`).
- **해결 방향:** 주기 작업을 기존 Procrastinate 워커로 이관하거나 빌링 패턴대로 advisory lock을 틱 전체에; 피드는 스코프별 single-flight(프로세스 내 asyncio lock + 크로스 노드 advisory lock) + `ON CONFLICT` upsert.

### CR-4 · 게이트웨이: 프록시 콜마다 이벤트 루프 위 동기 DB 4회 (커밋 2회 포함)
- **증거(재검증):** `control-plane/controlplane/gateway.py` — auth SELECT(`:155-159`) + 엔타이틀 SELECT(`:121-131`) + `_meter` INSERT+COMMIT(`:74-78`→`:102`) + `_audit` INSERT+COMMIT(`:68-71`→`:103`), 전부 async 핸들러 안 **동기 SQLAlchemy**. uvicorn 1워커(`control-plane/Dockerfile:20`).
- **스케일 증상:** 모든 유저의 모든 툴콜이 지나는 단일 루프에 콜당 ~10-20 ms 직렬 스톨(fsync 2회 포함) → 업스트림 속도와 무관한 **플랫폼 전역 ~50-100 req/s 상한**. 부수: `usage_events`+`audit_log`가 콜당 2행 무한 append(§HI-13).
- **해결 방향:** meter/audit을 인메모리 큐 → 주기 bulk INSERT(또는 async 엔진/fire-and-forget); auth·엔타이틀은 TTL 캐시(플랜은 이미 있음); `--workers` 확장은 그 다음.

### CR-5 · Gemini: 단일 키 + 기본 ~12스레드 풀 + 크리티컬 패스 429 재시도 0
- **증거:** 전 LLM 호출이 `asyncio.to_thread(client.models.generate_content…)` — plan(`agentengine/planner.py:257`), 스트리밍 합성은 **청크 폴마다 스레드 점유**(`planner.py:202-213`), intake(`intake.py:213`), 팔로우업(`enrichment.py:227`) 등. 커스텀 executor 없음(전역 grep 확인) → 기본 `min(32, cpu+4)` ≈ 12스레드. 키는 플랫폼 전체 1개(`gemini_io.py:13-24`, 테넌트 키는 게이트웨이 전용). `genai_client()`에 retry_options 없음 → SDK 1회 시도; **429 재시도는 팔로우업 칩에만 존재**(`enrichment.py:224-246`) — intake/plan/합성의 429는 곧장 유저 가시 실패("답변 생성 중 문제가 발생했어요", `chat.py:459-463`).
- **턴당 호출 수:** intake 1 + plan ≤8(캡 14, `config.py:43-44`) + refine 1 + **pro 합성 1** + 차트 주석 ≤1 + 팔로우업 2페르소나 + 훅 1 ≈ **9-10콜/턴**; A2A 분해 시 ~22콜. plan 컨텍스트는 누적 툴 결과를 **절단 없이** 전부 실음(`gemini_io.py:75-81`) — 스텝이 늘수록 비용 초선형.
- **스케일 증상:** 동시 Gemini 호출 상한 ≈ 12/프로세스 + executor 큐 대기는 90 s 타임아웃(`gemini_io.py:22-24`) 밖이라 **무한 대기 가능**. 공유 키 RPM(특히 pro 티어) 기준 **~30-60 동시 턴/분에서 브라운아웃** — 합성부터 무너진다. 1,000턴/일 비용 추정 ~$50-100/일.
- **해결 방향:** genai async 클라이언트(`client.aio`) 또는 전용 사이즈드 executor + **티어별 세마포어/큐**; intake·plan·합성에 429 백오프; plan 컨텍스트 절단/요약; 키/프로젝트 다중화 또는 provisioned throughput.

### CR-6 · 플래너 싱글턴을 턴마다 변이 — 동시 2턴이면 티어 크로스톡 (지금도 발생하는 버그)
- **증거(재검증):** `planner.py:277-281` `@cache def _build_planner(model)` → **전 요청 공유 인스턴스**인데 `chat.py:171-172`가 턴마다 `planner.synthesis_override = spec.synthesis_model`로 변이; 합성 시점에 읽음(`planner.py:201,242`). "per-turn이라 크로스톡 없음" 주석은 사실과 다름.
- **스케일 증상:** 게스트/free 턴의 `flash` 오버라이드가 **동시에 달리는 pro 유저의 합성을 다운그레이드**(또는 그 역) — 플랜 티어 수익화가 조용히 깨진다. 동시 유저 2명부터 재현 가능.
- **해결 방향:** 오버라이드를 합성 호출 인자로 전달(상태 제거). 가장 작은 diff로 즉시 고칠 것.

### CR-7 · rag: 쿼리마다 새 psycopg 커넥션 — 풀 없음
- **증거:** `rag/rag/store.py:233-236` `_connect()` = `psycopg.connect(dsn)`+`register_vector`, 모든 연산이 매번 호출(search `:277` · lexical `:312,323,337` · upsert `:244` · replace `:405`). `multi_query=True` 기본이라 검색 1회 = 3쿼리 × dense+lexical 레그 = **커넥션 6-12개 신규 생성**.
- **스케일 증상:** 동시 검색 50건이면 커넥션 셋업 300-600회가 `max_connections=100`(datasets 풀 15 + procrastinate 8 + control-plane/studio/admin과 **공유**)에 부딪힘 → `too many clients` → 검색 레그가 예외를 삼켜(`search.py:87-97`) **RAG 근거가 답변에서 조용히 사라진다**.
- **해결 방향:** `psycopg_pool.ConnectionPool` 1개/프로세스(configure 훅에서 register_vector 1회) — 가장 작은 diff, 최대 안정성. 공유 인스턴스 앞 PgBouncer는 그 다음.

### CR-8 · pgvector 검색 병리 3종: trgm 22 s·rows=0 / HNSW 회수 기아 / 인덱스 > 메모리
- **증거:**
  - **trgm 레그** — ≤3토큰 쿼리(티커/회사명 = 최빈 형태)마다 실행(`store.py:328-345`): 실측 **콜드 22.0 s / 웜 2.0 s, 결과 0행**(짧은 쿼리 vs 수 KB 청크 텍스트의 similarity가 0.3을 못 넘어 후보 11,744개 전부 recheck 탈락). 렉시컬 레그엔 타임아웃 없음 + PG `statement_timeout=0`.
  - **HNSW 회수 기아** — `hnsw.ef_search`/`iterative_scan` 설정 코드 없음(grep 0건) → 기본 ef_search=40 < `candidate_k=64`(`rag/config.py:47`); `ticker='AAPL'` 필터 실측 **64개 요청에 12개 반환**. 코퍼스가 클수록 티커 선택도(현 ~0.5 %)가 떨어져 핵심 쿼리 형태의 dense 회수가 0으로 수렴.
  - **메모리 대비 성장** — HNSW 8.4 GB(ING-1 주석 시점 6.5 GB에서 30 % 성장) vs shared_buffers 2 GB; dense 콜드 6.2 s. 현 유니버스로도 **연 +2-3 M청크 ≈ HNSW +15-23 GB/DB +40-60 GB**; `us_all/kr_all` 확장 시 ×6-9. 보존/만료 경로는 어디에도 없음(뉴스 포함).
- **해결 방향:** trgm 레그 제거(짧은 필드 전용으로 전환) + 검색 커넥션에 `SET hnsw.iterative_scan=relaxed_order`·`ef_search≥candidate_k`·`SET LOCAL statement_timeout`; 뉴스 age-out; RAM을 인덱스 크기에 맞춰 산정(프로덕션 = AlloyDB 분리, `USER_TODO.md` 1-10).

### CR-9 · Yahoo 라이브 프록시 무캐시 — 한 IP에서 턴당 호출, 브레이커 오픈 = 전 유저 가격 다운
- **증거:** `datasets/app/providers/us/yahoo.py` 캐시 0; `/prices`·`/prices/snapshot`(`routers/prices.py:26-51`)이 에이전트 툴콜마다 라이브(KR은 `.KS`→`.KQ` 2배, `yahoo.py:24-27`). 마켓 오버뷰 팬아웃: themes 32·sectors 13·asset-classes 10·commodities 7 심볼 동시(`store/cross_asset.py:47-51`), `/prices/snapshot/market`는 **무제한 gather ≤100심볼**(`routers/_common.py:41-55`, 세마포어 없음). 429 재시도는 5.5 s 슬립 포함 4× 증폭(`app/http.py:19-58`), Retry-After 무시.
- **스케일 증상:** 유저 50명이면 시간당 수천 콜이 단일 egress IP에서 → Yahoo 429/차단 → 서킷브레이커가 **프로바이더 전역 60 s 오픈**(`http.py:26-33`) = 모든 유저의 가격 기능 동시 다운(Stooq 폴백은 EOD 전용).
- **해결 방향:** 심볼+레인지 키 30-120 s 공유 TTL 캐시(뉴스 dedup 패턴 재사용, `providers/news.py:87-105`) + 팬아웃 세마포어 + 프로바이더별 클라이언트측 토큰버킷; 가능한 곳은 인제스트된 `price_bars`에서 서빙.

### CR-10 · 보안 게이트 3종 — 트래픽 무관, 공개 전 필수
- **admin :8005** — 기본 `admin/admin` + 공개 데브 세션 시크릿(**쿠키 위조 가능**), 로그인 레이트리밋 0, 프로덕션 가드 **전무**, 0.0.0.0 공개 포트에 전 서비스 DB CRUD (`admin/adminpanel/config.py:15-17`, `main.py:77-98`, compose `${ADMINUI_USERNAME:-admin}`).
- **데브 시크릿이 7개 중 5개 서비스에서 무가드 통과** — 가드는 control-plane(admin_token)·studio-api뿐. **`AUTH_SECRET=dev-secret-change-me` 가드 없음 → Auth.js JWT 위조 가능**; datasets는 `DATASETS_API_KEYS` 미설정 시 **아무 키나 수용**(`datasets/app/deps.py:19-29`); rag/agent-engine 데브 토큰 묵인; `web/lib/studio.ts:6-13`은 미설정만 검사하고 데브 **값**은 통과; postgres `rag:rag` 하드코딩. `env/secrets.env.example:3-5`의 "기동 거부" 주장은 2/7만 사실.
- **대화 IDOR** — `GET /conversations/{id}/messages`(`studioapi/main.py:229-243`)와 `POST /conversations/{id}/stop`(`:179-184`)에 **소유권 체크 없음**(active-run에는 있음, `:256-259`) — ID는 비추측성이지만 유출 시 타인 대화 열람/중단 가능.
- **해결 방향:** 공용 `assert_production_secrets`를 7개 서비스 전부에(모든 데브 기본값 열거) + admin 프라이빗 바인딩/allowlist/레이트리밋 + 두 엔드포인트 `get_owned` 추가.

### CR-11 · 백업 전무 — 디스크 1개 유실 = 테넌트·키·빌링·원장 전체 유실
- **증거:** `archive_mode=off`, pg_dump/wal-g/pgbackrest 리포 전체 0건; 영속성 = 단일 호스트 로컬 볼륨 `pg_data`(22 GB). 인보이스·크레딧 원장·구독이 여기에만 존재.
- **해결 방향:** (오너 액션, USER_TODO 1-11) 최소 야간 pg_dump + 오프호스트 복사 지금 즉시; 프로덕션은 controlplane+studio(빌링)부터 PITR 있는 매니지드로. rag 코퍼스는 재생성 가능하나 재임베딩 비용 — 스냅샷 대상.

---

## 3. HIGH

### HI-1 · studio-api: 전 핸들러가 이벤트 루프 위 동기 SQLAlchemy + 세션을 네트워크 콜 건너 유지
`studioapi/db.py:45-53` 동기 엔진(기본 풀 5+10, **pre-ping 없음** — control-plane 동일 `controlplane/db.py:45-48`). 모든 `async def` 핸들러가 루프 위 동기 DB(`main.py` 전반, 턴당 쿼터 카운트 3-5회 `quotas.py:111-165`, **인증 요청마다** `ensure_user` `provision.py:45-66`). askfeed는 **45 s LLM 콜 동안 세션 유지**(`askfeed.py:218-226`), standing은 세션 들고 게이트웨이 직렬 프로브 N회(`standing.py:132-160`). 이 루프가 동시에 전 SSE 스트림을 펌프한다 → 토큰 스트리밍 버벅임 + 15커넥션 고갈 = 30 s pool_timeout 후 500. **Fix:** async 엔진(또는 일관된 `to_thread`) + 풀 사이징/pre-ping + 세션-네트워크 분리.

### HI-2 · 게이트웨이 인메모리 레이트리미터·플랜 캐시 — 레플리카 N배
`controlplane/ratelimit.py:12-26`(독스트링 스스로 "single-process dev/staging") + `gateway.py:39-52` 플랜 캐시. N대면 한도 N배, 재시작 시 리셋. `settings.redis_url`(`config.py:22`)은 **배선 안 됨**. **Fix:** Redis/PG 토큰버킷 (인터페이스는 이미 백엔드 중립).

### HI-3 · 의미상 글로벌인 프로세스-로컬 캐시 — **web+worker 2프로세스인 지금도 어긋남**
`datasets`는 이미 2프로세스(웹+워커)가 같은 코드를 돌린다: OpenDART 쿼터 블록 `_blocked_keys`(`providers/kr/opendart.py:101-128` — 워커가 소진 판정해도 웹은 계속 태움) · 서킷브레이커(`app/http.py:23`) · KIS OAuth 토큰(발급 ~1/분 제한, `kr/kis.py:19-45`) · 뉴스 2분 dedup(`providers/news.py:90-105` — "동시 유저 폭풍→1콜" 보장이 프로세스 내에서만) · TTLCache(SEC/DART 대형 페이로드 프로세스별 중복 다운로드). datasets `redis_url`(`app/config.py:92`)도 미배선. **Fix:** 쿼터 블록·KIS 토큰·브레이커·뉴스 dedup 4종부터 Redis로.

### HI-4 · TTLCache는 만료 엔트리를 영원히 안 지움 — OOM 경로
`app/cache.py:32-51` — 만료돼도 덮어쓸 때만 교체, `_inflight` 락도 무한 누적. `sec:facts:{cik}`는 파싱된 companyfacts dict가 **수십 MB**(`sec_edgar.py:102-110`), 500종목 스윕이면 워커에 수 GB 고정 + 웹 프로세스가 사본 하나 더. **Fix:** 바운디드 LRU + 만료 스윕; 대형 페이로드는 파싱 결과(추출 행)만 캐시.

### HI-5 · httpx 클라이언트를 콜마다 신규 생성 + 턴마다 카탈로그 fetch
agent-engine 툴콜/카탈로그마다 새 클라이언트(`agentengine/client.py:23,74` — 턴당 ~30회 TCP 핸드셰이크), studio-api ~20곳(`chat.py:116`은 **timeout=None**), rag 텔레메트리는 임베드 배치마다 신규 클라+detached task(`rag/embeddings.py:142-165`, `rerank.py:53-73`). 카탈로그는 준정적인데 **매 턴 게이트웨이 왕복**(`chat.py:296`, 재시도 3회). 게이트웨이 공유 클라는 기본 Limits(최대 100커넥션)라 그게 곧 동시성 캡(`gateway.py:24`). **Fix:** 서비스당 모듈 공유 `AsyncClient`+명시적 Limits, 카탈로그 TTL 캐시, 텔레메트리 배치화.

### HI-6 · 턴 쿼터 check-then-insert 레이스 + 복합 인덱스 부재
`quotas.py:111-165` COUNT→INSERT 무락(+게스트 `turns_used` RMW `:158-164`) — K병렬 턴이면 캡을 K-1 초과. `turn_usage`에 `(user_email, day)` 복합 인덱스 없음(`models.py:203-208`) + 게스트 턴마다 IP 형제 세션 조회 2회(`quotas.py:54-70`). **Fix:** 원자 INSERT…SELECT 가드(또는 유저 행 FOR UPDATE) + 복합 인덱스.

### HI-7 · `filing_search`가 요청 경로에서 동기 인제스트 (≤1200 s) — single-flight 없음
`app/routers/filings.py:66-71` — 히트 0이면 라이브로 공시 목록+HTML fetch+수백 청크 임베딩. 같은 콜드 티커 2명 = **중복 임베딩 지출**(replace_scope 락은 DB 스왑만 직렬화). 공시가 원래 없는 티커(ETF)는 검색마다 재트리거. **Fix:** (market,ticker) single-flight + 큐 위임("색인 중" 정직 응답) + 네거티브 캐시.

### HI-8 · RAG 검색 1회 = Gemini 2-4콜 + Vertex rerank 1콜(과금) — 결과 캐시 0
쿼리 확장 1(flash-lite, `search.py:31-58`) + 변형별 embed_query ≤3(`:84,113-116`) + rerank(`rerank.py:39-50`). 워스트 지연 스택 ~26 s + embed RPM 쿼터 경합. **Fix:** (query,filters)→hits 단기 공유 캐시 + 임베딩 텍스트해시 캐시 + 3변형 embed **1배치 호출**로 합치기.

### HI-9 · 스턱 런 영구 잔류 + 동시 런 시작 무제한
`studioapi/chat.py:116` `timeout=None` → 엔진이 wedge되면 런은 영원히 "running"; `_prune`은 finished만 제거(`runs.py:60-65`), 완료 런도 done 이벤트에 전체 인용/아티팩트 페이로드 300개 유지. 런 시작에 전역 동시성 캡 없음(쿼터는 유저별 카운트일 뿐) → 500 동시 턴 = 드라이버 태스크 500 + 버퍼 500. **Fix:** 런 전체 데드라인 워치독 + running 프룬 + done 페이로드 드랍(메시지에 이미 영속) + 전역 세마포어.

### HI-10 · 페이지네이션 전무 — 대화·메시지·알림 틱 풀스캔
`main.py:190-192` 전체 대화, `:231-243` 전체 메시지(아티팩트 JSON이 메시지당 수백 KB) 무제한 반환. 알림 틱은 60 s마다 active 전건 로드(`scheduler.py:51-63`, `next_fire_at` 인덱스/필터 없음) 후 직렬 fire(디지스트당 순차 fetch 8회, `alerts_render.py:117-137`) — 틱 > 60 s부터 지연 무한 누적. **Fix:** LIMIT+커서, `WHERE next_fire_at<=now` 인덱스 + SKIP LOCKED 클레임 + 워커 팬아웃.

### HI-11 · 관측성 0 — 홉마다 다른 request-id, 메트릭·트레이싱 없음
전 서비스가 자기 request-id를 **생성**만 하고 인바운드 `X-Request-ID`를 읽지 않음(`*/logging_config.py:121,135`) → 한 턴이 4홉에서 4개 id, 크로스 서비스 grep 불가. 지속 저장되는 지연은 게이트웨이→데이터플레인 홉뿐. **Fix:** id 전파(게이트웨이 `_HOP` 스트립 리스트는 이미 통과시킴) + SSE 스테이지 duration + 서비스당 최소 Prometheus 익스포터.

### HI-12 · 전체 유니버스 인제스트가 티커·접수번호 이중 직렬 — 첫 풀런 1-8일
`filing_ingest.py:212-234`(티커 직렬)·`:164-183`(접수번호 직렬); procrastinate 락 `pipe:{pipeline}:{market}`(`queue.py:101-104`)라 워커 concurrency=4가 파이프라인 내에선 무효. 임베드+삽입 실측 0.15-1.5 s/행 → 500종목 풀런 ≈ 50만 청크. 델타가 평시를 구제하지만 재청킹 변경 시 풀 경로 강제. **Fix:** 티커 배치 단위 잡 샤딩(락도 배치별) + `COPY`/pipeline-mode 삽입(현 `executemany` 행당 왕복, `store.py:245-249`).

### HI-13 · `usage_events`+`audit_log` 콜당 2행 무한 증식 — 보존·파티셔닝·롤업 없음
100 req/s → **일 ~2.6 GB**, OLTP와 같은 인스턴스. 어드민 `/costs` 30일 스캔에 `ts` 인덱스 없음(`admin/adminpanel/main.py:812-814`); 설정 화면의 usage 집계는 프로젝트 **전 이력** 스캔(`controlplane/admin.py:202-216`). **Fix:** 월 파티션+드랍 잡 + nightly 롤업 테이블 + `(project_id, ts)` 인덱스 (CR-4 배치화와 한 세트).

---

## 4. MEDIUM

| # | 발견 | 증거 | 방향 |
|---|---|---|---|
| ME-1 | 데스크피드 single-flight 없음(요청 안 45 s 생성, 탭 2개=2배) + standing 프로브 직렬 인라인 | `deskfeed.py:97-148`, `standing.py:132-172` | 스코프 락 + 백그라운드 생성/serve-stale |
| ME-2 | 배포 후 reconcile 허드 — `_reconciled` 프로세스-로컬, 유저 첫 요청마다 활성화 POST 9-11회 | `provision.py:40-67`, `plans.py:123-135` | `connectors_reconciled_at` 컬럼(플랜 버전 키) |
| ME-3 | 부팅 시딩/`create_all`/`ALTER` 레이스 — 레플리카 동시 부팅 시 IntegrityError 크래시루프 가능 | `main.py:50-52`, `db.py:56-104`, `agents.py:79-100` | advisory lock 또는 마이그레이션 잡 분리 |
| ME-4 | 게스트: 루트 방문마다 쿠키 → User 행 무한 증식(크롤러 포함), TTL 정리 없음; 전 게스트가 **글로벌 키 1개 240/min** 공유(제품 전역 상한); CGNAT면 IP 형제 IN절 수천 개 | `web/middleware.ts:9-15`, `guest.py:99-116`, `plans.py:40-41`, `quotas.py:54-70` | 첫 챗에만 쿠키 + TTL 클린업 잡 + JOIN 카운트 + IP 버킷 레이트 |
| ME-5 | 백그라운드 피드가 **임의 유저의 테넌트 키**로 과금/레이트 소비 (`_any_api_key`) — 그 키 폐기 시 피드 사망 | `askfeed.py:51-52,100-110,239-245` | 전용 서비스 테넌트/키 (guest.py 패턴 재사용) |
| ME-6 | 메시지/공유 페이로드 비대: 아티팩트 JSON Text 인라인, `og_image` base64 ≤4 MB가 DB 행, 공개 공유 **읽기마다 쓰기**(referral ensure+views UPDATE 핫로우) | `models.py:136,226-237`, `shares.py:138-181` | 사이드 테이블/오브젝트 스토리지, referral은 생성 시점, views는 카운터 테이블/샘플링 |
| ME-7 | `market.py` 유저별 무한 인메모리 캐시 — **전역 동일한** pulse를 유저별로 저장, watch는 유저당 120 s마다 스냅샷 8콜 | `market.py:25-93` | 전역 캐시 + LRU 바운드 + 티커별 공유 캐시 |
| ME-8 | admin 패널: `/db` 인덱스가 전 DB 전 테이블 `COUNT(*)`(스케일에서 분 단위) + 행 주소가 **ORDER BY pk OFFSET**(동시 삽입 시 **다른 행을 수정/삭제**할 수 있음) + 3개 DB import-시점 reflect | `admin/adminpanel/state.py:39-55`, `db_browser.py:95-202` | reltuples 추정치 + PK 주소 지정 + lazy reflect |
| ME-9 | 로그 무한: compose `logging:` 캡 없음(json-file 무제한, pg와 같은 디스크), 시크릿 redaction은 datasets 포매터만 | `docker-compose.yml`(전 서비스), `datasets/app/logging_config.py:30-32` | max-size/max-file 지금 즉시 + redaction 공용화 |
| ME-10 | evidence_docs 디스크 GC 전무(인용 URL마다 파일, 덱 PDF 40 MB 캡×유니버스, 버전 키 전환 시 사체 잔류) | `store/source_html.py:101-173`, `deck_ingest.py:31-48` | atime LRU 스위퍼 + 디렉터리 예산 |
| ME-11 | SEC 클라이언트측 레이트리미터 없음(10 r/s 가이드라인 — 스윕+유저 턴 겹치면 IP 차단 실사례 있는 규정); `fetch_bytes`는 재시도·브레이커 **둘 다 우회** | `sec_edgar.py`, `app/http.py:102-112` | 프로바이더별 토큰버킷 + fetch_bytes 경로 통합 |
| ME-12 | Gemini 비용 레버 방치: plan 컨텍스트 무절단(스텝↑=초선형), 합성 상시 pro, LLM 호출마다 신규 `genai.Client`+fire-and-forget usage POST | `gemini_io.py:75-81`, `enrichment.py:318,412,471`, `usage.py:32-54` | 절단/요약 + 클라 재사용 + 텔레메트리 배치 |
| ME-13 | worker/web/admin 헬스체크 없음(**인제스트 전담 worker가 조용히 wedge 가능**), 컨테이너 리소스 리밋 전무(폭주 1개가 호스트 OOM) | compose 전반 | procrastinate 하트비트 프로브 + deploy.resources |
| ME-14 | Toss 가드가 `TOSS_SECRET_KEY` 설정 시에만 활성 — 키 잊은 프로덕션이 **FakeGateway로 조용히 기동**; `BILLING_ENC_KEY` 하드코딩 폴백 | `config.py:81-85`, `billing.py:47-50` | CR-10 공용 assert에 편입 |
| ME-15 | 빌링 틱 직렬 처리(건당 ≤15 s) — 동일일 갱신 수천 건이면 틱이 수 시간, 던닝 드리프트 | `billing.py:381-432` | 바운디드 동시 청구 + CR-2 상태 가드 |
| ME-16 | 뉴스 코퍼스 무보존(doc_id=url 업서트만, 옛 청크 인덱스에 영구 잔류) — recency 부스트가 가릴 뿐 제거 안 함 | `news_ingest.py:38`, `search.py:123-134` | age-out 삭제 잡 |

## 5. LOW / 건강한 부분

- **web BFF는 무상태·순수 프록시** — 지금 그대로 수평 확장 가능한 유일한 티어 (`web/lib/studio.ts`, JWT 쿠키, 폴링 루프 없음, 로고 `max-age=604800`). 준정적 JSON(커넥터/템플릿) BFF 캐시는 공짜 헤드룸.
- **임베딩 비용은 비이슈** — 1.1 M청크 전체 코퍼스 ≈ 한 자릿수 달러(실측 56.5 M토큰); 쿼리 임베드는 ~무료.
- **큐 메커니즘 건강** — procrastinate LISTEN/NOTIFY+5 s 폴백, DB 부하 미미; 문제는 동거(§CR-8)뿐.
- **키 해싱 sane**(SHA-256+compare_digest — bcrypt-per-request 함정 없음) · **웹훅 멱등**(event_id UNIQUE) · CORS 전 서비스 미설정(BFF 구조상 무해).
- 소형 무보존 테이블(email_otp·webhook_events·card_taps·turn_usage)·게이트웨이 응답 전체 버퍼링·`existing_texts` 단일 ANY(%s)·렉시컬 OR 이중 실행(웜 0.75 s)은 인지만 해두면 됨.

---

## 6. 조치 로드맵 — M-SCALE

> 원칙: **순서가 곧 위험 순서다.** SC-0은 트래픽 0에서도 필수(보안·유실), SC-1은 첫 유저 트래픽의 상한을 올리고,
> SC-2가 끝나기 전에는 **어떤 서비스도 2레플리카로 늘리면 안 된다**(중복 발송·중복 과금·404가 즉시 발생).
> 각 태스크는 ROADMAP 규칙대로 1태스크 1PR.

### SC-0 · 보안·유실 방지 (공개 전 필수 — 코드 소량)
| 항목 | 커버 |
|---|---|
| 공용 `assert_production_secrets` 7개 서비스 전부 (AUTH_SECRET·SERVICE_TOKEN 값·ADMINUI_*·datasets keys·BILLING_ENC_KEY·TOSS 유무·pg 패스워드 파라미터화) | CR-10, ME-14 |
| admin :8005 잠금 — 프라이빗 바인딩/allowlist + 로그인 레이트리밋 + 데브 크레덴셜 기동 거부 | CR-10 |
| `messages`/`stop` 소유권 체크(`get_owned`) | CR-10 |
| 야간 pg_dump + 오프호스트 (오너 액션 → USER_TODO 1-11) | CR-11 |
| compose 로그 캡(max-size/max-file) + redaction 공용화 | ME-9 |

### SC-1 · 단일 노드 처리량 (첫 트래픽 전)
| 항목 | 커버 |
|---|---|
| 플래너 `synthesis_override` → 호출 인자화 (한 줄급, **최우선**) | CR-6 |
| Gemini: 티어별 세마포어 + intake/plan/합성 429 백오프 + plan 컨텍스트 절단 + `client.aio`(또는 사이즈드 executor) + genai 클라 재사용 | CR-5, ME-12 |
| 게이트웨이 meter/audit 배치 큐 + auth/엔타이틀 TTL 캐시 | CR-4 |
| rag `psycopg_pool` + trgm 레그 제거 + `ef_search`/`iterative_scan` + `statement_timeout` | CR-7, CR-8 |
| Yahoo TTL 캐시 + 팬아웃 세마포어 + 프로바이더 토큰버킷(SEC 포함) + fetch_bytes 경로 통합 | CR-9, ME-11 |
| 공유 httpx 클라이언트(agent-engine·studio·rag 텔레메트리) + 카탈로그 TTL 캐시 | HI-5 |
| studio/control-plane 풀 사이징+pre-ping + 세션-네트워크 분리(우선 askfeed/standing) | HI-1 |
| 쿼터 원자화 + `(user_email, day)` 복합 인덱스 | HI-6 |
| filing_search single-flight + 큐 위임 + 네거티브 캐시 | HI-7 |
| TTLCache 바운디드 LRU + 만료 스윕(대형 페이로드는 추출 행만) | HI-4 |
| 런 데드라인 워치독 + running 프룬 + done 페이로드 드랍 + 전역 동시 런 캡 | HI-9 |
| RAG 검색 결과/임베딩 캐시 + 3변형 1배치 임베드 | HI-8 |

### SC-2 · 멀티 레플리카 준비 (레플리카 늘리기 전 전부 필수)
| 항목 | 커버 |
|---|---|
| 런 상태 외부화(PG 이벤트 테이블/Redis Streams) + 죽은 런 쿼터 환불 | CR-1 |
| 빌링 락 틱 전체 유지 or `FOR UPDATE SKIP LOCKED` + 인보이스 `status='charging'` 원자 가드 | CR-2, ME-15 |
| alerts/askfeed/온보딩 킥 → advisory lock 또는 procrastinate 이관 + 피드 스코프 single-flight + `ON CONFLICT` | CR-3, ME-1 |
| Redis 배선: 레이트리미터 + OpenDART 쿼터 블록·KIS 토큰·서킷브레이커·뉴스 dedup | HI-2, HI-3 |
| 부팅 시딩/마이그레이션 advisory lock | ME-3 |
| reconcile 컬럼화 + 게스트 TTL 클린업 + 전용 백그라운드 테넌트 키 | ME-2, ME-4, ME-5 |

### SC-3 · 데이터 증식·관측·비용 (트래픽 램프와 병행)
| 항목 | 커버 |
|---|---|
| usage/audit 월 파티션 + 보존 드랍 + nightly 롤업 + `(project_id, ts)` 인덱스 | HI-13 |
| evidence_docs GC + 뉴스 age-out + og_image/아티팩트 스토리지 분리 | ME-10, ME-16, ME-6 |
| request-id 전파 + 스테이지 duration + 최소 메트릭 익스포터 | HI-11 |
| rag → AlloyDB 분리(USER_TODO 1-10) + controlplane/studio 매니지드 PITR | CR-8, CR-11 |
| 인제스트 잡 샤딩 + COPY 삽입 | HI-12 |
| 페이지네이션(대화·메시지) + 알림 틱 인덱스/클레임 | HI-10 |
| worker 헬스체크 + 리소스 리밋 + pg 전용 노드 분리 | ME-13 |

### 멀티 레플리카 준비도 판정 (한 줄 요약)
- **지금 레플리카를 늘려도 되는 티어: web뿐.**
- studio-api: CR-1·CR-2·CR-3·ME-3 전부 해결 전 **금지** (중복 과금 시도·중복 알림·재개 404).
- control-plane: HI-2 해결 전 레이트리밋 무력화.
- datasets/worker: HI-3 해결 전에도 이미 2프로세스 간 쿼터/브레이커 어긋남 — Redis 배선이 선행.
- agent-engine·rag: 상태는 없지만 CR-5/CR-7을 안 고치면 레플리카가 병목(키 RPM·pg 커넥션)을 나눠 갖기만 한다.
