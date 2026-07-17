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
- [ ] **OpenDART 키 여러 개 등록**: `OPENDART_API_KEYS=key1,key2,key3` (쉼표 구분, `.env`) —
  하루 사용한도(020)가 소진된 키는 KST 자정 리셋까지 자동으로 쉬고 다음 키로 로테이션돼요.
  계정별로 키를 발급받아 넣어주시면 돼요 ([opendart.fss.or.kr](https://opendart.fss.or.kr) →
  인증키 신청). 사용현황도 같은 포털에서 확인 가능
- [ ] **재빌드 + 재기동 필요**: 이번 픽스는 `docker-compose.yml`(worker에 `datasets_data:/data`
  볼륨 추가)을 바꿨어요 — worker 컨테이너를 **recreate**해야 인제스트 시 공시 HTML 캐시가
  datasets와 공유돼요 (`docker compose up -d --build datasets worker agent-engine rag studio-api web admin`)
- [x] ~~전체 유니버스 재실행 시 전량 재수집~~ → **델타 인제스트 랜딩(OPS-2)**: 어드민 백필 폼의
  '수집 방식'에서 델타(기본)/전체를 고를 수 있고, 크론 스윕은 델타로 돌아요. 쿼터 잔여량은
  어드민 Pipelines의 OpenDART 카드에서 키별로 보여요
- [ ] (로컬 스택만) **Vertex 리랭커 프로젝트 설정**: rag 로그에 `RESOURCE_PROJECT_INVALID`가 찍히면
  GCP 프로젝트/SA env가 memory의 구성(chungjin-456905, value-graph@ SA)과 다른 것 — `.env`의
  RAG_GCP_* 값을 확인하세요 (검색은 fail-safe로 동작하지만 리랭킹 품질이 빠져요)

## 1-10. RAG 인제스트 성능 (2026-07-12 ING-1)
- [ ] **재빌드 + 재기동 필요**: `docker-compose.yml`이 바뀌었어요 — postgres에 메모리 튜닝
  (`shared_buffers=2GB` 등) 추가. `docker compose up -d --build` 후 postgres가 recreate돼야
  대형 공시 인제스트의 HNSW 삽입 병목이 풀려요 (호스트 RAM 16GB+ 기준; 더 작으면 값 하향)
- [ ] **(프로덕션 권장) 벡터 저장소 = AlloyDB + pgvector**: 코드 변경 0(`RAG_DATABASE_URL`만
  교체), `alloydb_scann` 인덱스가 self-host HNSW 삽입 병목을 관리형으로 제거해요. Cloud SQL+
  pgvector도 가능하지만 인덱스가 동일(HNSW)이라 삽입 속도 이득은 적어요. Vertex AI Vector
  Search는 수백만 벡터·엄격 지연 SLA일 때만 (신규 백엔드 구현 필요)

## 1-11. 스케일링 감사에서 나온 오너 액션 (2026-07-12, [SCALING_AUDIT](./SCALING_AUDIT.md))
- [ ] **DB 백업 지금 즉시** (CR-11 — 현재 백업 전무, 디스크 1개 유실 = 테넌트·키·빌링 원장 전체 유실):
  최소한 서버에 야간 `pg_dump` 크론 + 오프호스트(GCS 등) 복사. 예를 들어 크론에
  `docker exec valuegraph-platform-postgres-1 pg_dumpall -U rag | gzip > /backups/pg_$(date +\%F).sql.gz`
  를 걸고 `gsutil cp`로 GCS에 올리면 돼요. 프로덕션은 controlplane·studio(빌링)부터 PITR 있는 매니지드
  DB로, rag는 AlloyDB로 분리(1-10). rag 코퍼스는 재생성되지만 재임베딩 비용이 드니 스냅샷도 같이
- [x] **admin(:8005) 크레덴셜·노출** (CR-10): 코드 가드 랜딩됨(SC-0.2 — 로그인 레이트리밋·IP 허용목록,
  기본 호스트 바인드가 loopback으로). **남은 오너 액션**: `ADMINUI_USERNAME/PASSWORD/SECRET`를 강한
  값으로 설정. 외부에서 접근해야 하면 `ADMIN_BIND`/`ADMINUI_IP_ALLOWLIST`로만 열고 공개 `0.0.0.0`은 금지
- [x] **프로덕션 시크릿 전수 교체** (CR-10): 이제 `ENV=production`이면 dev 기본값으로 기동을 **거부**해요
  (SC-0.1) — 배포 전 실값으로 안 바꾸면 스택이 아예 안 떠요. 바꿀 목록: `AUTH_SECRET`·`SERVICE_TOKEN`·
  `ADMIN_TOKEN`·`ADMINUI_*`·`DATASETS_API_KEYS`(+`AUTH_DISABLED=false`)·기본 pg 비번(`rag:rag`)·
  (게스트 켜면)`GUEST_IP_SALT`·(토스 켜면)`BILLING_ENC_KEY`
- [x] **레플리카 증설**: SC-2 완료(2026-07-17 기준) — 중복 결제/알림 중복/챗 재개는 크로스노드 잠금·
  단일비행으로 해소돼 studio-api·gateway 다중 레플리카가 가능해졌어요. 다만 챗 재개(SSE tail)는 아직
  세션 스티키 라우팅이 필요하니 로드밸런서에서 그 경로만 고정해 주세요(SC-3.3 잔여).

## 1-12. GCP 배포 (2026-07-17, [INFRA](./INFRA.md) — 배포 자산은 `deploy/`에 커밋됨)
배포 코드는 전부 랜딩(§CR-11 백업 포함) — 아래는 오너가 직접 실행할 것들. 순서는 INFRA §4 런북.
- [ ] **도메인 → Cloudflare**: 네임서버 이전(무료 플랜), SSL 모드 **Full (strict)**, 프록시 ☁ ON
- [ ] **Origin CA 인증서 발급**: Cloudflare → SSL/TLS → Origin Server → Create Certificate →
  VM의 `secrets/origin-cert/{origin.pem,origin-key.pem}`
- [ ] **`PROJECT=… DOMAIN=… ./deploy/provision.sh` 실행** (VM·고정IP·방화벽·백업 버킷·스냅샷 스케줄)
  → 출력된 IP를 Cloudflare A 레코드로. **첫 유저·테스트 단계면 `MACHINE=e2-standard-4`(16GB, −$44/mo)
  스타터로 시작** — 벡터 검색이 느려지면 `set-machine-type`으로 32GB 승격(~3–5분 다운타임 1회, INFRA §2)
- [ ] **업데이트는 항상 `DOMAIN=… ./deploy/deploy.sh`** (무중단 롤링 — 빌드 먼저·서비스별 헬스 게이트·
  SSE 드레인·Caddy 갭 흡수). ⚠️ 프로드 VM에서 test_all/e2e/coverage 스크립트 실행 금지(스택을 내림)
- [ ] **VM에서 `./deploy/vm-setup.sh`** → 체크리스트대로 `env/*.env`·`secrets/` 채우기
  (`ENV=production`은 dev 기본값 부팅 거부 — 1-2의 시크릿 목록 전부)
- [ ] **backup 버킷 설정**: `/etc/systemd/system/valuegraph-backup.service`의 `BACKUP_BUCKET` 교체
  → 수동 1회 실행으로 GCS 업로드 확인 → **월 1회 복원 드릴** (INFRA §4-D)
- [ ] **비용**: 베타 ~$213–245/mo (온디맨드) — 1개월 안정 후 **1-yr CUD 전환**(~$150–183)
  + Gemini 토큰 별도(~$150–900/mo, admin Costs로 실측)

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
