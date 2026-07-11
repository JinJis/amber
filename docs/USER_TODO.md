# USER TODO — 오너가 직접 해야 하는 것들 (2026-07-11)

> 코드는 전부 랜딩됐고(M-PROD ✅, ROADMAP §12e) 아래는 **콘솔 가입·키 발급·계약** 등
> 코드 밖 작업입니다. 완료하면 체크하고, 키는 `.env`에만 넣으세요(커밋 금지).
> 각 항목의 env 이름은 `.env.example`에 문서화되어 있습니다.

---

## 1. 프로덕션 런칭 필수 (M-PROD 활성화)

### 1-1. 인증 (소셜 로그인)
- [ ] **구글 OAuth**: [console.cloud.google.com](https://console.cloud.google.com) → OAuth 동의화면 구성(외부) → 사용자 인증 정보 → OAuth 클라이언트 ID(웹)
  - 리디렉션 URI: `{origin}/api/auth/callback/google`
  - → `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`
- [ ] **카카오 로그인**: [developers.kakao.com](https://developers.kakao.com) → 앱 생성 → 카카오 로그인 ON + Redirect URI `{origin}/api/auth/callback/kakao` → 보안 탭에서 Client Secret 발급
  - → `AUTH_KAKAO_ID`, `AUTH_KAKAO_SECRET`
  - [ ] **비즈앱 전환 심사** 신청(이메일 제공 동의항목) — 심사 전에도 로그인은 되지만 이메일 없이 센티널 계정으로 들어와요(유저가 설정에서 이메일 연결 가능)
- [ ] **이메일 OTP 발송 (Resend)**: [resend.com](https://resend.com) 가입 → 도메인 추가 + DNS(SPF/DKIM) 인증 → API 키
  - → `RESEND_API_KEY`, `EMAIL_FROM=ValueGraph <login@도메인>`
  - 키 없으면 dev 모드(코드가 studio-api 로그로만 남음) — 프로덕션에선 필수

### 1-2. 프로덕션 시크릿 (미설정 시 서비스가 기동 거부)
- [ ] `ENV=production` 설정
- [ ] `AUTH_SECRET` — `openssl rand -base64 32`
- [ ] `SERVICE_TOKEN` — 랜덤 문자열 (dev 기본값이면 studio-api 기동 거부)
- [ ] `ADMIN_TOKEN` — 랜덤 문자열 (dev 기본값이면 control-plane/studio 기동 거부)
- [ ] `GUEST_IP_SALT` — 랜덤 문자열 (게스트 IP 해시 솔트)
- [ ] `PUBLIC_BASE_URL` — 실제 서비스 도메인 (공유 링크·추천 링크가 이걸 씀)

### 1-3. 결제 (토스페이먼츠)
- [ ] **가맹 계약**: [tosspayments.com](https://www.tosspayments.com) 가맹 신청 → 자동결제(빌링) 상품 활성화
  - → `TOSS_SECRET_KEY`, `NEXT_PUBLIC_TOSS_CLIENT_KEY`
- [ ] `BILLING_ENC_KEY` 생성 (빌링키 암호화 — production에서 토스 키가 있으면 필수):
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- [ ] `TOSS_WEBHOOK_PATH_SECRET` — 랜덤 문자열 생성 후 **토스 개발자센터 → 웹훅**에
  `{origin}/api/billing/webhook/{그 값}` 등록 (결제 상태 변경 이벤트)
- [ ] `BILLING_ENABLED=true` (시간별 정기결제·던닝 스케줄러 시작)
- [ ] (권장) 토스 **테스트 키**로 먼저 검증: 카드 등록 → 첫 결제 → Pro 플립 → 설정에서 해지 예약

### 1-4. 퍼널 켜기 + 반영
- [ ] `FEATURE_GUEST=true` — 비로그인 게스트 체험(공유→suggestion→즉시 채팅) 활성화
- [ ] **전 서비스 재빌드**: `docker compose up -d --build` (web·studio-api·agent-engine·control-plane·rag·admin — M-PROD 전체 반영; DB 마이그레이션은 기동 시 자동)
- [ ] 배포 후 스모크: 비로그인 접속→게스트 3턴→가입 월→가입→대화 승계 확인 · admin `/billing`·`/costs` 열림 확인

### 1-5. 런칭 후 4주 (가격 확정)
- [ ] admin `/costs`의 **유저별 LLM 원가** 관찰 → `PLANS_JSON`·`PLAN_PRICE_PRO_KRW` 조정
  (현재 가설: Free 5턴/일·80/월 무료, Pro ₩19,900/월 200턴 fair-use — 플랜 문서 §3 참고)
- [ ] `PRICING_JSON`을 Gemini 최신 정가로 갱신 (admin /costs 요율표가 이걸 읽음)

---

## 1-9. 근거 뷰어 관련 (2026-07-11 evidence-pipeline fix에서 확인됨)
- [ ] **OpenDART 사용한도**: 전체 유니버스 파이프라인이 일일 한도(무료 키 2만 건)를 소진하면
  그날의 KR 공시 뷰어(문서 fetch)가 전부 죽어요(지금은 캐시 공유로 대부분 흡수되지만, 새 공시는
  여전히 라이브 fetch). [opendart.fss.or.kr](https://opendart.fss.or.kr) → 사용현황 확인,
  한도 상향 신청 또는 인제스트용/서빙용 키 분리 검토
- [ ] **재빌드 + 재기동 필요**: 이번 픽스는 `docker-compose.yml`(worker에 `datasets_data:/data`
  볼륨 추가)을 바꿨어요 — worker 컨테이너를 **recreate**해야 인제스트 시 공시 HTML 캐시가
  datasets와 공유돼요 (`docker compose up -d --build datasets worker agent-engine rag studio-api web`)
- [ ] (로컬 스택만) **Vertex 리랭커 프로젝트 설정**: rag 로그에 `RESOURCE_PROJECT_INVALID`가 찍히면
  GCP 프로젝트/SA env가 memory의 구성(chungjin-456905, value-graph@ SA)과 다른 것 — `.env`의
  RAG_GCP_* 값을 확인하세요 (검색은 fail-safe로 동작하지만 리랭킹 품질이 빠져요)

## 2. 바이럴·품질 (선택이지만 효과 큼)
- [ ] **Kakao JS 키** (V-6 카톡 리치 공유): developers.kakao.com 같은 앱 → JavaScript 키 + Web 플랫폼 도메인 등록 → `NEXT_PUBLIC_KAKAO_JS_KEY` (키가 오면 V-6 구현 착수 가능)
- [ ] **RAG 코퍼스 재인제스트** (RQ-9 — 검색 품질 최대 지렛대): 유니버스 재인제스트 + transcript US/KR + era news
- [ ] 어드민에서 로고 채우기 · `LOGODEV_TOKEN`(선택, 로고 화질)

## 3. 나중에 / 검토
- [ ] `FIXED_COSTS_JSON` — API Ninjas·FMP 등 고정 구독 선언 (admin /costs 합산 정확화)
- [ ] NYT_API_KEY(era news) · BigKinds(KR 뉴스 아카이브) 검토 · API Ninjas Business(어닝콜 2005~)
- [ ] 글로벌 확장 시 Paddle(MoR) 병행 검토 (Stripe는 한국 법인 머천트 불가)

---

**이미 확보됨**: GOOGLE_API_KEY · FMP · API Ninjas 프리미엄 · KIS · KRX 계정 · OPENDART/ECOS/FRED · GCP(Vertex 리랭커 SA)

**완료 시**: 이 문서의 체크박스를 갱신하고, 키를 `.env`에 넣은 뒤 해당 서비스만 재빌드하면 돼요.
