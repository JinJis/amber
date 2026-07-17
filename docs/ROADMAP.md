# ROADMAP — Investment-Agent Data Platform (v2, 2026-07-03)

> **This is the source of truth.** It replaces `docs/deprecate/ROADMAP.md` (history only).
> One task per PR; tag the task id in branch/commits/PR. A task is done only when its
> acceptance criteria **and** the Definition of Done (CLAUDE.md §5/§7) pass — unit tests
> added, coverage/e2e green, eval run, and this file's status + test totals updated in the
> same PR.
>
> Deep implementation detail for the M0–M2 killer feature lives in
> [`docs/HISTORY_LAB_SPEC.md`](./HISTORY_LAB_SPEC.md). The publish layer (공유 카드 · 팩트체크 ·
> 노트 — M-SHARE/M-FACT/M-NOTE) lives in [`docs/PUBLISH_SPEC.md`](./deprecate/PUBLISH_SPEC.md). The UX
> overhaul + all screen specs live in [`docs/UX_SPEC.md`](./UX_SPEC.md). RAG/검색/UX 품질 잔여
> 과제(RQ-9 재인제스트, UXQ-5..9)는 [`docs/QUALITY_SPEC.md`](./QUALITY_SPEC.md), 인제스트
> 원자성·배치 개편(ING-1)은 [`docs/RAG_INGEST_BATCHING_SPEC.md`](./deprecate/RAG_INGEST_BATCHING_SPEC.md).
> Read the relevant spec section before building.

---

## 0. Product thesis and the killer feature

The product is a **personal research desk** built on three pillars: trust by construction
(every figure sourced, point-in-time, visually verifiable), pull→push (the desk wakes up for
you), and an ecosystem (clone others' analysts). What shipped so far makes the desk *honest*.
What ships next makes it *irreplaceable*:

> **History Lab (히스토리 랩)** — "지금 이 낙폭, 역사에서는 어디쯤인가?"
> The user sees today's chart **overlaid on history**: how deep is the current drawdown
> versus the dot-com bust or the GFC; how did volatility at this level behave in every past
> episode; what were the headlines on the equivalent day of each past crisis; and what the
> **historical base rates** were — "S&P가 하루 −5% 이상 하락한 과거 87번의 사례에서, 20일 뒤
> 중앙값 수익률은 +2.1%, 상승 마감 비율은 63%였다 (과거 기록 · 전망 아님)."

Why this wins:

1. **Nobody serves it point-in-time.** Terminals show history as a static chart; we replay
   history *as it was known then* — the news feed, the macro snapshot, the disclosure of that
   day — because our whole data plane is already point-in-time and cited.
2. **It is guardrail-native.** We never forecast. Historical conditional frequencies are
   *descriptive statistics of the record*, computed transparently (n, dates, method shown via
   the existing 계산 근거 panel) and always labeled **"과거 기록 · 전망 아님"**. The label *is*
   the brand. See §2 invariants below.
3. **It composes with everything shipped**: the chart engine renders the overlays, the RAG +
   evidence viewers show era news/filings, and the agent orchestrates all of it **in chat** —
   the product is chat-first (see the feature-flag decision below).

Supporting killer features (analyst daily-driver work): the turn-zero **Proactive Desk**
(M-DESK — the desk tells you what's worth asking today), **Earnings Command Center** (M3),
**Filing Intelligence / risk-factor diff** (M4).

> **Chat-first decision (2026-07-03).** The dashboard(보드) and alert bot(알림봇) surfaces are
> **feature-flagged off by default** (FLAG-1) and receive **no further roadmap work** until
> re-approved. Everything in this roadmap ships as chat artifacts + the History Lab view.
> The existing board/alert code stays in the tree behind the flags — do not delete, do not
> extend.

---

## 1. Where we are (2026-07-03)

Shipped and green (see `docs/deprecate/ROADMAP.md` for the full ledger):

- **Platform**: 6 services + worker, gateway-enforced entitlement/metering, Procrastinate
  queue, admin console, 541 unit tests (datasets 262 · agent-engine 143 · studio-api 73 · web 63), e2e/coverage/eval harnesses.
- **Provenance**: SEC iXBRL + DART HTML evidence viewers with highlight, 8-K deck PDF viewer
  (pdf.js + Document AI), 계산 근거 panel, freshness/cadence on every artifact.
- **Answer quality**: Gemini intake (guardrail+plan), clarify/decompose, parallel sub-agents,
  thinking stream, citations dedup, follow-up suggestions.
- **Charts**: TradingView Lightweight Charts — candles/line/volume, sourced markers
  (earnings/dividend/split/filing), Gemini annotations, technical overlays, user drawings,
  PNG export, range 1M–MAX.
- **Data**: SEC/OpenDART/Yahoo/FRED·DBnomics/ECOS/Google News/API Ninjas transcripts/
  8-K decks/KR 잠정실적 + FMP (estimates/calendar) + KIS (KR realtime) Wave 2.
- **Product**: chat, watchlists/@groups, agent builder, prompt library, onboarding,
  background-run resume. Also built but now **feature-flagged off by default** (FLAG-1):
  multi-board dashboard (react-grid-layout, pin anything, per-widget alerts) and the alert
  bot with telegram/slack/kakao/email channels — kept in the tree, not extended.

Gaps that block the killer feature (verified in code, 2026-07-03):

| Gap | Today | Needed |
|---|---|---|
| Price history depth | 3y rolling (`PRICES_BACKFILL_YEARS`) | max-history for a curated history universe (indices/VIX/FX/rates) + watchlist tickers |
| Volatility context | realized vol as a technical overlay only | VIX series, vol percentiles vs own history, regime-conditional distributions |
| Regime labels | none | derived drawdown episodes + curated named regimes, stored & sourced |
| Base-rate statistics | none | descriptive conditional-frequency engine with provenance |
| Analogue search | none | similarity search over historical return windows |
| News archive | rolling ~8 headlines/ticker, no history | era news (NYT Archive, GDELT 2017+; KR provider TBD) |
| Transcript archive | latest 4 quarters (US) | multi-year backfill within provider limits |

---

## 2. Invariants added by this roadmap (extend CLAUDE.md §2 — never violate)

1. **Base rates are history, not forecasts.** Every History Lab statistic is a descriptive
   aggregate over an explicit, enumerable set of past events. The API response **must**
   include: the event definition, `n`, the event dates, the computation method, and
   `label: "과거 기록 · 전망 아님"`. The UI **must** render the label on every base-rate /
   analogue artifact (the `HistoricalLabel` component, UX_SPEC §6.4). The agent may report
   these statistics **only** in past-tense descriptive phrasing ("~였다/했다"), never as a
   probability claim about the future ("~할 확률이다", "반등할 것"). The intake guardrail is
   taught this boundary (LLM-judged, no regex — see HL-6) and eval scenarios enforce both
   sides.
2. **Point-in-time replay shows only what was knowable.** When the History Lab scrubber sits
   on date D, every panel (news, macro, disclosures) shows data with `as_of ≤ D`. No
   look-ahead, ever.
3. **Regimes are derived, then named.** Episode boundaries (peak/trough/recovery dates and
   depths) are computed from ingested price data by the documented algorithm
   (HISTORY_LAB_SPEC §4.2) — reproducible, with the computation shown. Curated metadata
   (name, aliases, description) is reference *data* with its own sources, not logic.

---

## 3. Milestone overview

| Milestone | Name | Outcome | Depends on | Status |
|---|---|---|---|---|
| **FLAG-1** | Chat-first feature flags | 대시보드 + 알림봇 hidden behind env flags, default off | — | ✅ done |
| **OPS-1** | Admin ingestion error detail | per-ticker real error messages, grouped summaries, retry-failed-only | — | ✅ done |
| **M0** | Deep History Data Plane | max-history prices + VIX + regime/episode store + analytics engine + `/history/*` API through the gateway | — | 🚧 HL-1..4 ✅ · HL-5(era news) ⬜ |
| **M1** | History Lab in Chat | agent answers "지금 낙폭 닷컴버블이랑 비교해줘" with analogue + base-rate artifacts, guardrail framing, new chart panes | M0 | 🚧 HL-6/7/9 ✅ · HL-8(chart panes) ⬜ |
| **QT-2*** | Number audit (pulled forward) | every numeral in prose/cards matches a tool value — the trust floor for anything that leaves the app | — | ✅ done (audit module + done-event ride-along + desk-feed card drop; share gating lands with SH-2) |
| **M-SHARE** | 공유 파이프라인 (PUBLISH_SPEC §3) | share tap → provenance-baked card image (aspect presets) + public read-only page (`/s/{token}`, OG) + evidence quote cards + 데스크 브리핑 카드 | QT-2 | ✅ done — SH-1/2/2b/3/4/5 + IMP-13 · **SH-ANSWER**(답변 전체 공유: 모든 답변에 🔗 공유, kind=answer 스냅샷·유저정보 0, 공개 페이지가 본문+인라인 그림+인용 SourceCard 렌더) · **MOBILE-1**(전면 모바일: 드로어 레일·근거 바텀시트·공개 페이지 모바일 퍼스트) |
| **M-FACT** | 근거 기반 팩트체크 (PUBLISH_SPEC §4) | paste a claim → cited verdict artifact (사실/사실과 다름/미래 주장) with findings for/against; the receipt for 정보방 | M-SHARE | ❌ **제거됨 (2026-07-05, ASK-1)** — 사용 흐름에서 겉돌아 유지비만 남음; 신뢰는 수치 원장으로 일원화 |
| **M-ASK** | "물어보기" 표면 개편 ([ASK_SURFACE_SPEC](./deprecate/ASK_SURFACE_SPEC.md) — deprecated, ENTRY-v6로 대체) | 팩트체크 제거 · 근거 담기 "+노트북" 단일화 · 근거 패널 챗 전용 · 네비 "물어보기" 통합 · 첫 화면 = 사전 생성 질문거리(per-ticker)+Hot Trend(5분 파이프라인, 접속 시 LLM 0회) · 온보딩 v2(관심종목 필수 3+) | LG, M-NB, M-DESK | ✅ ASK-1..6 + ASK-5b/5c + **ASK-7(2026-07-06 UX 개편)**: ① 근거 패널 v3 — 수집 중(과정 타임라인+도착순 출처) → 완료(판정→차트·표→인용한 출처[n] 번호순→수치 원장→참고만 한 출처(접힘)→과정(접힘)) 2단계 플로우, 섹션마다 한 줄 설명, [n] 배지 전 카드 통일 ② 엔트리 v4 — 티커 테이프 삭제, 관심종목은 **탭한 종목만** 온디맨드 3카드(`GET /ask-feed/ticker`, 30분 캐시), Hot Trend → **지금 뉴스에서**(news_feed 스코프, 10분 백그라운드 갱신, 전 티커 스윕 제거) ③ 근거/핀 액션 '대시보드'→'노트북' 용어 정리 + **ASK-8(아티클 답변)**: 합성 프롬프트를 리서치 노트(전문 블로그) 형식으로 — 두괄식 리드·## 섹션·마무리 체크포인트·자료 풍부 시 긴 글; 이번 턴의 차트·표를 {{figure:N}} 마커로 본문 문단 사이에 배치(미배치 시 글 끝 자동 폴백), 웹은 마커 자리에 실제 ArtifactCard를 center-align 그림으로 렌더(스트리밍 부분 마커 숨김·미지 마커 드랍), QT-2는 마커 숫자를 블랭킹, 아티팩트를 메시지에 영속화(재열람에도 인라인 그림 유지) + **LG-4(본문 수치 하이라이트)**: 수치 원장을 패널에서 본문 속으로 — 검증 수치는 노란 하이라이트, hover 팝업(원자료 대조·출처 [n]·as_of·🧮파생·📌노트북 담기), 클릭→원문; 미확인 수치는 앰버 경고; audit도 메시지에 영속화 + **프롬프트 컨셉 전환**: '자료에 없음' 나열 금지 — 정성 서사는 LLM 지식으로 풍부하게, 수치만 검증 인용(우리 차별점) + **ASK-9(질문거리 다양화)**: 티커 온디맨드가 9개 소스 와이드 수집(밸류·내부자/수급·컨센서스·어닝·변동성 추가) → 소스별 후보 2~3개 생성 → kind 다양성·의외성 큐레이션으로 3~5개 + **ASK-10(2026-07-06)**: ① 카드 question(표시·해요체)/query(실행·명령조) 이원화 — 컴포저에는 종목 주체가 주입된 query가 들어감(엉뚱한 종목 답변 방지) ② Hot Trend → **🌍 Macro Trends**: 뉴스+FRED 거시패널(US/KR) 가미, 5분 주기, 접속 시 read-through 킥(콜드스타트 해결), 어드민 Pipelines에 상태 카드+'지금 갱신 ▶'(POST /ask-feed/refresh) ③ **온보딩 v3**: 6스텝 — 정체성(진짜 데이터·교차검증)→근거 프리뷰(실 SourceCard·TrustStrip·하이라이트)→분석거리 프리뷰(QCard)→꼬리물기 프리뷰→관심종목 필수 3+→착륙 ④ **프롬프트 라이브러리 삭제**(버튼·모달·API·모델·시딩) ⑤ 한국어 토스톤 패치(해요체 통일·번역투/전문용어 제거) · 541 유닛 green + **ENTRY-v6(2026-07-17 탐구 홈 5섹션)**: ① 관심 @그룹을 아코디언 풀폭 행 → **칩 나열**(가로 wrap, 누르면 아래 패널에서 종목 토글); 끝에 관심 페이지의 ＋새 그룹 그대로(만들면 관심 페이지로 이동해 종목 담기) ② 마키 섹션 3개 추가 → 총 5섹션(관심종목 · Macro Trends · **어닝 레이더** · **투자거장·수급** · **히스토리 랩**), 모두 우→좌 마키; 각 섹션은 agent-engine의 새 스코프(earnings_radar/guru_flows/history_lab)로 시장 전체 공유 캐시 생성(전 티커 스윕 아님, signature-gated) · studio `refresh_sections_once`(1h 백그라운드+read-through 킥, `POST /ask-feed/refresh-sections` ops/eval 훅) · `_assemble`가 `sections[]`로 내려주고 프런트가 scope→제목·이모지·카피 매핑 · 히스토리 랩은 '과거 기록·전망 아님' 브랜드 유지 + **QG(턴제로 품질 갭 메우기, 2026-07-17)**: 감사에서 나온 갭 처리 — ① ask-feed `_signature` 400자 절단 버그 수정(전체 페이로드 해시 — 긴 리스트 깊숙한 새 레코드도 감지, 회귀 테스트) ② ONB-LIVE studio 계층 테스트 0 → 저장/빈응답 유지/read-through 킥 ③ desk-feed·티커 stale-서빙 경로 테스트(prior 캐시 있으면 stale:true/이전 풀 서빙) ④ eval `kind: ask_feed` 러너 + Macro Trends(무전망 judge)·어닝(무출처) 시나리오 ⑤ admin 홈 마키 섹션 ops 카드 커버리지 ⑥ 문서 드리프트(eval/README 96·RUBRIC verdict-fit 주석) |
| **EV-FIX** | 근거 프리뷰 신뢰성 (2026-07-11) | 인앱 근거 뷰어 전 케이스 복구: worker 볼륨(인제스트 시점 공시 HTML 캐시 공유) · OpenDART 쿼터 로테이션(`OPENDART_API_KEYS`)+읽기 API 캐시 · /financials 래퍼 언랩(재무제표 표·concept 앵커·차트) · filings `filing_type` 파라미터 · 뉴스 구글 인터스티셜 → 발행사 기사 디코드(gnews_resolve) · toCitation computation 보존 + DerivationCard 우선 · 인용 dedup 병합·실패콜 스킵 · RAG 검색 상시응답(렉시컬 AND-우선/상한 랭킹·임베드/리랭크 예산·게이트웨이 90s·클라이언트 1회 재시도, 56s→3~12s) | — | ✅ done (유닛 6서비스 green + 라이브 재현 검증; KR 원문 렌더는 쿼터 리셋 후 확인) |
| **OPS-2** | 델타 인제스트 + 쿼터 운영 (2026-07-12) | ① 파이프라인 `mode=delta\|full` — 델타는 IngestState 커서로 이미 색인한 공시·분기·덱을 스킵, 재무는 신선 종목 스킵; 크론 스윕 기본 델타; 어드민 백필 폼에 수집 방식 라디오 ② OpenDART 키별 일일 사용량 계측(UpstreamUsage, KST 창) + `/admin/quota` + 어드민 쿼터 카드(남은 한도·소진 배지·14일 추이) ③ 어드민 개편 — `/runs` 수집 이력(필터·페이저) + `/runs/{id}` verbose 상세(원인별 실패·활동 로그 전체·live), 활동 피드 level 필터·캡 5000, /pipelines 도구 섹션 정리 | EV-FIX | ✅ done (라이브 검증: 쿼터 패널·이력·델타 2회차 스킵) |
| **ING-1** | RAG 인제스트 원자성·배치 개편 ([RAG_INGEST_BATCHING_SPEC](./deprecate/RAG_INGEST_BATCHING_SPEC.md)) | 대형 공시 인제스트를 "빠르고·안 깨지고·재시도 무비용"으로: replace-스코프당 단일 POST · rag 원자적 prune-swap(`replace_scope`, advisory lock, 임베딩 후 단일 txn — 잘린 공시 서빙/접수번호 소실 창 제거) · replace tenant 고정 · 임베딩 동시성(RAG_EMBED_CONCURRENCY, 실측 109s/콜 → ~2s) · 문서수 비례 클라 타임아웃+연결실패만 1회 재시도 · 아이템 단위 델타 커서 · accession 인덱스 · postgres shared_buffers 튜닝(HNSW 삽입 병목) | OPS-2 | ✅ done (유닛 rag 37·datasets 340; 라이브 AAPL 치유·무결성·동시임베딩 검증) |
| **M-SCALE** | 프로덕션 스케일링 ([SCALING_AUDIT](./SCALING_AUDIT.md), 2026-07-12 감사) | 코드 정독 4축 감사에서 확인된 CRITICAL 11·HIGH 13을 4단계로 해소: **SC-0** 보안·유실(전 서비스 시크릿 assert·admin 잠금·IDOR·백업·로그 캡) → **SC-1** 단일 노드 처리량(플래너 오버라이드 레이스 → 인자화, Gemini 세마포어+429 백오프, 게이트웨이 meter/audit 배치, rag 커넥션 풀+trgm 제거+ef_search, Yahoo TTL 캐시+토큰버킷, 공유 httpx+카탈로그 캐시, 쿼터 원자화) → **SC-2** 멀티 레플리카(런 상태 외부화+쿼터 환불, 빌링 락 범위 확장+인보이스 상태 가드, 스케줄러 advisory lock, Redis 배선: 레이트리미터·OpenDART 블록·KIS 토큰·브레이커·뉴스 dedup) → **SC-3** 증식·관측(usage/audit 파티션+보존, evidence GC, request-id 전파+메트릭, AlloyDB 분리) | M-PROD | 🔄 SC-0 done · **SC-1 done(12/12 — CRITICAL CR-4·5·6·7·8·9 + HIGH HI-1·4·5·6·7·8·9 + ME-11·12; 단일 노드 처리량)** · **SC-2 done(6/6): SC-2.1 빌링 락 범위+인보이스 상태 가드(CR-2·ME-15) · SC-2.2 스케줄러/피드 advisory lock+ON CONFLICT+단일비행(CR-3·ME-1) · SC-2.3 런 상태 durable 레지스트리+죽은 런 쿼터 환불(CR-1 — RunRecord+INSTANCE_ID 소유권+하트비트+advisory-locked 리퍼: 죽은 레플리카의 고아 런을 reaped 처리하고 소비된 TurnUsage 환불, 오래된 레코드 GC) · SC-2.4 Redis 옵션 배선(REDIS_URL opt-in, 기본 OFF=인프로세스; 게이트웨이 레이트리미터·KIS OAuth 토큰·OpenDART 쿼터 키 블록·서킷 브레이커·뉴스 dedup 공유, HI-2·3) · SC-2.5 부팅 마이그레이션/시드 락(ME-3) · SC-2.6 reconcile 버전 컬럼+게스트 GC/JOIN 카운트/첫채팅 쿠키+전용 시스템 피드 키(ME-2·4·5)** — **SC-2 완료: 이제 studio/gateway 다중 레플리카에서 쿼터 유실·영구 running 런 없음(REDIS_URL 설정 시 레이트/토큰/블록/브레이커/뉴스도 공유). 단, 라이브 SSE tail의 크로스 레플리카 스트리밍은 미구현 — 진행 중 런의 tail은 소유 레플리카로 sticky 라우팅 필요(Redis pub/sub tail이 후속).** · **SC-3 진행: SC-3.1 페이지네이션(GET /conversations·/messages LIMIT+커서, 최신 슬라이스+has_more/load-earlier, HI-10) · SC-3.2 usage/audit (project_id,ts)+ts 인덱스 + 일일 보존(usage_events→per-project 누적 롤업 후 드롭·audit 드롭, advisory-locked)(HI-13) done** · **SC-3.3 request-id 인바운드 honor(6서비스 로깅 미들웨어)+웹 엣지 발급/전달 + 서비스별 Prometheus /metrics exporter(6서비스, http_requests_total{method,status}·http_request_duration_seconds{method}, prometheus-client 옵셔널·가드) done(HI-11)** — 게이트웨이는 이미 X-Request-ID를 데이터플레인으로 통과시킴; 잔여(부분): studio 디태치드 런→agent-engine·agent 툴루프의 request-id 명시적 전파(Starlette BaseHTTPMiddleware가 엔드포인트를 별 태스크로 실행→contextvar 비신뢰) · **SC-3.4 evidence-docs 디스크 GC(atime-LRU+max-age, 워커 일일 크론; 버전키 오펀 회수, ME-10) + 뉴스 코퍼스 age-out(ME-16 — RAG /rag/prune 스코프+날짜 삭제·(doc_type,as_of) 인덱스, 워커 일일 크론 gc_news가 news_retention_days 초과 뉴스 청크 제거; 스코프 없는 삭제는 거부) done** · **ME-6 부분: 공개 공유 읽기 write-free 화(추천 코드를 create_share에서 1회 발급, 고트래픽 GET은 read-only 조회 → read replica 가능) done** — 잔여 ME-6 스토리지 분리(inline 아티팩트 JSON→사이드테이블 · og_image base64 DB행→오브젝트 스토리지 · viral 공유 view 카운터 핫로우→카운터 테이블/샘플링; 오브젝트 스토리지는 인프라 결정) · 인프라 결정(AlloyDB 분리·PITR·전용 pg·인제스트 샤딩, CR-8/11·HI-12·ME-13) |
| **INFRA-1** | GCP 배포 자산 ([INFRA](./INFRA.md), 2026-07-17) | 단일 GCE VM(asia-northeast3, e2-highmem-4 32GB) + Cloudflare 무료 프록시 → Caddy(Origin CA·SSE flush) 아키텍처 확정 · `docker-compose.prod.yml`(caddy·전 API 포트 루프백 강제·mem_limit 예산·worker 헬스체크 — ME-13 일부 해소) · `deploy/`(provision.sh: VM·고정IP·IAP-only SSH 방화벽·GCS 백업 버킷 30d 라이프사이클·스냅샷 스케줄 7d / vm-setup.sh / backup.sh+systemd 타이머 — **CR-11 백업 해소**) · web Dockerfile `NEXT_PUBLIC_TOSS_CLIENT_KEY` ARG(프로드 빌드 키 누락 버그 수정) · 비용 모델: 베타 ~$213–245/mo(온디맨드)→$150–183(1-yr CUD)+Gemini 토큰 별도 · 관리형 DB(Cloud SQL PITR·AlloyDB)는 트리거 기반 성장 경로 · **스타터 티어**(e2-standard-4 16GB, `VG_MEM_*` 프로필, −$44/mo — 첫 유저용, `set-machine-type`으로 3–5분 다운타임 1회 승격) · **무중단 롤링 배포 `deploy/deploy.sh`**(빌드 먼저→서비스별 `--wait` 헬스 게이트→studio/agent `stop_grace_period 45s` SSE 드레인→Caddy `lb_try_duration` 웹 스왑 갭 흡수; pg는 재기동 제외) | M-SCALE | ✅ 자산 커밋 — 프로비저닝 실행은 오너(USER_TODO §1-12) |
| **M-VIRAL** | SNS 바이럴 파이프라인 ([VIRAL_SPEC](./VIRAL_SPEC.md)) | 차트 박힌 서버사이드 OG · 네이티브 공유 · 캐시 · 조회 측정 · 전환 루프 · 카카오 리치 · 훅 산출 · 어닝 서프라이즈 카드(API Ninjas) · 온디맨드 공시 인제스트 | M-SHARE | 🔄 V-1..5·7..9 ✅ done — **API Ninjas 프리미엄**로 어닝콜 US+KR(d9a4353)·로고·서프라이즈 해금; **V-6 카카오 리치 공유만 잔여**(유저 Kakao JS 키 대기, VIRAL_SPEC) |
| **M-NB** | 리서치 노트북 | — | — | ❌ **제거됨 (2026-07-10)** — 사용 흐름에서 겉돌아 유지비만 남음; 담기/노트/kind=note 공유 전부 삭제 (NOTEBOOK_SPEC §A는 히스토리) |
| **M-SA** | 스탠딩 알림 ([NOTEBOOK_SPEC](./NOTEBOOK_SPEC.md) §B) | 답변 근거의 cadence 기반 "🔔 이 질문 계속 지켜보기" 칩 → 서명 비교 → 데스크 `standing_update` 카드 (푸시 채널 없음, 챗-퍼스트) | M-DESK | ✅ SA-1..4 (라이브 검증) |
| **M-NOTE** | 인사이트 노트 (PUBLISH_SPEC §6) | conversation → structured, cited note → A4 report-grade image/PDF + share | M-SHARE, M2 | ➡ **M-NB로 흡수** (노트북 공유 = NT-3/4) |
| **DATA-KR-1** | KR macro expansion | ECOS beyond rates (CPI·실업률·성장·PPI·환율) — KR fact-check needs official KR macro | — | ✅ done (ECOS 5종, macro panel/indicators KR, 라이브 검증) |
| **M2** | History Lab Surface | dedicated 히스토리 랩 view: century ribbon, THEN\|NOW split, day scrubber, era news + point-in-time macro | M1 | ⬜ planned |
| **M-DESK** | Proactive Desk (턴 제로) | the empty chat becomes a live, sourced briefing: what to ask today — news/filings/calendar/price-move suggestion cards, watchlist nudge & pulse | basic: FLAG-1 · history hooks: M0 | ✅ basic done (DK-1..4) · DK-3b after M0 |
| **M-QUANT** | Analysis→Artifact engine | declarative, deterministic multi-series computation (`/compute/series`) + numeric-integrity verify + scatter/distribution artifacts — the general "여러 데이터 → 통계 분석 → 차트/표" pipeline | — (M0 shares the analytics module) | ⬜ planned |
| **M3** | Earnings Command Center | earnings season in chat: calendar/surprise artifacts, transcript archive + QoQ tone diff | — (parallel to M2) | ⬜ planned |
| **M4** | Filing Intelligence | 10-K risk-factor YoY redline, 8-K/주요사항 event timeline | — | ⬜ planned |
| **M5** | UX Overhaul (chat-first) | new IA (탐색·히스토리·관심·설정), command palette, ticker context header, a11y + FE refactor completion | M2 | ⬜ planned |
| **M6** | Horizon (needs re-approval) | standing analysts + briefs; publish/clone gallery; dashboard & alert-bot revival (history-aware triggers, chart-image notifications) | M3 | ⬜ not started |

Status legend: ⬜ planned · 🚧 in progress · ✅ done. **Update a task's marker in the same PR
that completes it.**

**Build order v3 (2026-07-04 — publish layer added; see PUBLISH_SPEC §8 for rationale):**

```
[done] FLAG-1 · OPS-1 · M-DESK basic · M0(HL-1..4) · M1(HL-6/7/9)
[done] QT-2 · M-SHARE(SH-1..3) · M-DERIV(DRV-1..5) · M-FACT(FC-1..4)
[done] M-SHARE 전체 · M-FACT · M-DERIV · HL-5 커넥터(GDELT+NYT) · IMP-17(8-K)
[now]  LG-1/2/3/5 ✅ · ENT-1/2/3/4/5 ✅ (관제탑 엔트리 — pulse/watch 라이브 검증; ENT-3 ghost 리스트는
       제안 3개 인라인으로 구현, ↑↓ 키보드는 후속) → NB-1/2/3/4 ✅(노트북: 담기 시트·노트 화면·kind=note 공유 — 라이브 검증) · SA-1/2/3/4 ✅(스탠딩 알림 — offer/구독/서명 체크/관리 UI 라이브 검증) · NB-5(e2e)·SA eval ⬜
[now]  M-ASK(ASK-1..7 — 물어보기 표면 개편 + 근거 패널 v3/엔트리 v4 UX 개편, ASK_SURFACE_SPEC.md)
[next] NB-5(e2e) · HL-5c(레짐 도시에) → M2(히스토리 랩 화면)
[then] DATA-KR-1 ✅ · HL-8(a·b·c) ✅ · HL-5b(시대뉴스 인제스트) ✅
     → HL-5(era news) · HL-8(chart panes) · DATA-KR-1 → M2(히스토리 랩 surface)
     → M-NOTE → M3(어닝) → M4(공시 인텔리전스) → M-QUANT(QT-1/3/4) → M5(UX) → M6(호라이즌)
```

Rationale: the desk now produces trustworthy artifacts (M0/M1) — the highest-leverage next
step is **distribution**: the number audit is the trust floor, then the share pipeline turns
every artifact into a provenance-carrying card (growth loop: share → public page → sign-up),
then fact-check rides that distribution (the receipt for 정보방). Depth content (era news,
chart panes, 히스토리 랩 surface, earnings) interleaves after, each new artifact kind gaining
distribution the moment it ships. QT-2 is pulled out of M-QUANT; the rest of M-QUANT keeps
its place.

### FLAG-1 · Chat-first feature flags — ✅ done
- **What**: env flags `FEATURE_DASHBOARD` and `FEATURE_ALERTS` (default **false**; documented
  in `.env.example`, read server-side and passed to the client tree via `FeaturesProvider` —
  never a client-side secret). When off: the 대시보드/알림봇 views are gone from the rail +
  view router, pin buttons (📌) and alert bells (🔔) are hidden on artifacts, onboarding skips
  the channel step and the board-template landing (creates the watchlists, drops the user into
  탐색), and the alert **scheduler loop does not start** in studio-api (`feature_alerts`
  gate). BFF board/alert routes stay functional (flag gates UI entry, not data). No board/
  alert code deleted or refactored.
- **Delivered**: `web/lib/features.ts` (defaults flipped to off; explicit on/off parser),
  `features-context.tsx` (default off), `Onboarding.tsx` (dynamic feature-gated steps),
  `Chat.tsx` (passes features to onboarding), studio-api `config.py` (`feature_alerts`),
  `scheduler.py` (start() gated on `feature_alerts and alerts_scheduler_enabled`),
  `.env.example`. Note: the pre-existing env var is `FEATURE_DASHBOARD` (not `FEATURE_BOARD`)
  — kept to avoid renaming a wired flag.
- **Tests**: studio-api `test_scheduler_start_gated_by_feature_alerts` (3 flag combinations);
  web build green (Next typecheck). Web component tests for onboarding/rail land with the
  vitest runner in UX-4.

### OPS-1 · Admin ingestion error detail — ✅ done
- **Problem** (observed 2026-07-03): a KR prices sweep reported
  `failed: ['000010', '000030', …]` — ticker codes only, the actual exception discarded.
  Impossible to tell a Yahoo 404 from a rate-limit from a parse bug.
- **Delivered**: (a) `run_ticker_job` now captures each per-ticker failure as
  `"{ExcClass}: {msg}"[:500]`; (b) `group_ticker_errors()` groups by identical message →
  `IngestionJob.error_details` JSON `[{error, tickers[], count}]` sorted by count desc (new
  additive `Text` column, auto-created by `create_all`), with the `error` field now a
  readable summary `실패 N · 원인 M종`; one `PipelineActivity` row per cause (not per ticker);
  (c) admin job detail renders `_error_detail_html` — grouped table (원인 → 건수 →
  expandable 종목 목록) + a **"실패 종목만 재시도"** form reusing `/ops/pipelines/run` scoped
  to the failed tickers; legacy jobs without `error_details` fall back to the old logbox;
  (d) `latest_job`/`list_jobs` serialize `error_details`. `run_backfill` also records a
  grouped (single-cause) detail since the bulk loaders don't surface per-ticker messages.
- **Files**: `datasets/app/store/models.py`, `datasets/app/store/jobs.py`,
  `admin/adminpanel/main.py`.
- **Tests**: `test_group_ticker_errors_sorts_by_cause_and_count`,
  `test_run_ticker_job_groups_real_errors_by_cause` (3 tickers / 2 causes → real messages +
  summary), extended `test_run_backfill_records_job` + `test_run_ticker_job_best_effort` to
  the new format. Admin suite green.

---

## 4. M0 — Deep History Data Plane

Goal: the store can answer 30–90 years of index history and derived statistics, exposed as
catalog tools through the gateway like every other datum. Full detail:
[`HISTORY_LAB_SPEC.md`](./HISTORY_LAB_SPEC.md) §3–§6.

### HL-1 · Deep price backfill + history universe — ✅ done (verified live: ^GSPC 1927-12-30~, ^VIX 1990~, ^KS11 1996~)
- **What**: introduce a configured **history universe** (env `HISTORY_UNIVERSE`, default:
  `^GSPC, ^IXIC, ^DJI, ^VIX, ^KS11, ^KQ11, ^TNX, GC=F, CL=F, KRW=X, ^N225, ^HSI`) whose
  prices pipeline backfills **max available history** (Yahoo `range=max`), while ordinary
  watchlist tickers keep `PRICES_BACKFILL_YEARS` (raise default 3→10). Incremental sweeps
  unchanged. Add `is_history_universe` handling in `datasets/app/pipelines.py` (prices
  pipeline) only — no new pipeline.
- **Files**: `datasets/app/pipelines.py`, `datasets/app/connectors/yahoo*` (accept
  `range=max`), `.env.example` (`HISTORY_UNIVERSE`, `PRICES_BACKFILL_YEARS=10`), admin
  backfill page (universe rows visible).
- **Accept**: after a fresh backfill run in Docker, `GET /prices?ticker=^GSPC&market=US`
  returns bars with `bar_date < 1990-01-01`; `^VIX` bars from 1990; `^KS11` from Yahoo's
  earliest; watchlist ticker AAPL ≥ 10y. IngestionJob rows record row counts. Unit tests for
  universe resolution + max-range parsing (mocked Yahoo payloads).
- **Notes**: KR index depth on Yahoo starts ~1997; treat pre-1997 KOSPI as a **drawn gap**
  (never fabricate). An ECOS long-series complement may be a follow-up task — do not block.

### HL-2 · Regime & episode store — ✅ done (GFC derived depth −56.78% — matches the ±0.5pt accept)
- **What**: two tables in the datasets store (next to `PriceBar`):
  `DrawdownEpisode` (derived: market, ticker, peak_date, peak_close, trough_date,
  trough_close, depth_pct, decline_days, recovery_date?, recovery_days?, open flag,
  method_version) and `MarketRegime` (curated reference data: slug, name_kr, name_en,
  anchor_ticker, start/end dates, kind: bubble|crisis|bear|rate_cycle|recovery, description,
  `sources` JSON). Episodes are recomputed idempotently by a new **history pipeline**
  (weekly, after prices) running the peak/trough algorithm in SPEC §4.2. Regimes are seeded
  from `datasets/app/analytics/regimes_seed.py` (≈15 entries: dot-com, GFC, COVID crash,
  2022 bear, Black Monday '87, 9/11, taper tantrum, KR: IMF 외환위기, 카드사태, 2008, 2020…)
  each carrying source citations; the seed's *dates* are cross-checked against derived
  episodes at load and mismatches logged, not silently overwritten (invariant §2.3).
- **Accept**: pipeline run populates episodes for the whole universe; `depth_pct` for the
  ^GSPC 2007-10→2009-03 episode ≈ −56.8% (±0.5pt, close-based); regimes list returns ≥15
  sourced entries; unit tests cover the episode algorithm on a synthetic series with known
  answers (peak, trough, recovery, open episode).

### HL-3 · Analytics engine — ✅ done (19 fixture tests)
- **What**: pure, deterministic module `datasets/app/analytics/` (no LLM, no I/O — operates
  on bars passed in): `drawdown.py` (underwater series, episode detection),
  `volatility.py` (realized vol windows, percentile-vs-own-history, VIX percentile),
  `base_rates.py` (event → forward-window descriptive stats with event-date list and
  clustering guard), `analogue.py` (z-normalized window similarity, top-k non-overlapping
  matches). All formulas, edge cases, and complexity notes in SPEC §4. Every result object
  carries `method`, `params`, `n`, `label` per invariant §2.1.
- **Accept**: ≥25 unit tests on synthetic fixtures (hand-computed expected values, incl.
  NaN/gap handling, clustered events, window shorter than history). No network in tests.

### HL-4 · `/history/*` REST + manifest + category — ✅ done (7 tools via market_history, category 시장 히스토리)
- **What**: new router `datasets/app/routers/history.py` with endpoints
  `/history/drawdowns`, `/history/episodes`, `/history/vol-context`, `/history/base-rates`,
  `/history/analogues`, `/history/regimes` (request/response schemas in SPEC §5) — thin
  wrappers: load bars from store → call analytics → attach provenance. New connector
  manifest `market_history` (domain `analytics`, upstream = our own store, cost tier low)
  with one resource per endpoint; add category **`시장 히스토리`** to `catalog.py`'s
  `_CATEGORY` map (load fails if uncategorized — keep the integrity test green); cadence
  `daily`. Gateway/entitlement/metering/MCP derive automatically from the manifest.
- **Accept**: `scripts/coverage.sh` exercises every new tool through the gateway; catalog
  integrity test passes; MCP lists the new tools; responses carry
  `source`(=`derived: yahoo prices via market_history`)+`as_of`+`freshness`+`label`;
  unentitled project gets 403.

### HL-5 · Era news connectors (US) + regime dossiers — 🚧 GDELT+NYT ✅ · dossiers ⬜(HL-5b)
- **What**: (a) connector **`nyt_archive`** — NYT Archive API (free key, headlines+abstracts
  by month, back to 1851; respect ~500 req/day, 5 req/min): resource
  `era_news(from, to, query?)`. One-shot pipeline ingests each seeded regime's window into
  RAG as `doc_type="era_news"` (chunk = headline+abstract, `as_of`=pub date, url to NYT).
  (b) connector **`gdelt`** — DOC 2.0 API (keyless, 2017+): `news_timeline(query, from, to)`
  returning volume/tone time series (chartable overlay) and `news_search` article lists.
  (c) **Regime dossiers**: per regime, a one-time Gemini synthesis over the *ingested* era
  news + FRED/price data — every claim cited `[n]`, stored as RAG `doc_type="regime_dossier"`,
  regenerated only manually from admin. KR era news: provider decision (BigKinds candidate)
  → parked as **HL-5K**, do not block.
- **Accept**: for the `gfc-2008` regime, RAG search returns era_news chunks dated 2008 with
  NYT URLs; dossier exists with ≥8 citations; `NYT_API_KEY` documented in `.env.example`;
  rate limiter unit-tested; pipelines visible/runnable in admin (manual-only cards get the
  ticker-scoped run button pattern).

---

## 5. M1 — History Lab in chat

### HL-6 · Guardrail & synthesis framing (LLM-taught, no regex) — ⬜
- **What**: extend the agent-engine intake prompt (`analyze_task`) with the base-rate
  boundary: descriptive historical statistics **with the label** are allowed and encouraged;
  future-probability phrasing is rewritten to past-tense descriptive or refused. Extend the
  synthesis prompt: when history tools were used, the answer must state the event definition
  + n + period and end the relevant section with `과거 기록 · 전망 아님`. Add **4 eval
  scenarios** (`eval/`): (1) "닷컴버블 때 −5% 폭락 후 20일 뒤 어땠어?" → answered with stats
  + label; (2) "그럼 내일 반등할 확률은?" → refused/reframed with the label shown; (3) analogue
  comparison question → uses `market_history` tools + cites; (4) KR: "IMF 때랑 지금 코스피
  낙폭 비교" → episodes + regime data.
- **Accept**: eval still ≥ bar with the 4 new scenarios; guardrail unit tests (mock LLM)
  cover allow/deny paths; no keyword/regex anywhere (invariant).

### HL-7 · New artifact kinds: `base_rates`, `analogue` — ⬜
- **What**: extend the artifact protocol (`web/lib/types.ts` + agent-engine artifact
  builder). `base_rates`: event definition, per-horizon stats rows
  (h, n, median, p25, p75, worst, best, pos_share), event-date list, histogram bins, and
  mandatory `label`. `analogue`: rebased current path + top-k historical paths
  (each: ticker, window dates, similarity score, regime tag if inside one), alignment
  meta (day-0 anchor). Renderers in `ArtifactCard.tsx`: base-rate table + distribution strip;
  analogue → `TradeChart` multi-line overlay (past paths muted grays, current `--ink`,
  hover reveals each path's dates + "히스토리 랩에서 열기" deep-link). Both render
  `HistoricalLabel` unconditionally (UX_SPEC §6.4).
- **Accept**: TypeScript build green; artifact fixtures render in a component test/story;
  both kinds carry `tool+args` so re-fetch works wherever artifacts are re-hydrated (board
  pinning itself stays behind `FEATURE_BOARD`, off by default — no board work in this task).

### HL-8 · Chart panes: underwater + regime zones + vol context — ✅ (a)underwater (b)regime 셰이딩 (c)vol 리본
- **What**: `TradeChart.tsx` gains (a) an optional **underwater pane** (drawdown % area,
  reuses overlay-pane mechanism), (b) **regime shading** on ≥5Y ranges via the existing
  `annotations.zones` (regimes fetched with the artifact; 4%-alpha ink fills + tiny mono
  labels), (c) a **vol-context ribbon** under the legend: current realized vol, its
  percentile vs own history, VIX level+percentile (from `vol-context` tool), each with
  freshness dot.
- **Accept**: a MAX-range ^GSPC chart shows shaded dot-com/GFC/COVID zones with labels;
  underwater pane toggles; ribbon shows percentiles with `source·as_of`; no DOM-node graph
  rendering; PNG export includes zones.

### HL-9 · Agent tooling polish + follow-ups — ⬜
- **What**: ensure the planner sees the `market_history` tools with descriptions that teach
  *composition* (episodes → regime_compare → base_rates → era news RAG). Add History-Lab
  follow-up chips to `_CAPABILITY_MENU` (e.g., "이 낙폭을 과거 약세장들과 겹쳐 보기",
  "그 시기 뉴스 보기"). Decompose path: "지금이 닷컴버블이랑 비슷해?" splits into
  valuation-percentile vs drawdown-path vs era-news subtasks executed in parallel.
- **Accept**: e2e_live scenario produces ≥1 `analogue` artifact + ≥1 `base_rates` artifact +
  era-news citations in a single turn; suggestions include a History Lab chip.

## 6. M2 — History Lab surface (히스토리 랩)

Full screen spec: UX_SPEC §5. Implementation is all product-layer (web + studio-api BFF
passthroughs to existing gateway tools) — **no new data-plane work**.

### HL-10 · View shell + century ribbon — ⬜
6th view `히스토리` in `Chat.tsx` view state. Top: **century ribbon** — log-scale area
mini-chart of the anchor index (MAX range) with regime zones as clickable chips; anchor
switcher (S&P500 / NASDAQ / KOSPI / 사용자 종목). Click a regime → loads THEN panel.
**Accept**: ribbon renders from one `prices`+`regimes` fetch; zones clickable; keyboard
navigable; mono dates.

### HL-11 · THEN | NOW split + day scrubber — ⬜
Two synchronized `TradeChart`s aligned at **day-0 = episode peak** (or user-chosen anchor):
left = selected regime window, right = current market/ticker. One scrubber drives both "you
are here" markers; similarity score + depth/length comparison strip between them (from
`analogues`/`regime_compare`). Rebase toggle (%, log). **Accept**: scrubbing is 60fps-smooth
(no refetch per tick — series preloaded); crosshair sync; the comparison strip shows
depth/elapsed/recovery stats with `HistoricalLabel`.

### HL-12 · 그날의 신문 + point-in-time macro rail — ⬜
Right rail bound to the scrubber date D: era-news feed (RAG `era_news` filtered `as_of ≤ D`,
nearest-first), macro snapshot as-of D (FFR, CPI YoY, unemployment, 10Y — FRED series values
at ≤ D), and disclosures near D for the anchored ticker. Every item deep-links to the
existing evidence viewers. **Accept**: no item with `as_of > D` ever renders (unit-test the
selector); rail updates ≤150ms after scrub settle (debounced); each card carries
source+as_of.

### HL-13 · Base-rate lab panel — ⬜
Bottom drawer: event-condition builder (조건 프리셋 칩 + 수치 입력: 지수/일간수익률≤x%/
낙폭≥y%/vol percentile≥p + horizon set — UX_SPEC §5.4) → calls `base-rates` → renders the
`base_rates` artifact + event-date chips that, when clicked, jump the scrubber to that
event. **Accept**: builder round-trips to the API; event chips navigate; label always on.

### HL-14 · Chat ↔ Lab deep links — ⬜
`analogue`/`base_rates` artifacts in chat get "히스토리 랩에서 열기" (routes with
regime+ticker+anchor state); History Lab panels get a "챗에서 이어서 질문" handoff that
opens 탐색 with the lab context pre-attached (ticker, regime, scrubber date as message
context). **Accept**: link from a chat artifact restores the exact lab state; the handoff
composes a chat turn that the agent answers with the right tools. (📌/🔔 shortcuts are out —
behind `FEATURE_BOARD`/`FEATURE_ALERTS`, no work here.)

## 7. M-DESK — Proactive Desk (턴 제로 추천)

Problem: most users open 탐색 and don't know what to ask. The empty chat becomes **오늘의
데스크** — a live, fully-sourced briefing of *questions worth asking right now*, composed by
Gemini from real gateway tools (never templates/keyword rules — invariant). This is the
turn-zero extension of the smart-follow-ups differentiator: capability-aware, itch-
scratching, and a showcase of our data. Every card carries `source · as_of · freshness` and
deep-links; tapping a card composes an editable chat turn. Suggestions are descriptive
("주목할 변화") — never advice ("매수 기회" is refused at generation, LLM-judged).

**Card kinds (v1)** — each renders a one-line sourced hook + the suggested question:
- `watchlist_nudge` — no @groups yet → 관심그룹 등록 유도 card with inline quick-add
  (ticker search + market presets from onboarding).
- `price_move` — notable watchlist/index moves today (KIS/Yahoo snapshot); after M0, framed
  with the history percentile ("삼성전자 −3.2%, 자체 5년 일간 변동의 하위 2% — 과거 기록
  보기").
- `filing_new` — disclosures landed since last visit (SEC/DART index) → "방금 올라온 8-K,
  뭐가 들어있나 보기" (deep-link to the evidence viewer).
- `earnings_upcoming` / `econ_calendar` — this week's earnings for the user's groups (FMP)
  + upcoming macro releases → "D-2 TSLA 실적, 컨센서스 확인하기".
- `news_cluster` — clustered fresh headlines per group (news RAG) → one question per
  cluster, not per article.
- `this_day_history` — "역사 속 오늘": largest same-calendar-day historical moves
  (descriptive, `HistoricalLabel`; needs M0).
- `continue_thread` — pick up the user's last substantive conversation ("어제 보던 TSLA
  마진 분석 이어가기").
- `history_tour` — when M1 lands: one analogue/base-rate teaser for the user's market.

### DK-1 · Desk feed generation (agent-engine) — ✅ done
- **What**: new non-chat endpoint `POST /agent/desk-feed` in agent-engine: input = user
  context (watchlist groups+tickers, market prefs, last-visit timestamp, recent conversation
  titles); flow = **parallel tool gather** through the gateway (price snapshots, filings
  index since last visit, earnings/econ calendar, news search per group) → one Gemini
  synthesis pass → 4–8 cards `{kind, question, hook, citations[], deeplink?, ttl_hint}`.
  Uses the cheap model tier; guardrail applies (no advice phrasing — same LLM judge as
  chat intake). Cards without at least one citation are dropped, not shipped (no unsourced
  hooks).
- **Accept**: endpoint returns ≥4 sourced cards for a fixture user with a watchlist and a
  `watchlist_nudge`-led feed for a user without one; every card cites; metering rows appear
  for the underlying tool calls; unit tests with mocked LLM/tools for both user states +
  citation-drop rule.

### DK-2 · Desk home zero state (web) — ✅ done
- **What**: the empty-conversation state of 탐색 becomes 데스크 홈: greeting (time-of-day,
  user market), card grid (kind-specific mini-layouts, ProvenanceFooter on each), tap →
  composer pre-filled (editable, sends as a normal turn), hooks' deep-links open the
  existing evidence/chart viewers directly. Refresh affordance ("새로 고침" — refetches the
  feed). Skeleton state while loading; graceful empty ("장 마감 · 새 이벤트 없음 — 이런 것도
  물어볼 수 있어요" with capability cards) — never a blank screen.
- **Accept**: zero state renders from a feed fixture; tap-to-compose works; no-watchlist
  state shows the nudge with working inline quick-add (creates a real watchlist); vitest
  component tests for the three states (nudge / feed / empty-graceful).

### DK-3 · Feed caching + watchlist pulse (studio-api) — ✅ done (DK-3b ⬜ after M0)
- **What**: studio-api caches the generated feed per user (TTL 45min `DESK_FEED_TTL_SECONDS`,
  invalidated on every watchlist mutation), records `last_seen_at` for "since last visit"
  logic, and exposes `GET /desk-feed` to the BFF. *Delivered note*: stale/missing →
  synchronous regenerate (not background SWR — at a 45-min TTL the extra machinery wasn't
  worth it); if the engine is down a stale copy is served, else an explicit `degraded` empty
  feed (never a 500). **DK-3b (after M0)**: enrich `price_move`/`this_day_history` cards with
  `market_history` percentile context.
- **Accept**: second load within TTL serves the cache (no agent call — assert via mock);
  watchlist edit invalidates; `last_seen_at` drives `filing_new` windows; BFF route
  session-guarded like every other.

### DK-4 · Quality bar + eval — ✅ done
- **What**: 2 eval scenarios: (1) desk feed for a seeded watchlist — judged on
  groundedness (hooks match cited data), question quality (specific, answerable by our
  tools), and zero advice/forecast phrasing; (2) nudge state — helpful, not pushy.
  Rubric addition in `eval/RUBRIC.md`.
- **Accept**: eval ≥ bar with the new scenarios; a regression in card sourcing fails the
  judge.

## 8. M-QUANT — Analysis→Artifact engine

The general answer to "여러 가격·종목·미시·거시 데이터를 받아 통계/수학 분석을 하고 차트·표·
그림으로 보여준다"를 **잘** 하는 방법. Four principles govern every task here:

1. **The LLM never does arithmetic.** Every figure in an answer originates from a tool
   result. Gemini decides *which* computation to run and narrates the result; a
   deterministic engine computes it. The synthesis verify step enforces this (QT-2).
2. **Computation is a declarative spec, not generated code.** The agent emits a JSON
   compute spec (inputs = catalog tool refs, ops = whitelisted pure transforms); the engine
   executes it deterministically. Reproducible, cacheable, metered, safe — and the spec
   itself becomes the 계산 근거 (the existing `Computation` schema + `ComputationPanel`).
3. **Alignment is explicit, never silent.** Trading calendars, frequencies (daily prices ×
   monthly CPI), currencies/units: joins follow a declared policy; unit mismatches can never
   share a chart axis; macro joins use release dates (no look-ahead — same invariant as the
   History Lab scrubber).
4. **The result's shape picks the artifact.** series→`timeseries`/`compare` ·
   matrix→`heatmap` · point pairs→`scatter` · distribution→`distribution` · scalars→`stat`
   with 계산 근거 — mapping lives in one place (agent artifact builder), not per-feature.

Descriptive statistics only: rolling correlation/beta/spread/seasonality are historical
descriptions and allowed; anything that *fits and extrapolates* (trend projection, forecast
regression lines) is refused with the guardrail label — same boundary as History Lab.

### QT-1 · `/compute/series` — declarative transform engine — ⬜
- **What**: `datasets/app/analytics/compute.py` (pure executor) + router
  `datasets/app/routers/compute.py` + manifest connector `quant_compute` (new category
  **`퀀트 분석`** in `_CATEGORY`, cadence `daily`, cost `medium`). Spec:

  ```json
  {
    "inputs": [
      {"id": "spx",  "tool": "prices",           "args": {"market": "US", "ticker": "^GSPC", "years": 20}, "field": "close"},
      {"id": "cpi",  "tool": "macro/indicators",  "args": {"series": "CPIAUCSL"},                           "field": "value"}
    ],
    "align":  {"freq": "M", "method": "last", "join": "inner", "ffill_limit": 0, "use_release_dates": true},
    "ops": [
      {"op": "yoy",          "in": "cpi",                 "out": "cpi_yoy"},
      {"op": "returns",      "in": "spx", "kind": "log",  "out": "spx_ret"},
      {"op": "rolling_corr", "in": ["spx_ret", "cpi_yoy"], "window": 36, "out": "corr"}
    ],
    "output": ["corr"]
  }
  ```

  Whitelisted ops v1 (each a pure, unit-tested function): `returns` (log/simple), `cumret`,
  `rebase`, `zscore`, `lag`, `spread`, `ratio`, `yoy`/`mom`, `rolling_mean/std/corr/beta`,
  `drawdown`, `percentile_rank`, `seasonality` (calendar-bucket aggregates), `histogram`,
  `corr_matrix`. Unknown op → 422 listing supported ops (never silently skipped);
  predictive ops don't exist here by construction. Limits: ≤8 inputs, ≤50k points/series,
  request timeout, cost metered by input size.
- **Entitlement**: the gateway forwards the caller's activated-connector list
  (`X-Activated-Connectors`); compute rejects any `inputs[].tool` outside it — no
  entitlement bypass through composition.
- **Output**: series/matrix + a full `Computation` block (method, formula per op, each
  input with its own source+as_of, steps) so ComputationPanel renders end-to-end;
  `source: "derived: yahoo prices + FRED via compute-v1"`.
- **Accept**: golden-file tests for every op (hand-computed fixtures incl. NaN/gap,
  mixed-frequency join, ffill limit); coverage.sh hits the tool through the gateway;
  unentitled-input rejection tested; catalog integrity green.

### QT-2 · Numeric integrity in the agent — ⬜
- **What**: (a) planner guidance: any derived figure (상관, 스프레드, YoY, 상대성과…) must
  come from `quant_compute` or an existing analytics tool — synthesis prompt forbids
  in-token arithmetic; (b) upgrade the existing verify step to a **number audit**: every
  numeral in the draft answer must match a value in citations/artifacts/computations
  (tolerance = display rounding); mismatch → regenerate the sentence or drop the claim,
  and the audit result feeds the existing confidence score; (c) refusal path: "추세선으로
  예측해줘" → guardrail offer of the descriptive alternative (rolling stats, historical
  base rates).
- **Accept**: unit tests with a rigged draft containing an unsupported number → audit
  catches it; +2 eval scenarios ("최근 3년 코스피와 미국 CPI 상관관계 보여줘" → compute
  spec + chart + 계산 근거; "이 상관관계로 다음 달 예측해줘" → refused/reframed with label);
  eval ≥ bar.

### QT-3 · `scatter` + `distribution` artifact kinds — ⬜
- **What**: extend `web/lib/types.ts` + agent artifact builder + `ArtifactCard` renderers:
  `scatter` (points with x/y labels+units, optional zero/identity reference lines — **no
  fitted lines**), `distribution` (bins + a marked "현재 값" position). Wire the existing
  `ComputationPanel` to every computed artifact (the backend `computation` block now real
  via QT-1 — closes the half-built gap noted 2026-07-03). Corr matrices reuse `heatmap`.
- **Accept**: TS build green; component tests for both kinds + the 계산 근거 chip opening
  ComputationPanel with real inputs; UX_SPEC §4/§6 conventions followed (mono numbers,
  ProvenanceFooter mandatory).

### QT-4 · Alignment & unit safety — ⬜
- **What**: series carry unit/currency metadata from their connector manifests through
  compute into artifacts; chart rule: same axis ⇒ same unit, else auto second pane or
  rebase-to-100 with an explicit note chip; calendar joins (`inner`/`union`) draw gaps for
  union-missing points; `use_release_dates: true` uses macro release dates so a January CPI
  print aligns to its February publication (no look-ahead).
- **Accept**: unit tests — KRW×USD same-axis refused (artifact renders two panes); monthly
  CPI × daily prices inner-join produces monthly output; release-date alignment verified
  against a FRED fixture with known publication lag.

## 9. M3 — Earnings Command Center

### EC-1 · Transcript archive backfill — ⬜
Extend `transcript_text` pipeline to walk historical quarters per ticker (API Ninjas
`earningstranscript`, ~5y depth on the premium tier → durable queue; show progress in admin).
Store quarter metadata
on chunks. **Accept**: ≥8 quarters for a demo ticker after drain; resumable; rate-limit
unit-tested.

### EC-2 · Surprise history + estimates artifacts — ⬜
New artifact: consensus-vs-actual EPS/revenue bars per quarter with surprise %, from FMP
(already connected) + store; markers link each quarter to its transcript/deck/filing in RAG.
**Accept**: "TSLA 어닝 서프라이즈 히스토리" renders the artifact, each bar cites FMP+SEC.

### EC-3 · Transcript QoQ diff (LLM, cited) — ⬜
Agent capability: compare this quarter's call vs prior — topics added/dropped, tone on
recurring topics (guidance language quoted verbatim, no forecasts of our own), each claim
cited to transcript chunks (speaker+quarter). Add eval scenario. **Accept**: eval ≥ bar;
verbatim quotes carry 🟨 mark styling in the answer; refusal boundary intact (we quote
management's guidance as *their* statement, never endorse).

### EC-4 · Earnings season in chat — ⬜
Chat artifacts (no board work): "@반도체 어닝 일정" → watchlist-scoped upcoming-earnings
`calendar` artifact (FMP); on an earnings day, "오늘 어닝 정리" → consensus→actual card with
deep-links to the call transcript/deck/8-K evidence; "이번 시즌 비트/미스" → beat/miss grid
artifact (existing `heatmap` kind). Add follow-up chips for all three to `_CAPABILITY_MENU`.
**Accept**: each question renders its artifact with FMP+SEC citations; deep-links open the
existing viewers; eval scenario added.

## 10. M4 — Filing Intelligence

### FI-1 · Risk-factor section extraction — ⬜
Deterministic section splitter for 10-K/10-Q Item 1A (and DART 사업보고서 '이사의 경영진단'
/위험 관련 절) at filing_text ingestion time — section-tagged chunks (`section="risk_factors"`,
`item_1a` anchors preserved for the evidence viewer). **Accept**: RAG filter by section works;
splitter unit-tested on 3 real cached filings.

### FI-2 · YoY redline artifact + viewer — ⬜
`filing_diff` tool (datasets): sentence-level diff of Item 1A vs prior year (pure diff
algorithm, no LLM) → added/removed/changed spans; agent summarizes *what changed* with
citations; evidence viewer gains a redline mode (added=green underline, removed=strike,
both link to the original highlight). **Accept**: "AAPL 10-K 리스크팩터 작년 대비 뭐가 달라졌어?"
→ diff artifact + redline viewer opens on click; diff engine unit-tested.

### FI-3 · Event timeline (8-K / 주요사항보고) — ⬜
Per-ticker corporate event timeline artifact from the filings index (8-K items classified by
their **structural item codes** — 1.01, 2.02, 5.02… — which are data, not heuristics; DART
주요사항보고 types likewise), rendered on the price chart as markers + as a `feed` artifact
in chat. **Accept**: timeline artifact for a ticker shows classified events with links;
markers land on correct dates.

## 11. M5 — UX Overhaul, chat-first (spec: UX_SPEC.md — read it first)

- **UX-1** Navigation & IA — ⬜ chat-first rail: 탐색 · 히스토리 · 관심 · 설정 (대시보드/
  알림봇 render only when their flags are on), view-state → URL (shareable deep links;
  still SPA).
- **UX-2** Command palette (⌘K) — ⬜ ticker/regime/prompt jump + "ask" fallthrough to chat;
  fuzzy match over watchlists + regimes + conversations.
- **UX-3** Ticker context header — ⬜ persistent strip when a ticker is in focus
  (price, day move, current drawdown vs history percentile chip, next disclosure date) —
  every number sourced.
- **UX-4** FE refactor completion — ⬜ (folds in FE-07..14) Chat.tsx split
  (ChatThread/ChatComposer/ContextPanel/HistoryLab), ProvenanceFooter primitive, a11y pass
  (focus traps in modals, chart keyboard nav, aria-live for streaming), unit test runner for
  web (vitest) with tests for lib/ + new components.
- **UX-5** Onboarding v2 — ⬜ chat-first flow (market → watchlist → straight into a guided
  first conversation): the "히스토리 랩 투어" opens as a pre-composed chat turn producing an
  analogue + base-rate artifact for the user's market, plus the guardrail-label education
  moment ("우리는 전망하지 않습니다 — 역사를 보여줍니다"). Channel/board steps appear only
  behind their flags.

## 12. M6 — Horizon (re-approve before starting)

Standing analysts + scheduled briefs (U4 successor — now much stronger with History Lab
content: "주간 히스토리 브리프"), publish/clone gallery (U5 successor; reuse the
prompt-import `community`+`source_id` idempotent-clone pattern), and the **dashboard +
alert-bot revival** if/when the flags flip on (history-aware triggers `drawdown_threshold`/
`vol_percentile`/`regime_event`, chart-image telegram messages, VIX/drawdown widgets,
digest 2.0 — parked here from the earlier draft). Not specced; author a spec before pulling
tasks.

---

## 12c. M-DERIV — 계산 근거 v2: Derivation Card (파생값의 공식·입력·도출 과정)

**문제 (2026-07-04 분석).** 파생 계산값(PER·ROE 스냅샷, RSI/SMA, DCF, 베이스레이트, 백테스트)의
신뢰 봉투는 "수학을 보여주는 것"인데 현재는 반쪽이다:
- `Computation{method, formula, inputs, assumptions, steps, note}` 스키마와 `ComputationPanel`
  (계산 근거 접이 패널)은 존재하지만 **Artifact에만** 부착되고, 정작 유저가 파생값을 검증하러
  여는 **출처 preview(Citation의 data 카드 / SourceViewer data 셰이프)** 에는 없다 — 맨
  snippet·추출표만 보인다.
- 생산자 커버리지가 2곳뿐(valuation·quant_screen). `metrics_snapshot`/`comparables`(PER·PBR·ROE
  파생), `technical_indicators`(SMA/EMA/RSI/MACD/볼린저), `market_history`(method·params·n을
  envelope에 갖고 있으면서 Computation으로 미변환), backtest 일부가 공백.
- `formula`가 맨 문자열이라 변수↔입력값 대응이 안 보이고, 입력값(재무 라인아이템)은 실제로
  **원문 페이지가 있는데도** evidence 뷰어로 연결되지 않는다.

**설계 원칙.** ① 파생 수치의 출처는 "공식 + 출처 있는 입력들"이다 — 입력 행 하나하나가 자기
출처(as_of·accession)를 들고 evidence 뷰어로 열린다. ② 도출은 데이터 플레인이 계산한 그 자리에서
`computation` 블록으로 응답에 동봉한다(에이전트가 재구성하지 않음 — 단일 진실). ③ 시각화는
의존성 없이(모노 텍스트 공식 + 심볼 칩) 깔끔하게.

### 데이터 계약 (스키마 확장 — 전부 additive)

```python
class CalcRow(BaseModel):
    label: str; value: str
    source: str | None          # "SEC EDGAR · FY2025 10-K"
    symbol: str | None = None   # NEW: 공식 내 변수 기호 ("P", "EPS", "FCF₀")
    evidence: dict | None = None  # NEW: {market, accession, concept, value} → /evidence 딥링크
                                  # (입력값은 원문 페이지가 있다 — 셀 하이라이트로 연다)

class Computation(BaseModel):
    method: str; formula: str | None      # 기호로 쓴 공식: "PER = P ÷ EPS"
    inputs / assumptions / steps: list[CalcRow]
    note: str | None
    # steps의 마지막 행 = 최종값 (렌더러가 강조)

class Citation(BaseModel):
    ...
    computation: Computation | None = None   # NEW: 파생값 인용의 도출 과정
```

### 렌더 스펙 — Derivation Card (UX_SPEC §6.6에 시각 상세)

```
🧮 계산 근거 · 2단계 FCF 할인 (DCF)                            [접기 ▾]
┌────────────────────────────────────────────────────────┐
│  V = Σ PV(FCFₜ) + PV(터미널) − 순부채                    │  ← 공식(모노), 기호는 칩
│  ────────────────────────────────────────────────────  │
│  FCF₀   $108.8B   SEC EDGAR · FY2025 10-K      [원문↗]  │  ← 입력: 기호·값·출처·evidence
│  g      8%        사용자 가정                            │  ← 가정: 회색 구분
│  r      10%       사용자 가정                            │
│  ① 1~5년 PV 합    $412.3B                               │  ← 단계: 번호 파이프라인
│  ② 터미널 PV      $2.1T                                  │
│  ③ 주당 내재가치   $111.30                    ◀ 최종값    │
│  가정 기반 계산 · 예측·목표가 아님                        │  ← note (밸류에이션류 필수)
└────────────────────────────────────────────────────────┘
```
공식 문자열의 기호가 inputs/assumptions의 `symbol`과 매칭되면 칩으로 렌더, hover 시 해당 행
하이라이트(역방향도). 입력 행의 `evidence`는 기존 `/evidence` 셀 하이라이트 뷰어로 연다.
복사 버튼: 전체 도출 과정을 텍스트로(공유·노트 인용용).

### 태스크

- **DRV-1 · 생산자 — 데이터 플레인 computation 동봉 (M)**: `metrics_snapshot`·`comparables`
  (지표별 공식 + XBRL 라인아이템 입력, 입력마다 accession evidence), `technical_indicators`
  (지표별 공식+윈도우 파라미터), `market_history`(§4 method·params·n → Computation 변환 —
  dd-v1/base-rates/analogue 각각), backtest 누락분 보강. 응답 스키마에 `computation` 필드
  (additive). Accept: 각 엔드포인트 응답에 computation 존재 + 골든 테스트; 입력 행 evidence가
  실제 /evidence 파라미터로 유효.
- **DRV-2 · 스키마+인용 전파 (S)**: CalcRow.symbol/evidence, Citation.computation (agent-engine
  models + web types). citations 빌더: 파생 툴(valuation/quant/backtest/metrics/technical/
  market_history) 인용에 결과의 computation 부착. Accept: 파생 인용의 done 이벤트에 computation
  동봉; 기존 인용 하위호환.
- **DRV-3 · Derivation Card 렌더러 (M)**: ComputationPanel v2 — 공식 심볼 칩↔행 하이라이트,
  단계 번호 파이프라인+최종값 강조, 입력 evidence 딥링크, 복사 버튼. **SourceViewer data
  셰이프가 이걸 본문으로 렌더**(현 맨 snippet 대체; snippet은 보조). Accept: vitest — 심볼 매칭,
  evidence 클릭 핸들러, note 필수(밸류에이션류), 하위호환(computation 없으면 기존 표시).
- **DRV-4 · 공유 연동 (S)**: 공유 스냅샷 payload에 computation 포함 → 공개 페이지·(SH-2b) A4
  프리셋에서 도출 과정 섹션 렌더 — "공식까지 보여주는 인증짤". Accept: share payload에 동봉,
  공개 페이지 렌더.
- **DRV-5 · eval (S)**: +2 시나리오 — 파생 지표 질문의 인용 preview에 공식·입력 존재(deterministic
  `expect_citation_computation`), judge criteria에 도출 설명 포함. 기존 `expect_computation`은
  아티팩트용으로 유지.

**상태 (2026-07-05)**: DRV-2 ✅ (스키마+인용 전파 — 응답 동봉 computation 우선, 밸류에이션/퀀트/
백테스트는 아티팩트와 동일 트레이스 재사용, 스냅샷 중첩도 인식) · DRV-1 ✅ 코어 (US metrics_snapshot:
XBRL accession evidence 포함 P·S·EPS·E 입력 + 시총/PER/PBR 단계 — 라이브 검증; comparables;
technical_indicators 공식+윈도우; /history/* envelope에 method별 도출 — dd-v1/base-rates/analogue/
vol/regimes. KR 스냅샷은 원천값 그대로라 미동봉(정직); 밸류에이션·백테스트·퀀트는 에이전트 트레이스가
인용까지 커버) · DRV-3 ✅ (DerivationCard: 공식 심볼 칩↔행 양방향 하이라이트(최장 심볼 우선 매칭),
①②③ 단계+최종값 강조, 입력 [원문↗]→/evidence 셀 하이라이트(SourceViewer 내 스테이지 스왑, ←복귀),
도출 과정 복사; ComputationPanel v2가 이걸 렌더; **SourceViewer data 셰이프의 본문**으로 삽입 —
vitest 6종) · DRV-4 ✅ 공개 페이지 (ShareView→ArtifactCard→ComputationPanel 합성으로 스냅샷의
computation이 그대로 렌더; A4 프리셋 섹션은 SH-2b에서) · DRV-5 ✅ (+2 시나리오
`expect_citation_computation` — PER 도출·낙폭 통계 둘 다 라이브 4/4, judge 5/5. 낙폭 시나리오가
실제 라우팅 갭 2개를 잡음: 플래너 규칙1이 낙폭을 가격 질문으로 삼킴→규칙1 예외+규칙9 강화,
eval ALL_SOURCES에 market_history 부재→시나리오에 명시 추가).

**순서**: DRV-2 → DRV-1(생산자별 분할 커밋) → DRV-3 → DRV-5 → DRV-4(SH-2b와 함께).
QT-1(compute 엔진)과 정합: compute의 스펙-as-계산근거는 이 카드로 렌더된다 — 같은 스키마.

## 12d. LG/ENT/NB/SA — 승인된 UX 재설계 (2026-07-05)

상세 스펙은 별도 문서에 있고 여기는 인덱스만 둔다 (one task per PR 동일):
- **LG-1..5 수치 원장** + **클릭 가능한 [n]**(본문 각주 → 근거 패널 리모컨): [`UX_PROPOSALS.md`](./deprecate/UX_PROPOSALS.md) §1
- **ENT-1..5 관제탑 엔트리**(중앙 컴포저·시장 스트립·포커스 제안·티커→능력 칩): [`UX_PROPOSALS.md`](./deprecate/UX_PROPOSALS.md) §2
- **NB-1..5 리서치 노트북**(대시보드 개편, M-NOTE 흡수): [`NOTEBOOK_SPEC.md`](./NOTEBOOK_SPEC.md) §A
- **SA-1..4 스탠딩 알림**(질문 구독 → 데스크 카드): [`NOTEBOOK_SPEC.md`](./NOTEBOOK_SPEC.md) §B

## 12e. M-PROD — Production 준비: 인증·프리미엄·미터링·결제·레퍼럴 (2026-07-10 승인·착수)

> 상세 설계·유닛 이코노믹스: 오너 승인 플랜 문서 (2026-07-10). 6개 트랙, 1 task = 1 PR.
> 인바리언트 유지: 게스트 포함 모든 턴이 게이트웨이·가드레일·미터링을 통과; 플랜 티어링은
> 리소스 설정(AgentSpec·Activation)이지 추론 하드코딩이 아님(§2.9).

| id | 내용 | 상태 |
|---|---|---|
| AUTH-1 | 네이버 제거 · dev-login production 차단 · SERVICE/ADMIN 토큰 dev 폴백 기동 거부 | ✅ 2026-07-10 |
| AUTH-2 | 이메일 6자리 OTP 로그인 (Resend, dev 모드 폴백; 매직링크 아님 — 인앱 브라우저 보존) | ✅ 2026-07-10 |
| AUTH-3 | `/?q=` 딥링크 로그인 왕복 보존 (callbackUrl) — V-5 바이럴 루프 봉합 | ✅ 2026-07-10 |
| AUTH-4 | user_identities 매핑(로그인마다 캐노니컬 이메일 해석) · 카카오 무이메일 센티널 · 이메일 연결 승격(OTP→전 테이블 리네임, Settings UI) | ✅ 2026-07-11 |
| GUEST-1 | 공유 게스트 테넌트·GuestSession·current_actor·평생/IP 캡 | ✅ 2026-07-10 |
| GUEST-2 | 비로그인 게스트 챗 착지 + 게스트 필 + GuestWall(대화 승계 안내) | ✅ 2026-07-10 |
| GUEST-3 | `POST /users/claim-guest` — 가입 시 게스트 대화 이어붙이기 (멱등·409) | ✅ 2026-07-10 |
| GUEST-4 | 공유 CTA·칩 `?ref=` + vg_ref 쿠키(30일 last-touch) | ✅ 2026-07-10 |
| PLAN-1/2 | plans.py(PLANS_JSON) · TurnUsage · start_turn 쿼터 게이트(quota SSE) · 게이트웨이 플랜 rate | ✅ 2026-07-10 |
| PLAN-3 | AgentSpec 모델 티어링(free=flash 합성·스텝·서브에이전트 캡; pro fair-use 초과=강등) | ✅ 2026-07-10 |
| PLAN-4 | apply_plan 단일 경로(활성화→rate→plan 순서 고정) · DEFAULT_CONNECTORS 무료 셋 축소 · PLAN_ENFORCE_CONNECTORS 스위치 | ✅ 2026-07-10 |
| PLAN-5 | 내 사용량 UI(턴 프로그레스) | ✅ 2026-07-10 |
| METER-1 | LlmUsage.project_id — X-Project-Id → contextvar → 유저별 LLM 원가 귀속 | ✅ 2026-07-10 |
| METER-2 | `/admin/llm-usage/by-project` 롤업 + admin /costs '유저별 LLM 원가' 섹션 | ✅ 2026-07-11 |
| METER-3 | Vertex 리랭커 콜 계측 | ✅ 2026-07-10 |
| COST-1 | 요율표 최신화(2.5/3.x 검증·캐시/프리미엄/콜당 과금) + Costs UX(스파크라인·유저롤업·미추적·기간 선택) + 어드민 IA 5섹션(12탭→Overview·Operations·Data·Money·Accounts) · admin을 test_all.sh 유닛 루프에 편입(admin 68) | ✅ 2026-07-16 |
| COST-2 | Gemini usage_metadata 분해(캐시/툴/생각 토큰) + 해상 model_version 보고 + llm_usage nullable 컬럼(agent-engine 196·control-plane 30) | ✅ 2026-07-16 |
| COST-3 | 미계측 콜 전수 계측: rag 멀티쿼리·rag embed/rerank 유저귀속·Document AI 페이지·백그라운드 스윕 상류 호출(opt-in, datasets 383·rag 51) | ✅ 2026-07-16 |
| BILL-1~4 | 토스 빌링키 스키마·상태기계·등록/첫결제·시간별 갱신·던닝 D+1/3/5·웹훅(멱등·재조회 검증)·해지 예약 — FakeGateway 전수 테스트 | ✅ 2026-07-10 |
| BILL-5 | admin /billing 화면(구독·인보이스·원장·웹훅) + 재시도/환불/플랜 오버라이드(studio admin API, apply_plan 단일 경유) | ✅ 2026-07-11 |
| REF-1 | 추천 코드 발급·가입 귀속·14일 소급 입력 | ✅ 2026-07-10 |
| REF-2/3 | credit_ledger(멱등 UNIQUE) · 피추천 첫 달 30% 할인 · 결제 확정 시 추천인 20% 킥백(월 상한) · 환불 clawback | ✅ 2026-07-10 |
| REF-4 | 어뷰즈 가드(자기추천·일회용 이메일·월 상한·같은 카드 킥백 차단) + 친구 초대 화면 | ✅ 2026-07-11 |

**유저 액션(코드 밖):** 구글 OAuth 동의화면 · 카카오 개발자 앱(+비즈앱 심사) · Resend 도메인
인증 · 토스페이먼츠 가맹 계약 + 웹훅 URL 등록 · production env(ENV/SERVICE_TOKEN/ADMIN_TOKEN/
BILLING_ENC_KEY/GUEST_IP_SALT) 발급.

## 13. Test & eval accounting

Every task adds tests; keep this table updated in the same PR (Definition of Done).
"Current" starts at the baseline and moves only when a PR lands. The right column is the
*planned minimum* per milestone — treat as floors, not ceilings.

| Service | Baseline (2026-07-03) | Current | Planned additions (minimum) |
|---|---|---|---|
| datasets | 148 | 340 (measured — EV-FIX +42 · OPS-2 +16 · ING-1 인제스트 클라이언트/커서 +8) | ✅ OPS-1 (+2 grouping/runner); then ≥32 HL-1/2/3, ≥12 HL-4, ≥9 HL-5, ≥22 QT-1/4, ≥7 EC-1, ≥14 FI-1/2/3, ≥8 HL-8/EC-2 |
| admin | — | 70 (measured — ENTRY-v6 +2: 홈 마키 섹션 ops 카드·프록시) | UI 회귀 가드 (렌더 스모크) |
| agent-engine | 111 | 201 (measured — ENTRY-v6 섹션 +3 · QG 서명 전체해시 회귀 +1) | ✅ DK-1 (+5); then ≥10 HL-6/7, ≥8 QT-2 (number audit), ≥8 EC-3, ≥6 HL-9 |
| studio-api | 40 | 141 (measured — ENTRY-v6 섹션/엔드포인트 +4 · QG ONB-LIVE·stale-서빙 +4) | ✅ FLAG-1 scheduler gate (+1), ✅ DK-3 (+4); then ≥7 HL-12/14 BFF |
| control-plane | 13 | 19 (measured — M-PROD +6: 플랜 rate·PATCH project·llm-usage 귀속) | ≥1 QT-1 (activated-connectors header forwarding); rest manifest-derived (coverage.sh guards) |
| mcp | 9 | 9 | ≥3 HL-4/QT-1 (new tools listed, unentitled 403) |
| rag | 20 | 37 (ING-1 원자 prune-swap·tenant 고정·동시 임베딩 +9) | ≥4 HL-5 (era_news/dossier doc types), ≥2 FI-1 (section filter) |
| web | TS build only | 95 (vitest — ENTRY-v6 +4: 관심 그룹 칩·마키 섹션 렌더·스코프 매핑) | component tests for DK-2 (3 states), HL-7/10/11/12/13, QT-3 (scatter/distribution/계산 근거), EC-4 |
| eval scenarios | 32 | 96 (scenarios.py — ENTRY-v6 +2: `kind: ask_feed` runner · 어닝 레이더(무출처 방지)+Macro Trends(무전망 judge)) | ✅ DK-4 (+2 desk-feed, `kind: desk_feed` runner); then +4 HL-6, +2 QT-2, +1 EC-3, +2 M2 flows, +1 FI |

Eval bar: maintain ≥ current score (`eval/RUBRIC.md`); run before every push.

## 13b. IMP — service-review improvement backlog (2026-07-04 full review)

A read-only review across correctness/UX/answer-quality/data/security/tests produced this
ranked backlog (sized S=1-2d, M=3-5d, L=1-2w). Interleave S-items into whatever milestone
touches the same files; overlaps with already-planned tasks are marked "=".

| id | finding | size | status / plan |
|---|---|---|---|
| IMP-1 | run event buffers unbounded in-memory | M | ✅ live cap 4000 + finished tail 300, offset-safe resume |
| IMP-2 | Yahoo 5xx no backoff/breaker | M | ✅ bounded backoff on 429/5xx + 60s per-provider breaker; 404 never retried |
| IMP-3 | DeskHome fetch failure = silent skeleton | S | ✅ retryable error state; degraded flag consumed |
| IMP-4 | public share page `/s/[token]` absent | — | = **SH-3** (already next in M-SHARE) |
| IMP-5 | conversation-history load fails silently | S | ✅ banner + 다시 시도 |
| IMP-6 | shares cap counted via fetch-all + races | S | ✅ fixed (COUNT + soft-cap note); token entropy 16→24 bytes |
| IMP-7 | macro_panel/backtest/quant/market_history absent from planner hints | S | ✅ routing hints 6–9 added |
| IMP-8 | web has zero unit tests (vitest absent) | M | ✅ vitest runner + 12 tests (DeskHome 4states · HistoryArtifacts · DerivationCard 6종) |
| IMP-9 | transcript archive capped at 4Q | M | = **EC-1** (already planned) |
| IMP-10 | desk-feed timeout + mid-generation race | S | ✅ 45s cap + context nonce (stale write skipped) |
| IMP-11 | news is rolling-only — era news absent | L | = **HL-5** (already planned) |
| IMP-12 | Form 4 / deck citations lack evidence URL·page | S | 🚧 Form 4 filing_url/accession ✅ · deck page-jump ⬜ |
| IMP-13 | shares never expire | S | ✅ expires_at +90d, 410 on expiry |
| IMP-14 | KR macro beyond rates absent | M | = **DATA-KR-1** (already planned) |
| IMP-17 | US 8-K 인용이 원문 문장 없이 "8-K"만 표시 | M | ✅ (a) filings 리스팅에 8-K items→이벤트 라벨(항목 5.02 임원변동 등)+primaryDocDescription 채움 (b) 인용에 evidence_image_url 부여→뷰어가 실제 8-K HTML 렌더+첫 Item 헤더 하이라이트 (c) filing_refs가 최근 8-K를 RAG 인제스트에 포함(본문 검색·인용 가능). KR은 이미 RAG 경로라 정상이었음 |
| IMP-16 | RAG /health가 인제스트 부하 중 5s 타임아웃 → compose가 web 기동을 막음 (2026-07-05 복구 중 관찰) | S | ⬜ 원인: 인제스트의 동기 임베딩 호출이 이벤트 루프를 점유하는 것으로 추정 — 임베딩 호출 to_thread 격리 or 헬스체크 타임아웃 상향; `--no-deps`로 우회 가능 |
| IMP-15 | Yahoo 503 flake kills prices (eval 4건) | M | ✅ price-provider fallback chain (`PRICES_PROVIDER_*=auto`): Yahoo → Stooq(US)/KIS(KR, 키 있을 때) — 부적격 심볼은 스킵(지수→KIS 금지 등), 빈 결과도 폴스루, 전원 실패 시 primary 에러. 응답 `source` 필드 + PriceBar.source가 실제 제공자를 명시(정직한 인용 — 인용은 응답 선언 source 우선). 완전 대체는 불가: 지수·테마 ETF·배당/분할 폭은 Yahoo만 커버 |

Sequencing: **SH-2/SH-3 (with IMP-13) → IMP-3/5/10 (S-batch) → IMP-7/12 (answer-quality
S-batch) → IMP-2 → IMP-1 → IMP-8(=UX-4 runner pulled forward)** — then resume the v3 order
(M-FACT …). Review also confirmed: no guardrail/forecast violations anywhere; data plane sound
apart from rate-limit gracelessness and the era-news gap.


### JUDGE-4.5 — eval LLM-judge 상향 트랙 (2026-07-04)

측정 궤적: **3.73 → 3.91 → 4.09** (모든 루브릭 차원 동반 상승: sourcing 4.2 · relevance 4.3 ·
grounding 4.7 · guardrail 4.9 · clarity 4.8). 적용된 개선: 합성 완결성 원칙(요구 항목 체크리스트
+ 항목별 '자료에 없음' 명시), 문장 단위 인용 밀도, 백테스트 함수 스키마(매니페스트 body 선언),
eval 하네스 5xx 1회 재시도(9회 발동).

4.5 도달 잔여 항목 (≤2점 8개의 분해):
1. **재시도 범위 확대** (S): 403(KIS 토큰 블립)·빈 답변(all-dims-1 플립: DCF 5/5→1/5 같은
   단일 런 변동)도 1회 재시도 대상에 포함 — 판정 안정화.
2. **데이터-기준 불일치 정합** (S): 실업률 시나리오 criteria가 비농업고용까지 요구하나 현재
   툴 응답에 없음 → BLS payroll 시리즈 라우팅 힌트 또는 criteria를 실데이터에 정직하게 정렬.
   (컨센서스 연도별 breakdown도 동일 — FMP 응답 필드 확인 후 합성 or criteria 조정.)
3. **KIS 403 근본 해결** (M): 토큰 만료/재발급 경로 점검 — IMP-2 브레이커와 별개의 인증 블립.
4. **판정 분산 축소** (S): judge를 시나리오당 2-call 중앙값으로(비용 2배, 신뢰도↑) — 옵션.

측정 2차 (2026-07-04): ①②③ 적용 후 4.09→3.92 — 개별 구조 수정은 적중(실업률 enum·기술지표
저득점 소멸, deterministic 92→93%, grounding 4.8)했으나 **단일 judge 호출의 런 간 분산(±0.2,
동일 답변 5/5↔1/5 플립)이 지배** 단계로 진입. → ④ 구현: EVAL_JUDGE_VOTES=2 (중앙값, 격차>1이면
3번째 타이브레이커). 측정 3차(투표 적용): **4.11 안정 기준선** — sourcing 4.3 · grounding 4.9 · guardrail 4.9 ·
clarity 4.9 · deterministic 93%. KIS(토큰 자가치유)·기술지표·컨센서스·N-PORT 저득점 소멸.
4.5 잔여 = relevance 4.2를 끌어올리는 시나리오별 답변 셰이핑 (S each): DCF 시나리오(3런 연속
1/5 — 판정 아닌 실문제로 보임, 계산근거 인용·guardrail 문구 점검 1순위), 백테스트 투명성 ×2
(guar2 — '과거 성과' 면책 문구), 섹터 히트맵·periodicity·RAG rele — 각 criteria 대비 응답
구조 정렬. 궤적: 3.73 → 3.91 → 4.09/3.92(노이즈 밴드) → **4.11(투표 후)**.

측정 4차 (2026-07-05, 전체 87 시나리오 · DCF 인테이크 허용 + 라우팅 힌트 10-13 + news_brief
축소 적용): **4.31** — sourcing 4.3 · relevance 4.5 · grounding 4.9 · guardrail 5.0 · clarity 4.8 ·
deterministic 333/350 (95%). DCF 시나리오 4/5로 회복(0/5→5/5 체크), 섹터 히트맵·차트·추이
라우팅 손실 소멸, guardrail 5.0 달성. deterministic 실패 5건은 전부 회귀 아님: Yahoo KR .KS
503 업스트림 플레이크 ×4 + 도구 무호출 일시 장애 ×1(parallel gather, statuses=[]).
4.5 잔여 격차 = **sourcing 4.3**이 최저 차원으로 교대 (S each):
- N-PORT 지수펀드 보유(2/5 rele2)·13F 거장(3/5) — 표는 맞으나 질문의 특정 관점(비중 상위
  변화 등) 재진술이 없음 → 합성 시 질문 리프레이즈 선행 문장.
- 매크로 원문(sour2~3: Core CPI·BLS 페이지) — 수치는 맞고 원문 링크도 있으나 judge가 기관명
  명시를 요구 → 합성 인용 라벨에 기관명(BLS·BEA·ECOS) 포함.
- 컨센서스 연도별 breakdown(2/5) — FMP 응답에 2030뿐인 해를 '자료에 없음'으로 명시(완결성
  원칙 재적용 확인).
- ~~Yahoo KR 503 재발 시 KIS 폴백~~ → ✅ IMP-15로 데이터 플레인에서 해결 (라우팅 힌트보다 확실).
궤적: 3.73 → 3.91 → 4.09/3.92(노이즈 밴드) → 4.11(투표 후) → **4.31**.

## 14. Non-goals (unchanged)

No forecasts, price targets, momentum scores, or advice — in any milestone, including
History Lab (that's the point). No non-Gemini models. No keyword routers. No client-side
keys. No fabricated gaps: pre-1997 KOSPI, missing transcripts, and un-ingested eras are
**drawn as gaps** with an explanation chip.
