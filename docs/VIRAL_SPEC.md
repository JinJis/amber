# VIRAL SPEC — SNS 바이럴 파이프라인 (M-VIRAL)

> Companion to [`ROADMAP.md`](./ROADMAP.md) · [`PUBLISH_SPEC.md`](./PUBLISH_SPEC.md).
> 2026-07-10 갭 분석에서 나온 실행 계획. 목표는 하나 — **공유된 링크 하나하나가
> "받은 짤"을 이기는 영수증이 되어 가입을 끌고 오는 것.** 인바리언트(CLAUDE §2)는 불변:
> 모든 수치는 출처와 함께, 전망 없음, 날조 없음. 바이럴은 신뢰의 배포 채널이지 예외가 아니다.

---

## 0. 컨셉 — 왜 이 순서인가

한국 주식 커뮤니티(정보방·Threads·X)의 화폐는 **주장 붙은 차트 짤**이다. 지금 그 짤은
출처가 없다. 우리는 반대로 간다: **모든 공유물에 출처·기준일·검증이 박혀 있고, 링크를
열면 근거까지 검증 가능**하다. 그래서 전략은 3단:

1. **공유 루프 완성** (V-1~V-6) — 링크가 피드에서 눈에 띄고(차트 OG), 한 탭에 공유되고
   (네이티브 셰어), 열면 빠르고(캐시), 얼마나 퍼졌는지 보인다(측정). *외부 키 불필요, 순수 코드.*
2. **짤 공장 가동** (V-7~V-9) — 공유할 "물건"의 화력: 답변이 훅을 뽑고, 어닝 서프라이즈
   카드(API Ninjas로 해금됨)와 온디맨드 공시 근거가 콘텐츠 품질을 끌어올린다.
3. **킬러 화면** (M2 히스토리 랩 — 별도 스펙) — THEN|NOW 비교 화면 자체가 데모이자 짤 공장.

각 태스크는 독립 PR(one task per PR), 아래 순서가 의존 순서다.

---

## 1. API Ninjas 프리미엄 — 확보한 무기 (2026-07-10 라이브 검증)

| 엔드포인트 | 검증 결과 | 우리 활용 |
|---|---|---|
| `/v1/earningstranscript` | ✅ US+**KR**(005930.KS, 영어 진행), 화자턴 텍스트, `date` 포함, Developer 티어 ~5년(2021 ✓ / 2016 ✗) | **완료** — 트랜스크립트 1순위 소스 + KR 파이프라인 활성화 (커밋 d9a4353) |
| `/v1/logo` | ✅ 티커 직조회, KR 코드(.KS) 포함, 깨끗한 PNG | **완료** — 로고 해석기 2순위 소스로 삽입 |
| `/v1/earningscalendar?ticker=` | ✅ **actual/estimated revenue·EPS + 차이·%까지 계산돼 옴** (컨센서스 대비 서프라이즈!) | **V-8 어닝 서프라이즈 카드의 데이터 원천** — FMP 플랜 제한 우회 |
| `/v1/marketcap` | ✅ 시총+통화+갱신시각 | 보조(스냅샷 보강; 우선순위 낮음) |
| `/v1/sec?ticker=&filing=` | 파라미터 필요 (10-K 등) — SEC 직접 수집이 이미 있어 중복 | 스킵 |
| dividends / ipo / insidertrading | ❌ 미존재(404) | — |

**의도**: 유료 키 하나로 (a) 어닝콜 전문 US+KR (b) 로고 (c) 서프라이즈 데이터 3개 축을
확보 — 어닝 시즌 콘텐츠(2단계)의 재료가 전부 갖춰졌다.

---

## 2. V-1 · 차트가 박힌 서버사이드 OG 이미지 ⭐ 최우선

**컨셉**: 링크 미리보기가 곧 광고다. 지금 OG는 텍스트 카드 + 클라이언트 업로드(타이밍
레이스). 이를 **서버가 만드는, 실제 차트가 그려진 1200×630 카드**로 바꾼다.
**목적**: 피드 스크롤을 멈추는 건 차트다. 크롤러가 언제 와도 이미지가 이미 존재해야 한다.

단계:
1. **한글 폰트 번들**: `web/assets/fonts/`에 Pretendard(또는 Noto Sans KR) Regular/Bold
   TTF subset 추가(용량 ~1MB 이하로 서브셋). 라이선스 명시(SIL OFL).
2. **OG 라우트**: `web/app/og/s/[token]/route.tsx` — Next `ImageResponse`(@vercel/og, edge
   아님 node 런타임)로 1200×630 JSX 렌더:
   - 좌: 제목(≤3줄, 볼드) + 리드 2줄 + 푸터(출처 칩·as_of·짧은 링크)
   - 우: **차트 미니 렌더** — TradeChart는 canvas라 서버 불가 → 아티팩트의
     `series/candles`를 **순수 SVG polyline/area로 직접 그림**(새 유틸
     `web/lib/ogChart.tsx`: min/max 정규화 → 640×400 viewBox path). 캔들은 종가 라인으로
     단순화(OG는 실루엣이 목적). 데이터 없으면 수치 테이블 3행.
   - `⏳ 과거 기록 · 전망 아님` 라벨은 히스토리 kind에 무조건.
3. **셰어 페이지 메타 교체**: `/s/[token]/page.tsx`의 og:image →
   `/og/s/{token}` (studio의 저장 이미지 폴백 유지). ShareSheet의 클라이언트 OG 업로드
   경로 **제거**(레이스 소멸, 코드 삭제).
4. **캐시**: OG 라우트에 `Cache-Control: public, s-maxage=86400` — 스냅샷 불변이므로 안전.
5. **테스트**: ogChart 순수 함수(정규화·path 생성·빈 데이터 폴백) vitest; OG 라우트는
   토큰 fetch 목으로 200+image/png 확인.

**DoD**: 카톡/텔레그램에 링크 붙이면 즉시(업로드 대기 없이) 차트 카드 언펄. 회귀:
studio GET /shares/{token}/image 경로는 유지(구 링크 호환).

## 3. V-2 · 네이티브 공유 + 공유 UX 마감

**컨셉**: 바이럴은 마찰 게임. 폰에서 "공유 → 시트 → 링크복사 → 앱 전환" 3~4탭을
**1탭**으로.
1. ShareSheet에 `navigator.share` 지원 감지 → 모바일이면 최상단에 큰
   **"공유하기"** 버튼(`share({title, text: 리드 80자, url})`). 미지원(데스크톱)은 현행 유지.
2. 답변 상단에도 진입점: 답변이 화면 밖으로 길면 푸터 공유를 놓침 → 아티클 우상단에
   작은 ↗ 아이콘(스크롤 위치 무관 접근).
3. 공유 시트에 **미리보기 한 줄**(제목+출처 N곳)을 보여줘 "무엇이 공유되는지" 확신을 줌.
4. 테스트: navigator.share 목 주입 → 호출 파라미터 검증; 미지원 폴백 렌더.

## 4. V-3 · 공유 페이지 캐시/성능

**컨셉**: 터진 링크는 초당 수십 뷰 — 매 요청 studio 왕복은 자폭이다. 스냅샷은 불변.
1. `/s/[token]/page.tsx`: `force-dynamic` 제거 → `fetch(..., { next: { revalidate: 3600 }})`
   (Next data cache). revoke 반영은 1시간 지연 허용(정책 명시) — 또는 revoke 시
   studio가 web에 `revalidateTag` 웹훅(후속).
2. studio `GET /shares/{token}` 응답에 `Cache-Control: public, max-age=300` 추가.
3. 부하 스모크: `ab -n 500 -c 50` 로컬 확인(문서화만, CI 제외).

## 5. V-4 · 공유 조회 측정 (그로스의 눈)

**컨셉**: 뭐가 터졌는지 모르면 다음 짤을 못 만든다. 최소·프라이버시 안전 카운터.
1. studio `ShareLink`에 `views: int default 0` 컬럼(additive).
2. 공개 read(`GET /shares/{token}`) 시 `views += 1` (best-effort UPDATE, 실패 무시).
   서버사이드 렌더 캐시(V-3) 때문에 페이지뷰≠read 호출 → **비콘 방식 병행**:
   공개 페이지 클라이언트에서 `navigator.sendBeacon("/api/shares/{token}/view")` →
   studio `POST /shares/{token}/view` (무인증, 토큰만; rate-limit 1/ip/min은 스킵 — dev).
3. "내 공유" 목록(`GET /shares`)에 views 노출 + 어드민 /costs 옆 `/shares` 운영 페이지에
   상위 공유 랭킹(조회수 desc).
4. 테스트: view 비콘 → views 증가; revoked엔 미증가.

## 6. V-5 · 공유 페이지 전환 루프

**컨셉**: 지금은 막다른 골목. 읽고 감탄한 사람이 **다음 행동**을 하게 만든다.
1. **이어 묻기 칩**: answer 스냅샷에 이미 `suggestions`가 있으면 페이로드에 포함
   (ShareSheet answer payload에 `suggestions` 추가) → 공개 페이지 하단
   "이 질문에서 이어가기" 칩 3개 → 클릭 시 `/?q={질문}` 딥링크 → 로그인 후 컴포저
   프리필(Chat이 `?q` 쿼리 파라미터 소비, 온보딩 후에도 유지).
2. **갱신 유도**: 스냅샷 as_of가 7일 이상 과거면 "지금 데이터로 다시 보기 →" CTA
   (같은 딥링크, q=원 질문).
3. **소셜 프루프**: views ≥ 50이면 "👀 N명이 봤어요" (허수 없음 — 실측만).
4. 테스트: suggestions 렌더·딥링크 쿼리 소비(컴포저 프리필)·오래된 as_of CTA.

## 7. V-6 · 카카오톡 리치 공유 (국내 본진)

**컨셉**: 카톡 언펄은 OG만으로도 되지만, **Kakao JS SDK 템플릿**은 버튼("근거 보기")까지
박힌 카드를 보낸다. *유저 액션 필요: developers.kakao.com 앱 생성 → JavaScript 키.*
1. env `NEXT_PUBLIC_KAKAO_JS_KEY` (публishable). 미설정 → 현행 링크복사 유지(자동 게이트).
2. ShareSheet: SDK lazy-load(`https://t1.kakaocdn.net/kakao_js_sdk/.../kakao.min.js`,
   integrity 고정) → `Kakao.Share.sendDefault({objectType:'feed', content:{title, description:
   리드, imageUrl: OG url, link}, buttons:[{title:'근거까지 보기', link}]})`.
3. CSP/도메인: 카카오 개발자 콘솔에 사이트 도메인 등록 필요(문서화).
4. 테스트: SDK 목 → sendDefault 파라미터 스냅샷.

## 8. V-7 · 훅 산출 (공유 제목의 품질)

**컨셉**: 지금 OG 제목 = 유저 질문("삼성전자 어때?"). 바이럴 제목은 **발견**이다
("삼성전자 영업이익, 컨센서스 +12% 상회"). LLM이 이미 답을 아니 한 줄 더 뽑게 한다.
1. agent-engine 합성 프롬프트에 산출 필드 추가: 답변 마지막에
   `<<HOOK: 한 줄>>` 마커(또는 done 이벤트 별도 필드가 깔끔 — `hook`) — 수치는 반드시
   본문에 있는(=audit 통과한) 수치만 재사용, 새 숫자 금지(QT-2가 어차피 잡음).
2. done 이벤트에 `hook` 탑재 → Msg에 저장 → ShareSheet answer payload의 title로 사용
   (없으면 현행 질문 폴백). OG 제목도 자동 개선.
3. eval 시나리오 +1: hook이 본문 수치와 일치하고 전망 문구가 없는지 judge.
4. 테스트: done hook 파싱·폴백; audit에 hook 숫자 포함 검증(rigged draft).

## 9. V-8 · 어닝 서프라이즈 카드 (EC-2 — API Ninjas로 해금)

**컨셉**: 어닝 시즌 = SNS 금융 트래픽 피크. `earningscalendar`가 **추정·실제·차이·%**를
그대로 주므로 "비트/미스" 아티팩트를 데이터플레인 정석 코스로 만든다.
**의도**: 분기마다 자동으로 공유각 콘텐츠가 쏟아지는 파이프.

단계:
1. **datasets 프로바이더** `providers/us/api_ninjas.py`: `earnings_calendar(ticker)` →
   rows[{date, actual_eps, estimated_eps, eps_surprise_pct, actual_revenue, …}]. (검증된
   스키마; KR 티커도 .KS로 시도 — 커버리지는 응답으로 판단, 날조 금지.)
2. **라우터+매니페스트**: `datasets/app/routers/earnings_surprise.py` →
   `GET /earnings/surprise?market=&ticker=` — 최근 8분기 표준화(발표일·EPS 추정/실제/서프%
   ·매출 추정/실제/서프%) + `source="API Ninjas (earnings calendar)"` + as_of. 커넥터
   `api_ninjas` 신설(도메인 earnings, cost low), `_CATEGORY`에 기존 '실적' 카테고리로 등록
   (무등록 시 로드 실패 — 인테그리티 테스트 green 유지). coverage.sh가 자동 포섭.
3. **아티팩트**: agent-engine `_artifacts`에 surprise 도구 매핑 — kind `compare`(기존):
   분기 x축, 추정 vs 실제 두 시리즈 + 표. 프런트 신규 렌더러 불필요(기존 compare/table).
   훅 예: "최근 8분기 중 7번 컨센서스 상회".
4. **플래너 티칭**: planner 도구 설명에 "어닝 서프라이즈/비트미스 질문 → earnings_surprise"
   + `_CAPABILITY_MENU`에 칩 1개("어닝 서프라이즈 히스토리 보기").
5. **테스트**: 프로바이더 respx(정상·빈배열·비커버 티커) · 라우터 표준화 · 카탈로그
   인테그리티 · eval 시나리오 +1("TSLA 어닝 서프라이즈 히스토리" → compare 아티팩트+인용).

## 10. V-9 · 온디맨드 공시 인제스트 (EV-PASSAGE 마감)

**컨셉**: EV-PASSAGE(본문 하이라이트)는 RAG에 그 공시가 있어야 작동. 최신 공시는 자주
없다 → **미스 시 그 자리에서 그 accession 하나만 인제스트**하고 재시도.
1. datasets `POST /filings/ingest-one {market, ticker, accession}` (ungoverned 아님 —
   기존 filing_search 온디맨드 패턴 재사용 검토: `filing_search_ingest_limit` 경로가 이미
   유사 동작. 있으면 재사용, 없으면 filing_ingest의 단일 accession 함수 노출).
2. agent-engine passages.py: accession 매칭 실패 시 위 엔드포인트 호출(게이트웨이 경유,
   3s 캡) → 성공하면 rag__search 1회 재시도. 전체 예산 1턴 +4s 상한.
3. 테스트: 미스→인제스트→히트 목 시나리오; 인제스트 실패 시 기존 제목 유지.

## 11. 잔여(이번 스펙 범위 밖, 순서만 고정)

- **M2 히스토리 랩 화면** — HL-10~14 (HISTORY_LAB_SPEC). 짤 공장 본체. V-1~8 후 착수.
- 다크모드(공유 페이지 우선) · 공유 페이지 차트 인터랙션 · KR 뉴스 아카이브(BigKinds
  결정 필요) · Logo.dev 토큰(유저 발급) · 실결제.

## 12. 마일스톤 요약

| id | 이름 | 크기 | 외부 의존 |
|---|---|---|---|
| V-1 | 차트 박힌 서버사이드 OG | M | 폰트 파일만 |
| V-2 | 네이티브 공유 + UX | S | — |
| V-3 | 공유 페이지 캐시 | S | — |
| V-4 | 조회 측정 | S | — |
| V-5 | 전환 루프(이어 묻기·딥링크) | M | — |
| V-6 | 카카오 리치 공유 | S | **Kakao JS 키(유저)** |
| V-7 | 훅 산출 | S | — |
| V-8 | 어닝 서프라이즈 카드 | M | API Ninjas ✅ |
| V-9 | 온디맨드 공시 인제스트 | S | — |

권장 착수: **V-1 → V-8 → V-2/V-3/V-4(묶음) → V-7 → V-5 → V-9 → V-6**.
(V-8을 앞당기는 이유: 어닝 시즌 타이밍 + 데이터가 이미 검증됨.)
