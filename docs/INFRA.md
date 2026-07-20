# INFRA — GCP 배포·운영 (단일 GCE VM, asia-northeast3 서울)

> **캐노니컬 인프라 문서.** 배포 자산은 [`deploy/`](../deploy/)와
> [`docker-compose.prod.yml`](../docker-compose.prod.yml). 비용은 §6 (2026-07-17 조사).
> 결정 요약: **베타 ~수십 명 · VM 1대 self-host Postgres + GCS 백업 · Cloudflare 무료 프록시 →
> VM Caddy · 관리형 DB는 성장 경로(§8)**. 프로비저닝은 오너가 직접 실행한다(코드만 커밋).

## 1. 아키텍처

```
                         ┌────────────────────────── GCE VM: vg-beta (e2-highmem-4, 32GB) ──────────────────────────┐
 사용자 ──▶ Cloudflare ──▶│ :443 Caddy ──▶ web:3000 ──▶ studio-api:8004 ──▶ agent-engine:8003 ──▶ gateway:8001      │
       (무료: TLS/CDN/    │  (Origin CA,                                                    │                        │
        DDoS, Full-strict)│   SSE flush)                                          datasets:8000 ─── rag:8002        │
                         │                                                              │              │            │
                         │                       worker (Procrastinate 크론 스윕) ──────┴── postgres ◀─┘            │
                         │                                                            (pgvector:pg16, pg_data)      │
                         │  admin 127.0.0.1:8005  ← IAP SSH 터널로만                                                │
                         └──────────────────────────────────────────────────────────────────────────────────────────┘
                              │ 야간 03:30 KST pg_dumpall → GCS(30d 라이프사이클)   │ 03:00 KST 디스크 스냅샷(7d)
                              ▼                                                    ▼
                          gs://<project>-vg-backups                          스냅샷 체인 (증분)
```

- **왜 VM 1대인가**: ① SSE 장수명 스트리밍(턴 ~45s+, 최대 120s)이 핵심이라 scale-to-zero 서버리스와
  안 맞음 ② Yahoo/SEC 등 업스트림이 **소스 IP 기준** 레이트리밋 → 고정 이그레스 IP 1개가 오히려 이상적
  ③ Postgres가 HNSW 8.4GB 인덱스를 페이지캐시에 올려야 해서(16GB+ 호스트 전제 튜닝) 어차피 큰 상주
  메모리 필요 ④ compose 스택을 무변경으로 그대로 운영 — 이식 비용 0.
- **Cloudflare 무료**: TLS 종단·정적자산 CDN 캐시·DDoS. 유명한 100초 제한(524)은 **바이트 간**
  타임아웃이라, 턴 스트림이 진행 이벤트를 계속 흘리는 우리 SSE는 안전. 모드는 **Full (strict)** +
  VM엔 Cloudflare **Origin CA** 인증서(무료·15년·갱신 불필요).
- **redis 불필요**: `scale` 프로필은 >1 replica 전용(크로스 노드 공유 상태). 단일 노드는 그대로 정합.
- **이미지 = Artifact Registry**(서울 `vg` 레포): 개발 머신에서 `deploy/build-push.sh`로 빌드·푸시
  (태그=커밋 SHA + latest), VM은 **pull만** — 빌드 CPU 스파이크 없음, 배포 수십 초, **롤백 = 이전
  태그 pull**(재빌드 없이 동일 바이트). 동일 리전 pull 이그레스 무료, 스토리지는 클린업 정책(최근
  10버전·30일)으로 ~2–4GB 상한. VM 로컬 빌드는 폴백 모드(`VG_REGISTRY_PREFIX=` 비우면)로 유지.

## 2. GCP 리소스 인벤토리 (이름 = `deploy/provision.sh`와 일치)

| 리소스 | 이름 | 스펙 |
|---|---|---|
| VM | `vg-beta` | e2-highmem-4 (4 vCPU/32GB), Ubuntu 24.04 LTS, OS Login, 태그 `vg-web` |
| 부트 디스크 | `vg-beta` | 200GB pd-balanced (현 데이터 ~52GB + 성장 40–60GB/yr → ~2년 헤드룸, 온라인 리사이즈 가능) |
| 고정 IP | `vg-beta-ip` | 리전 외부 IPv4 — Cloudflare A 레코드 대상이자 고정 이그레스 IP |
| 방화벽 | `vg-allow-http-https` | tcp:80,443 + udp:443(HTTP/3) → 태그 `vg-web` (강화판: Cloudflare IP 대역만) |
| 방화벽 | `vg-allow-iap-ssh` | tcp:22, 소스 35.235.240.0/20 (IAP)만 — 공인 SSH 차단 |
| 서비스계정 | `vg-vm@…` | 백업 버킷 `roles/storage.objectAdmin`만. 앱의 GCP 인증(DocAI·Vertex Ranking)은 기존 `secrets/gcp-sa.json` 그대로 |
| GCS 버킷 | `gs://<project>-vg-backups` | 서울 리전, uniform access, PAP, 라이프사이클: 7d→Nearline, 30d 삭제 |
| 스냅샷 스케줄 | `vg-daily-snap` | 매일 03:00 KST, 7일 보존, 부트 디스크에 부착 |
| Artifact Registry | `vg` (docker, 서울) | 서비스 이미지 저장소 — **클린업 정책: 최근 10버전 유지·30일 후 삭제**(스토리지 무한 증식 방지). VM SA에 reader만 |

**자원 무증식 보장**: `provision.sh`는 전 자원이 `describe || create` 가드라 **몇 번을 돌려도 이미
있으면 스킵**(중복 생성 0). 계속 쌓이는 것들은 전부 상한이 있음 — AR 이미지(최근 10버전),
GCS 백업(30일 라이프사이클), 스냅샷(7일 보존), VM 로컬 이미지(`deploy.sh`가 매 배포 prune),
컨테이너 로그(10MB×5 캡). 시간이 지나며 자연 증가하는 건 **pg 디스크 사용량뿐**(+40–60GB/yr,
§8 트리거로 대응).

**VM 메모리 예산 (32GB 기본)**: postgres 14g(shared_buffers 2g + HNSW 8.4g 페이지캐시) · worker 3g ·
rag 2.5g · datasets 2g · agent-engine 1.5g · web 1.5g · control-plane/studio-api/admin 각 1g ·
caddy 0.5g ≈ 29.5g + OS ~2g, 스파이크는 8GB 스왑. `docker-compose.prod.yml`의 `mem_limit`와 동일.

**스타터 티어 (첫 유저·테스트용, −$44/mo)**: `MACHINE=e2-standard-4`(4vCPU/16GB, $125.51)로
프로비저닝하고 스타터 메모리 프로필을 셸/루트 `.env`로 지정:
```bash
VG_MEM_PG=8g VG_MEM_WORKER=2g VG_MEM_RAG=1.5g VG_MEM_DATASETS=1.5g   # + DOMAIN=…
```
트레이드오프: HNSW 인덱스(8.4GB)를 페이지캐시에 다 못 올려 **콜드 벡터 검색이 느려짐**(측정: 콜드
6.2s/웜 10ms) — 유저 수십 명 전이면 체감 OK. 대량 인제스트(주간 filing sweep)가 느려지면 업그레이드
신호. **업그레이드는 ~3–5분 다운타임 1회** (데이터·디스크·고정IP 전부 보존, 새벽에 실행):
```bash
gcloud compute instances stop vg-beta --zone asia-northeast3-a
gcloud compute instances set-machine-type vg-beta --zone asia-northeast3-a --machine-type e2-highmem-4
gcloud compute instances start vg-beta --zone asia-northeast3-a
# VM에서: VG_MEM_* 변수 제거(32GB 기본값 복귀) 후 DOMAIN=… ./deploy/deploy.sh
```
CUD는 **업그레이드가 끝나 사이즈가 확정된 뒤** 걸 것 (약정은 머신 리소스 양 기준).

## 3. 보안

- **인그레스**: 80/443만 월드 오픈(옵션: Cloudflare IP 대역으로 축소), SSH는 IAP 터널만. prod
  오버라이드가 모든 API 포트(3000/8000/8002/8003/8004/8010/8005)를 **127.0.0.1로 강제 재바인딩** —
  외부에 열리는 건 Caddy뿐 (방화벽 + 심층 방어 이중).
- **admin(:8005)**: 전 서비스 DB CRUD 콘솔 — prod 오버라이드가 ADMIN_BIND와 무관하게 루프백 강제.
  접근: `gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap -- -L 8005:127.0.0.1:8005`
  → 브라우저 `http://localhost:8005`. (대안: Cloudflare Access — 별도 서브도메인+정책. 베타엔 터널 권장.)
- **시크릿**: `env/*.env` + `secrets/`(SA JSON·Origin CA 인증서)는 VM 파일시스템에만(gitignored).
  `ENV=production`이면 dev 기본값(AUTH_SECRET/SERVICE_TOKEN/ADMIN_TOKEN/ADMINUI_*/GUEST_IP_SALT/
  BILLING_ENC_KEY/`rag:rag` pg 비밀번호)으로 **부팅 거부**(SC-0) — 전부 채워야 뜬다.
- **빌드 타임 주의**: `NEXT_PUBLIC_TOSS_CLIENT_KEY`는 `npm run build`에 인라인 — 셸/루트 `.env`로
  compose에 넘겨야 함(`env/*.env`는 compose 보간에 안 읽힘). `DOMAIN`도 동일.

## 4. 런북

**A. 프로비저닝 (워크스테이션, 1회)**
```bash
PROJECT=<gcp-project> DOMAIN=<your.domain> ./deploy/provision.sh
# 출력된 IP로 Cloudflare DNS: A <domain> → <IP>, 프록시 ☁ ON, SSL 모드 "Full (strict)"
# Cloudflare → SSL/TLS → Origin Server → Create Certificate → origin.pem/origin-key.pem 저장
```

**B. 이미지 빌드·푸시 (개발 머신, 매 배포)**
```bash
PROJECT=<gcp-project> NEXT_PUBLIC_TOSS_CLIENT_KEY=<key> ./deploy/build-push.sh
# → AR에 :<git-sha> + :latest 푸시, 배포 명령 출력
```

**C. VM 부트스트랩 (VM 위, 1회) + 첫 기동**
```bash
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap
./deploy/vm-setup.sh        # docker·스왑·KST·unattended-upgrades·repo clone·env+.env 스캐폴드·AR 인증·백업 타이머
# 체크리스트대로 root .env(DOMAIN·VG_REGISTRY_PREFIX)·env/*.env·secrets/ 채우기 (§3 시크릿)
./deploy/deploy.sh <git-sha>   # AR pull + 기동 (첫 실행 = 그냥 전체 기동)
```

**D. 검증 체크리스트**
- `docker compose ps` — 전 서비스 healthy (worker 포함 — prod 오버라이드에 헬스체크 있음)
- `curl -s https://<domain>/` 200 (Cloudflare 경유)
- 채팅 1턴 end-to-end: SSE 스트림이 중도 524 없이 완료되는지 (브라우저 DevTools에서 event-stream 확인)
- 백업 1회 수동 실행: `sudo systemctl start valuegraph-backup.service` → `journalctl -u valuegraph-backup -n 20`
  → GCS에 객체 확인
- **복원 드릴** (월 1회 권장): 스크래치 컨테이너에 최신 덤프 복원
  ```bash
  gcloud storage cp "$(gcloud storage ls gs://<bucket>/pg/ | tail -1)" /tmp/restore.sql.gz
  docker run -d --name pg-drill -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16
  gunzip -c /tmp/restore.sql.gz | docker exec -i pg-drill psql -U postgres
  docker exec pg-drill psql -U postgres -d studio -c "select count(*) from users;"   # 샘플 검증
  docker rm -f pg-drill
  ```
- admin: IAP 터널로만 접속되는지(공인 IP:8005 접속 불가 확인)

**E. 업데이트 / 롤백 — 무중단 롤링 배포 (`deploy/deploy.sh`)**
```bash
# 개발 머신: PROJECT=… ./deploy/build-push.sh   → 태그 출력
# VM에서 (DOMAIN·VG_REGISTRY_PREFIX는 root .env에 있음):
./deploy/deploy.sh <새 태그>       # 업데이트 — AR pull(수십 초) + 롤링 스왑
./deploy/deploy.sh <이전 태그>     # 롤백 — 재빌드 없이 이전 이미지 그대로 (동일 바이트)
# 태그 목록: gcloud artifacts docker tags list <prefix>/valuegraph-web
```
무중단 동작 원리 (단일 VM에서):
1. **pull 먼저** — 새 이미지를 받는 동안(수십 초; 폴백=VM 빌드 수 분) 구 스택이 계속 서빙
2. **한 서비스씩 헬스 게이트 재기동** (`up -d --no-deps --wait`, 의존 순서: datasets→rag→gateway→
   agent→studio→worker→admin→web) — 변경 없는 서비스는 no-op
3. **인플라이트 SSE 드레인** — studio-api·agent-engine `stop_grace_period: 45s`: 진행 중인 턴(~45s)이
   끝날 때까지 구 컨테이너가 기다림. 45s 넘는 턴은 끊기지만 SC-2.3이 쿼터를 자동 환불.
   (주의: grace는 컨테이너 **생성 시점**에 박히므로, 이 설정 이전에 뜬 스택의 첫 배포 1회는
   기본 10s로 드레인 — 두 번째 배포부터 45s 적용)
4. **web 스왑 갭(~2–5s) 흡수** — Caddy `lb_try_duration 30s`: 스왑 중 새 요청은 502 대신 대기·재시도
5. **postgres는 절대 재기동 안 함** — pg 이미지 업그레이드만 수동 점검창에서
한계(정직하게): 완전한 blue-green이 아니라 "실패 요청 0 + 수 초 지연" 수준. 진짜 무중단(구/신 동시
서빙)은 §8의 앱/DB 분리 후 가능. **DB까지 롤백**해야 하면 §4-D 복원 절차를 본 postgres에 적용.

> ⚠️ **프로드 VM에서 `scripts/test_all.sh`·`e2e*.sh`·`coverage.sh` 절대 실행 금지** — 이 하니스들은
> 라이브 스택을 내렸다 올립니다(과거 `down -v` 사고 전례). 테스트·eval은 개발 환경(회사 GCE)에서만.

## 5. 백업 전략 (CR-11 해소)

| 계층 | 무엇 | 주기·보존 | 복구 시나리오 |
|---|---|---|---|
| `deploy/backup.sh` (systemd 타이머) | `pg_dumpall`(전 논리 DB — 결제 원장·RAG 코퍼스 포함) → gzip → GCS | 매일 03:30 KST · GCS 30일(7일 후 Nearline) | DB 논리 복원, 특정 시점(일 단위) |
| 디스크 스냅샷 스케줄 | 부트 디스크 전체(볼륨·env·secrets 포함) | 매일 03:00 KST · 7일 | VM 통째 복구/리전 내 재생성 |
| `datasets_data` | 로고·evidence HTML **캐시** — 재생성 가능 | 스냅샷에 포함되면 충분 | 유실 시 자동 재수집(단, OpenDART 쿼터 소모) |

RPO ≈ 24h(덤프 기준). 결제가 본격화되면 §8의 Cloud SQL(PITR)로 이전해 RPO를 분 단위로.

## 6. 예상 비용 (월, USD — 2026-07-17 웹 조사, 서울 asia-northeast3)

| 항목 | 베타(온디맨드) | 베타(1-yr CUD) | 근거 |
|---|---|---|---|
| VM e2-highmem-4 (4vCPU/32GB) | $169.18 | $106.59 | gcloud-compute.com 2026-07-12 스크레이프 |
| ↳ 스타터: e2-standard-4 (16GB) | $125.51 | $79.07 | 첫 유저용 — §2 스타터 프로필, 합계 ~$170–202 |
| 디스크 200GB pd-balanced | $26.00 | $26.00 | $0.13/GB·mo |
| 고정 외부 IP | $3.65 | $3.65 | $0.005/hr (사용 중에도 과금) |
| 디스크 스냅샷 (~70GB 증분 체인) | ~$3.50 | ~$3.50 | ~$0.05/GB·mo |
| Artifact Registry (~2–4GB, 클린업 정책 상한) | ~$0.2–0.4 | 〃 | $0.10/GB·mo, 0.5GB 무료; 동일리전 pull 무료 |
| GCS 백업 (~7GB/일 × 30일 ≈ 210GB) | ~$4.85 | ~$4.85 | ~$0.023/GB·mo (7일후 Nearline로 실제론 더 낮음) |
| 인터넷 이그레스 (50–200GB) | $6–38 | $6–38 | $0.12/GB(아시아 발신), 한국 착신 $0.19/GB |
| **핵심 인프라 합계** | **~$213–245** | **~$150–183** | 예산 $150–250 내 |
| (옵션) Vertex AI Ranking (~4.5k 쿼리) | ~$4.50 | 〃 | $1.00/1k 쿼리 |
| (옵션) Document AI Layout Parser | 사용량 | 〃 | $10/1k 페이지 (덱 파싱 에피소딕) |

**예산 초과 트리거**: e2-standard-8 온디맨드($251 단독 초과) · pd-ssd(+$18) · 이그레스 >250GB/월
(완화: Cloudflare가 `/_next/static/*` 캐시) · DocAI 대량 인제스트 달.

**Gemini 토큰 (별도, 지배적 변동비)**: 턴당 ~9–10콜(intake·plan·synthesis·팔로업…), 토큰 ~50–80k in
/ 6–8k out → **턴당 $0.04–0.10**(2.5-flash 티어 기준; 3.x 티어로 앨리어스가 풀리면 $0.15–0.30).
베타 100–200턴/일 ≈ **$150–900/mo**. 절감 레버: ① `AGENT_MODEL` 명시 핀(자동 티어 상승 방지)
② context caching(캐시된 입력 최대 90%↓) ③ `PLANS_JSON` 일일 턴 캡(이미 지원) ④ admin Costs
대시보드로 실측 추적(`admin/adminpanel/pricing.py`가 단가 원장).

## 7. 운영 메모

- **로그**: 컨테이너당 json-file 10MB×5 캡(기본 compose에 설정됨). 중앙 수집은 성장 경로.
- **모니터링(베타 최소)**: `docker compose ps`의 healthy 상태 + GCP 기본 VM 메트릭(CPU/디스크).
  Cloud Monitoring 에이전트는 무료 티어 내 — 원하면 vm-setup에서 ops-agent 설치.
- **이미지 빌드는 개발 머신에서** (`deploy/build-push.sh` → AR push). VM 로컬 빌드는 폴백 모드만 — 프로드 VM의 빌드 CPU 스파이크 방지.
- **시간대**: VM은 KST(vm-setup) — 백업 타이머·KIS/OpenDART KST-자정 쿼터 리셋과 일치.
- **worker 일시정지**: `docker compose stop worker` — 전 자동 인제스트 중단(기존 운영 관행 동일).

## 8. 성장 경로 (트리거 기반)

| 트리거 | 액션 | 추가 비용 |
|---|---|---|
| 스타터(16GB)에서 벡터 검색/인제스트 체감 저하 | `set-machine-type`으로 e2-highmem-4 (§2 — 다운타임 ~3–5분 1회, 새벽에) | +$44/mo |
| 베타 1개월 안정 (사이즈 확정 후) | VM 1-yr CUD 전환 | −$63/mo |
| 유료 고객 발생 (결제 원장 RPO 분 단위 필요) | controlplane+studio DB만 **Cloud SQL**(PITR)로 분리 — `DATABASE_URL` 스왑만 | +$60–100/mo |
| rag >60GB / 인제스트가 VM 포화 | rag DB를 **AlloyDB**(`alloydb_scann` — self-host HNSW 인서트 병목 제거)로 — `RAG_DATABASE_URL` 스왑만 (USER_TODO §1-10) | +$200+/mo |
| 동시 턴 증가로 단일 노드 한계 | `--profile scale` redis + `REDIS_URL` + 앱 노드 분리 + LB(SSE 재개 경로 sticky — SC-3.3 잔여) | +VM/LB |
| 배포 자동화 필요 | build-push.sh를 Cloud Build/GH Actions CI로 이관 (AR는 이미 기본) | ~$0–10/mo |
| 복원 드릴 실패/디스크 성장 | 디스크 리사이즈(온라인) 또는 관리형 DB 조기 이전 | 상황별 |

관련(모두 `docs/deprecate/`로 이관됨 — 참고용, 링크 없음): SCALING_AUDIT(열린 tail: CR-8/CR-11·HI-12·ME-13·ME-6),
USER_TODO §1(오너 액션), QUALITY_SPEC §D(QG-14 PG 동시성).
