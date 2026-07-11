# RAG_INGEST_BATCHING_SPEC — 인제스트 청크 배치 분할 · 원자성 개편 (ING-1)

> **Status**: ⬜ planned (2026-07-12 작성 · ROADMAP **ING-1**) · 의존: OPS-2 (델타 인제스트/커서)
> **Scope**: `rag/`(스토어·인제스트·임베딩) + `datasets/`(인제스트 클라이언트·커서·로그). 검색 경로·청킹 알고리즘·API 외형은 바꾸지 않는다.
> **One-liner**: 대형 공시 인제스트를 "빠르고(동시 임베딩), 절대 깨지지 않게(원자적 prune-swap), 끊겨도 무비용 재시도(멱등)"로 만든다.

---

## §0. 문제 정의 — 검증된 실패 모드 5가지

2026-07-11 OPS-2 라이브 검증 중 AAPL 10-K `filing_text` 인제스트가 `ReadTimeout ×1 (AAPL)`로 실패했다.
원인 조사에서 확인된 사실(전부 코드/로그/DB로 검증됨):

| # | 실패 모드 | 근본 원인 (file:line) |
|---|---|---|
| 1 | **배치 간 replace 비원자성** — 중간 배치 실패 시 잘린 공시가 완전한 것처럼 검색에 서빙됨 | `_ingest_to_rag`가 40-doc 배치로 나눠 보내며 `replace`를 **배치 0에만** 실음 → 배치 0에서 delete-all 커밋, 이후 배치 실패 시 부분 상태 잔존 (`datasets/app/store/news_ingest.py:66-74`) |
| 2 | **요청 내 비원자성** — 임베딩 실패 시 접수번호 전체가 코퍼스에서 소실 | `delete_where`가 임베딩 **전에** 커밋됨 (`rag/rag/ingest.py:24-25` → embed는 `:32`) |
| 3 | **임베딩 wall-time** — 40-doc 배치 1개가 436초 (실측, rag 로그 `POST /rag/ingest 200 436504.8ms`) | 64텍스트 Gemini `embed_content` 호출이 **순차** 실행 (`rag/rag/embeddings.py:84-102`), 부하 시 호출당 ~109초 |
| 4 | **고아 작업 / 이중 지출** — 클라이언트는 300초에 포기(`ReadTimeout`), 서버는 계속 일해서 200 반환 | datasets 클라이언트 timeout 300s (`news_ingest.py:52`) < 서버 작업 시간; rag 핸들러엔 자체 예산 없음 |
| 5 | **커서 조립도(coarseness)** — 접수번호 1개 실패가 티커 전체 재작업으로 확대 | `mark_items`가 티커 루프 **종료 후** 일괄 실행 (`datasets/app/store/filing_ingest.py:169-172`; kr_earnings/transcript/deck 동일 패턴) |

**라이브 증거** (2026-07-11):
- rag 로그: `← POST /rag/ingest 200 436504.8ms` — datasets는 300초에 ReadTimeout, rag는 7분 뒤 완주(고아 커밋).
- 코퍼스: AAPL `0000320193-26-000013`(137청크)·`0000320193-26-000006`(97청크) — 배치 0~1만 반영된 **잘린 상태 의심** (구 청킹의 완전한 10-K는 203~205청크).
- 잡 노트: `0/1 tickers indexed, 0 chunks · FAILED ReadTimeout ×1 (AAPL)` — 커서 미마킹(다음 델타가 자연 재시도).

**숨은 비용 버그(§1-B가 함께 고침)**: replace 경로에선 delete가 `existing_texts`(변경 없음 스킵, `ingest.py:28-29`)보다 먼저라, **변경 없는 공시 재인제스트도 배치 0 전량이 재임베딩**된다 — 증분 스킵이 replace 인제스트에서 사실상 무력화돼 있다.

**논-골(non-goals)**: 비동기 202+폴링 인제스트 API 도입 · 청킹 알고리즘 변경 · 검색 경로 변경 · 코퍼스 리샤딩. (이유: 아래 설계로 충분하며, 위 항목들은 리스크 대비 이득이 없다.)

---

## §1. 아키텍처 결정 (A–G)

### A. replace-스코프당 **단일 POST** (클라이언트 배치 분할의 재정의)
- `replace`를 싣는 인제스트(filing_text · kr_earnings · transcript · presentation)는 **접수번호(스코프)당 모든 docs를 한 요청**으로 보낸다. 40-doc 클라이언트 배칭은 **replace 없는 피드**(news `news_ingest.py:126`, era_news `era_news_ingest.py:85`)에만 유지.
- 근거: 서버 측 prune-swap(B)이 "이 스코프의 새 청크 id 전체 집합"을 알아야 stale 행을 계산한다. 페이로드는 유계 — 섹션 상한 6,000자(`filing_ingest.py:29 _SECTION_CHARS`)라 500섹션 10-K도 ~3MB JSON이고, 경로는 datasets→rag **직통**(`http://rag:8002`, 게이트웨이 미경유)이라 문제없다.
- **배포 순서: 서버(B) 먼저, 클라이언트(A) 나중.** 신구 조합 안전성:

| 조합 | 동작 |
|---|---|
| 구 클라(배치) + 신 서버 | 배치 0의 prune이 배치 1..n의 구 행을 지우고, 그 배치들이 곧 재업서트 → **현행과 동일한 최종 상태/일시 창** (악화 없음) |
| 신 클라(단일) + 구 서버 | 정상 동작하나 요청 내 비원자성(2번)은 잔존 → B 배포로 해소 |

### B. rag **원자적 prune-swap** (핵심 결정)
`ingest_docs`(`rag/rag/ingest.py:11-34`)를 다음 순서로 재배열한다:

```
chunk 전체 생성
→ existing_texts (어떤 삭제보다 먼저!)          # 변경 없음 스킵이 replace에서도 살아남
→ todo = 신규 id 또는 텍스트 변경분만 embed      # 스토어 무변이(no mutation) 구간
→ [단일 트랜잭션] scope 내 stale 행 DELETE + todo upsert   # prune-swap
```

신규 스토어 프로토콜 메서드(양 백엔드 패리티):

```python
async def replace_scope(self, filters: dict, keep_ids: list[str],
                        chunks: list[Chunk], vectors: list[list[float]]) -> int:
    """ONE atomic transaction: DELETE rows matching `filters` whose id is NOT in
    `keep_ids`, then upsert `chunks`+`vectors`. Returns the pruned row count."""
```

구현 세부 (전부 준수할 것):
1. **keep-set = 이 요청의 새 청크 id 전체**(`[c.id for c in chunks]`) — `todo`만이 아님. 변경 없어 스킵된 청크가 프룬되면 안 된다.
2. **`todo`가 비어도 프룬은 실행** — 텍스트는 그대로인데 과거 청킹이 남긴 stale id(예: 재청킹으로 섹션 수 감소)를 제거해야 한다. 단, **청크가 아예 0개면 절대 삭제하지 않는다**(빈 추출이 멀쩡한 기존 인제스트를 날리지 못하게 — 현행 가드 `ingest.py:22-24` 의미 유지).
3. **PgVectorStore**: 한 커넥션에서 `SELECT pg_advisory_xact_lock(hashtext(<scope key>)::bigint)` → `DELETE FROM rag_chunks WHERE <conds> AND NOT (id = ANY(%s))` → `executemany` upsert(기존 `upsert`의 SQL 재사용, `store.py:209-213`) → `commit()`. scope key = replace 필터의 정규 직렬화(정렬된 `k=v` join). `ANY(array)`라 keep-set 수천 개도 파라미터 한도 문제 없음.
   - **advisory lock이 필요한 이유**: 주간 스윕(procrastinate lock은 `pipe:filing_text:{market}` 단위, `queue.py`)과 **온디맨드 인제스트**(`datasets/app/routers/filings.py:69` filing_search의 라이브 인제스트)가 같은 접수번호를 동시에 스왑할 수 있다. 락으로 스왑을 직렬화 — 경쟁 시 임베딩 중복은 발생할 수 있으나(지출), 코퍼스 오염은 불가능해진다.
4. **MemoryStore**: 메서드 본문을 **순수 동기**로(중간 `await` 금지) 작성 → asyncio 하에서 원자적. `delete_where`의 `_keep` 술어(`store.py:120-132`)+인라인 upsert 재사용. 유닛테스트 패리티가 목적.
5. **필터 시맨틱은 `delete_where`와 동일**: `meta->>k = v` 정확 일치, `None` → `IS NULL` (`store.py:312-320`). 검색용 `_where`의 tenant-OR-NULL 시맨틱(`store.py:333-346`)과 혼동 금지. **조건 빌더를 `delete_where`에서 분리해 공용화**한다.
6. `ingest_docs`는 `(embedded, pruned, skipped)`를 담은 결과(dict 또는 dataclass)를 반환; `delete_where`는 다른 용도(테넌트 삭제 등)로 존치.

효과: 실패 모드 **1·2 소멸**(스코프는 항상 "완전한 구판" 아니면 "완전한 신판"), 그리고 리오더 덕에 **변경 없는 재인제스트 = 임베딩 0회** → 클라이언트 재시도가 사실상 무비용이 된다.

### B2. replace에 tenant **무조건** 고정 (하드닝)
- 현행 `rag/rag/main.py:80`은 헤더가 있을 때만 replace에 tenant를 붙인다 → 글로벌(무헤더) replace가 **같은 접수번호를 가진 테넌트 행까지** 지울 수 있다(기존 `delete_where`에도 있던 위험; B의 prune에서는 테넌트-네임스페이스 id `{tenant}::{doc_id}::{i}`(`models.py:68-80`)가 글로벌 keep-set에 없어 더 위험).
- 수정: `replace = {**body.replace, "tenant": tenant}`를 **항상** 적용 (tenant가 `None`이면 `meta->>'tenant' IS NULL` — 글로벌/프리-테넌트 행만 매치). 동작 변경은 "좁히기"뿐.

### C. 임베딩 **동시성** (throughput)
- `GeminiEmbedder._embed`(`embeddings.py:84-102`)의 순차 루프를 `asyncio.Semaphore` + `asyncio.gather`로 교체.
- 신규 설정: `RAG_EMBED_CONCURRENCY`(기본 **4**) · `RAG_EMBED_BATCH`(기본 **64** — 기존 모듈 상수 `_BATCH` 승격). `=1`이면 현행 순차와 동일 — **무배포 롤백 노브**.
- **순서 보존**: gather는 위치 보존 — 순서대로 flatten (벡터가 `todo`와 정렬돼야 함). `self.dim` 설정은 flatten 후. `_report_usage`는 `_embed`당 1회 유지.
- **재시도는 현행 유지**: `_with_retry`(`embeddings.py:29-52`, 429/5xx에 2/4/8s)는 `to_thread` 워커 안의 `time.sleep`이라 자기 스레드만 블록 — async 재작성 불필요. 동시성으로 429 압력이 ~4배 오르나 기존 백오프가 흡수; 실측에서 429가 잦으면 노브를 2/1로 낮춘다(프로젝트의 Gemini 티어 RPM/TPM은 롤아웃 때 확인, 하드코딩 금지).
- 배치 크기는 64 유지(선제 축소 금지 — 반으로 줄이면 호출 수가 2배; 부하 지연은 동시성으로 먼저 공략하고 운영에서 설정으로 튜닝).
- 서브배치별 INFO 로그: index·size·소요 ms (§F 관측성 입력).
- 기대 효과: 병리 케이스 109s/호출 × N 순차 → **~N/4 wall-time**. 대형 10-K(~3,000청크) 병리 ~85분 → ~21분, 정상 지연 ~3분 → ~45초.

### D. 예산 정렬 — 서버 소프트 버짓 **없음**, 클라이언트 스케일 타임아웃 + 1회 재시도
- **rag 쪽 요청 예산을 두지 않는다**: 스왑이 원자적이면 서버가 끝까지 완주하는 게 정답이다 — 클라이언트가 포기한 "고아 요청"이 **커밋된 인제스트**가 되고, 클라이언트 재시도는 리오더 덕에 임베딩 0회로 확인만 하고 끝난다(어제의 436초-고아 케이스가 정확히 이렇게 치유됨). 중간 포기(soft budget)는 순수 낭비만 만든다.
- **클라이언트 read timeout을 문서 수에 비례**시킨다: `read = min(base + per_doc × len(docs), max)`.
  - datasets 신규 설정: `rag_ingest_timeout_base_seconds=120` · `rag_ingest_timeout_per_doc_seconds=6.0` · `rag_ingest_timeout_max_seconds=1200`.
  - 캘리브레이션: 실측 병리 ~11s/doc(순차) ÷ 4(동시성) ≈ 2.75s/doc → 6s/doc은 2배 마진.
  - 스칼라 하나가 아니라 `httpx.Timeout(connect=10, read=X, write=60, pool=10)`로.
- **1회 재시도** in `_ingest_to_rag`: `httpx.TransportError`(ConnectError/ReadTimeout/RemoteProtocolError 포괄) 및 5xx 응답에 한해 ~15초 후 1회. **4xx는 즉시 실패**(재시도 금지). B 이후 안전: 최악이 스코프 1개분 임베딩 중복 지출이고, advisory lock이 스왑을 직렬화하며, 두 시도 모두 동일 내용을 커밋한다.
- procrastinate 잡 레벨 `RetryStrategy(max_attempts=3)`(`queue.py:49-53`)은 무변경 — 티커별 catch 구조상 잡 재시도는 대참사에서만 발동하고, E+멱등 인제스트로 재실행이 저렴하다.

### E. 커서 세분화 — **아이템 성공 직후 마킹**
- 4개 루프(`filing_ingest.py:160-172` · `kr_earnings_ingest.py:57-73` · `transcript_ingest.py:92-102` · `deck_ingest.py` per-deck 루프) 모두: `_ingest_to_rag` 성공 **직후** `mark_items(kind, market, ticker, {item})` 호출, 루프 끝의 일괄 마킹은 제거.
- `mark_items`는 멱등 병합·200개 유계(`ingest_state.py:52-69`)라 아이템별 호출 안전(티커당 ≤4회 짧은 세션 — 무시 가능). 중간 실패 시 다음 델타는 **실패분+이후분만** 재작업.

### F. 관측성
- **datasets**: filing 루프에 접수번호별 `log_activity` 라인 — `[{ticker}] {accn} → {sections} sections, {chunks} chunks, {elapsed:.0f}s`. (현행 티커 요약 라인은 유지 — 다음 스톨이 "어느 공시"인지 즉시 특정 가능해짐.)
- **rag**: (a) C의 서브배치 타이밍 로그, (b) `/rag/ingest` 요청당 요약 INFO — docs·chunks·embedded·skipped·pruned·replace-scope·총 ms.
- 응답 확장: `{"chunks": n}` → `{"chunks": n, "pruned": p, "skipped": s}` — **추가 필드**라 기존 호출자(`news_ingest.py:74`, `adminpanel/main.py`의 RAG probe, `eval/run_eval.py`) 전부 `chunks`만 읽어 무해.

### G. 코퍼스 복구 — 신규 코드 **불필요** (자연 치유 + 증명)
- 코드로 확인: AAPL 실패는 `mark_items` 전에 예외가 전파돼 커서가 **미마킹** → 다음 델타 스윕이 refs(최근 4건)를 전부 재인제스트한다. B 이후에는 prune-swap이 잘린 두 접수번호를 원자적으로 완전판으로 교체하고, 리오더 덕에 텍스트 불변 청크는 임베딩 0회다.
- 즉시 치유가 필요하면 어드민 Pipelines에서 AAPL `filing_text` full 1회. 치유는 §4의 무결성 쿼리로 **증명**한다(가정 금지).

---

## §2. 구현 단계 — 7 페이즈 (독립 배포 가능, 안전 순)

> 각 페이즈는 별도 커밋. **배포 순서: rag(1→4) 먼저, datasets(5→6) 나중** (§1-A 표 참조).
> 공통 DoD: 해당 서비스 유닛 스위트 green + 신규 테스트 포함 + 이 스펙의 해당 섹션 상태 갱신.

### Phase 1 — rag: `meta->>'accession'` 표현식 인덱스
- **목표**: 접수번호 프룬/삭제가 86만+ 행 풀스캔을 멈추게 (현행 `delete_where`도 수혜).
- **파일**: `rag/rag/store.py` — `PgVectorStore` 부트스트랩 블록(기존 `rag_chunks_tsv`/`rag_chunks_trgm` 옆): `CREATE INDEX CONCURRENTLY IF NOT EXISTS rag_chunks_accession ON rag_chunks ((meta->>'accession'))` — 기존과 동일한 autocommit + 실패 시 경고-후-계속 패턴.
- **엣지**: 인덱스 생성 실패가 부팅을 막으면 안 됨(기존 warning 경로 미러).
- **테스트**: 자동화 없음(부트스트랩은 운영에서 검증). 수동: `\di rag_chunks*` + `EXPLAIN DELETE ... WHERE meta->>'accession'='X'`가 인덱스 스캔.
- **수용**: 인덱스 존재 + EXPLAIN 인덱스 스캔 확인.

### Phase 2 — rag: `replace_scope` + `ingest_docs` 리오더 (핵심)
- **목표**: 실패 모드 1(서버 측)·2 제거; 변경 없는 replace 재인제스트 = 임베딩 0.
- **파일/함수**:
  - `rag/rag/store.py`: `VectorStore` 프로토콜에 `replace_scope` 추가; `MemoryStore`(동기 본문)·`PgVectorStore`(단일 txn: advisory lock → `DELETE ... AND NOT (id = ANY(%s))` → executemany upsert → commit; pruned rowcount 반환) 구현; `delete_where`에서 조건 빌더 분리·공용화.
  - `rag/rag/ingest.py`: §1-B 순서로 재배열; `replace`면 `replace_scope(replace, all_ids, todo, vectors)`, 아니면 기존 `upsert`; 반환 `(embedded, pruned, skipped)`.
  - `rag/rag/main.py`: 응답 `{"chunks","pruned","skipped"}` + 요청당 요약 로그.
- **엣지**: 청크 0 + replace → 0 반환·무삭제(가드 유지) / `todo` 빈데 replace → 프룬은 실행 / keep_ids 수천 개(`ANY(array)`) / 필터 `None` → `IS NULL` / 한 요청에 같은 접수번호 문서 2개 → keep-set이 둘을 포괄.
- **테스트** (`rag/tests/`, MemoryStore + 가짜 임베더):
  1. 재청킹 축소: s.1~s.3 인제스트 후 s.1~s.2로 재인제스트 → s.3만 프룬, s.1/s.2 보존
  2. 원자성: 임베더가 중간에 raise → 스토어 무변화(구 청크 검색 가능·카운트 동일)
  3. 변경 없는 재인제스트 **with replace** → embedded=0, 코퍼스 동일 (리오더 전엔 불가능하던 동작)
  4. 동일 id 텍스트 변경 → 재임베딩되고 프룬 안 됨
  5. 빈 추출 가드: `ingest_docs([], replace=...)`·빈 텍스트 문서 → 아무것도 안 지움
  6. 비-replace 경로 무변경(기존 테스트 전부 green)
- **수용**: rag 스위트 green + "구식 배치 클라이언트 시퀀스(배치0 replace + 배치1 무replace)가 완전한 코퍼스로 수렴" 시뮬레이션 테스트.

### Phase 3 — rag: replace tenant 고정 (하드닝)
- **목표**: 글로벌 재인제스트가 테넌트 행을, 테넌트가 글로벌 행을 절대 프룬 못 하게.
- **파일**: `rag/rag/main.py` — `replace = {**body.replace, "tenant": tenant}` 무조건 적용(tenant `None` 포함).
- **엣지**: 프리-테넌트 행(meta에 tenant 키 없음) → `IS NULL` 매치(글로벌로 취급 — 정확함).
- **테스트**: 글로벌 인제스트가 접수번호 X의 `tenant=None` 행만 프룬하고 `tenant="p1"` 행은 생존; 역방향은 헤더 스탬프 `TestClient`로.
- **수용**: 신규 2종 green + 기존 테넌트 격리 테스트 무영향.

### Phase 4 — rag: 임베딩 동시성
- **목표**: 멀티 배치 임베딩 wall-time ÷ ~동시성.
- **파일**: `rag/rag/embeddings.py` — `_embed` 세마포어+gather(순서 보존 flatten, 서브배치 타이밍 로그); `rag/rag/config.py` — `embed_concurrency: int = 4`, `embed_batch: int = 64`(모듈 상수 `_BATCH` 삭제).
- **엣지**: 단일 배치 입력(gather 1개 무해) / 서브배치 1개 raise → `embed` 전체 raise(스왑 전이라 정확한 동작) / `self.dim`은 flatten 후 / `_report_usage`는 1회.
- **테스트**: 지연 주입 가짜 `embed_content` + 호출 순서 레코더 → 동시성 하 위치 정합; 429-후-성공 → 재시도 동작; `embed_concurrency=1` → 순차 호출 순서 재현.
- **수용**: 테스트 green; 운영 로그에 서브배치 타이밍 중첩; 대형 공시 인제스트 wall-time ~3-4× 단축.

### Phase 5 — datasets: replace 단일 POST + 스케일 타임아웃 + 1회 재시도
- **목표**: 실패 모드 1(클라 측)·4 제거.
- **파일/함수**: `datasets/app/store/news_ingest.py::_ingest_to_rag` — `replace`면 전체 docs 단일 POST + `httpx.Timeout(connect=10, read=min(base+per_doc×N, max), write=60, pool=10)` + TransportError/5xx 1회 재시도(~15s 후); 아니면 기존 40-doc 루프 유지. `datasets/app/config.py` — 타임아웃 설정 3종(120 / 6.0 / 1200). `.env.example`에 문서화.
- **와이어**: 요청 형태 무변경(`documents`+`replace`) — 4개 replace 호출처(`filing_ingest.py:169` 등)는 공용 헬퍼라 **호출부 수정 0**.
- **엣지**: 재시도 시 chunks 이중 카운트 금지(성공 응답에서만 합산) / 4xx 즉시 raise / 빈 docs → POST 없이 0(기존 가드).
- **테스트** (`datasets/tests/`, `test_delta_ingest.py`식 모킹): replace 호출이 정확히 1 POST(전 docs+replace 포함) / 비-replace 100 docs → 40/40/20 3 POST / N-doc read timeout 공식·상한 / 첫 시도 503 → 1회 재시도 → 성공·chunks 1회 카운트 / 2연속 실패 → raise.
- **수용**: datasets 스위트 green; **Phase 2~4 배포 후에만 배포**.

### Phase 6 — datasets: 아이템 단위 커서 + 접수번호별 관측성
- **목표**: 실패 모드 5 제거; 다음 스톨을 특정 공시로 즉시 귀속.
- **파일**: `filing_ingest.py`·`kr_earnings_ingest.py`·`transcript_ingest.py`·`deck_ingest.py` — 각 아이템 성공 직후 `mark_items(..., {item})`, 일괄 마킹 제거; filing 루프에 접수번호별 `log_activity` 라인(§F 포맷).
- **엣지**: `mark_items`는 자체 예외를 삼키는 best-effort — 북키핑 실패가 런을 가라앉히지 않음 / 델타 스킵셋 소비는 기존대로 선두 1회.
- **테스트**: `test_delta_ingest.py` 확장 — A1 성공·A2 실패 → `done_items`에 A1만(현행은 둘 다 없음); 다음 델타는 A2만 터치.
- **수용**: 테스트 green; 나쁜 접수번호 1개가 다음 스윕에서 그 1개만 재지출.

### Phase 7 — 라이브 검증 + 스펙 마감 (코드 없음)
- §4 프로토콜 전체 실행, 전후 실측치(임베딩 wall-time, AAPL 청크 수)를 이 문서에 기입, ROADMAP ING-1 ✅ 전환.

---

## §3. 와이어/설정 변경 표

| 표면 | 변경 | 하위 호환 |
|---|---|---|
| `POST /rag/ingest` 요청 | 형태 무변경(`documents`+`replace`); 요청당 docs 수만 증가 | 구 배치 클라이언트도 수렴(§1-A 표) |
| `POST /rag/ingest` 응답 | `"pruned"`, `"skipped"` 필드 추가 | additive — 기존 호출자는 `"chunks"`만 읽음 |
| `replace` 의미론 | delete-all-in-scope → **prune-stale**(keep-set=요청 id) + tenant 항상 고정(None 포함) | full-scope 요청의 최종 상태 동일; 크로스-테넌트 삭제는 의도적으로 좁아짐 |
| rag 설정 | `+RAG_EMBED_CONCURRENCY=4` · `+RAG_EMBED_BATCH=64` | `=1`/`=64`가 현행 재현(무배포 롤백) |
| datasets 설정 | `+RAG_INGEST_TIMEOUT_BASE_SECONDS=120` · `+RAG_INGEST_TIMEOUT_PER_DOC_SECONDS=6.0` · `+RAG_INGEST_TIMEOUT_MAX_SECONDS=1200` | 기본값만으로 동작 |

전 신규 설정은 `.env.example`에 주석과 함께 문서화한다.

---

## §4. 검증 프로토콜 (Phase 7)

1. **유닛**: rag 스위트(`rag/tests/` — MemoryStore 패리티가 핵심) + datasets 스위트(`test_delta_ingest.py`·`test_datasets.py`·`test_transcripts.py`·`test_decks.py`) — 전부 일회성 컨테이너로(라이브 스택 불가침).
2. **AAPL 치유(full)**: 어드민 Pipelines → `filing_text` US AAPL full 실행. 접수번호별 activity 라인 확인; 잡 노트 `1/1 tickers indexed`, `FAILED` 없음.
3. **델타 스킵 증명**: 곧바로 delta 재실행 → `[AAPL] 변경 없음 (델타 스킵)` + rag 로그에 해당 접수번호 임베딩 호출 0.
4. **코퍼스 무결성 SQL** (rag Postgres) — 섹션 연속(`sections == max_section`)이고 청크 수가 잘린 137/97을 초과해야 함:

```sql
SELECT meta->>'accession'                                      AS accession,
       count(*)                                                AS chunks,
       count(DISTINCT (regexp_match(id, ':s\.(\d+)'))[1]::int) AS sections,
       max((regexp_match(id, ':s\.(\d+)'))[1]::int)            AS max_section
FROM rag_chunks
WHERE meta->>'accession' IN ('0000320193-26-000013', '0000320193-26-000006')
GROUP BY 1;
```
(글로벌 filing 청크 id 스킴: `{accession}:s.{i}::{j}` — `models.py:68-80` + `filing_ingest.py:130`.)

5. **검색 스팟체크**: 10-K 후미 주제(예: Item 8 주석)로 `/rag/search`(`ticker=AAPL, doc_type=filing`) → 기존에 빠져 있던 꼬리 섹션에서 히트.
6. **처리량**: 대형 접수번호 1건의 rag 요청 요약 로그(총 ms·서브배치 타이밍) 전후 비교 — Phase 4 효과 정량화.

---

## §5. 리스크 · 롤백

| 리스크 | 완화 / 롤백 |
|---|---|
| 동시성으로 Gemini 429 증가 | `_with_retry` 2/4/8s가 흡수; `RAG_EMBED_CONCURRENCY=1`로 무배포 롤백 |
| 클라 재시도가 진행 중 시도와 경쟁 → 임베딩 이중 지출 | 스코프 1개분으로 유계; advisory lock이 스토어 일관성 보장; 리오더로 커밋-후-재시도는 무비용. **수용** |
| 트랜잭션 비대(스코프 행 + 벡터 메모리) | 접수번호 1개 분량(≤수천 행, 벡터 ~20MB 최악) · lock 직렬화 · 단명. **수용** |
| 신구 버전 스큐 | 양방향 모두 현행 의미론으로 강등(§1-A 표); 어쨌든 rag 먼저 배포 |
| tenant 고정 동작 변경 | 좁히기만; 정당한 크로스-테넌트 replace가 존재했다면 stale 행 잔존으로 **시끄럽게** 드러남(§4 쿼리로 가시); 리버트는 한 줄 |
| 페이즈 리버트 | 각 페이즈 독립 커밋 — 단독 리버트 가능. Phase 2 리버트 시 prune-swap이 쓴 데이터는 구 코드와 완전 호환; Phase 1 인덱스는 무해하게 잔존 |

---

## 부록 — 재사용할 기존 자산

- **배칭 루프 전례**: `news_ingest.py:67`(40-doc), `embeddings.py:84`(64-text)
- **재시도 전례**: `embeddings.py:29-52`(`_with_retry` — 재시도 대상 상태코드·지수 백오프), `datasets/app/http.py:18-40`(백오프 + 프로바이더별 서킷 브레이커)
- **멱등 기반**: 안정 청크 id(`rag/rag/models.py:68-80`) · `existing_texts` 변경-없음 스킵(`ingest.py:28-29`) · `ON CONFLICT (id) DO UPDATE`(`store.py:210-211`)
- **진행 로그**: `log_activity`/`update_progress`(`datasets/app/store/jobs.py`) — 어드민 `/runs/{id}` 상세가 그대로 표시
