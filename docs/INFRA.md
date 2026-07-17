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

**VM 메모리 예산 (32GB)**: postgres 14g(shared_buffers 2g + HNSW 8.4g 페이지캐시) · worker 3g ·
rag 2.5g · datasets 2g · agent-engine 1.5g · web 1.5g · control-plane/studio-api/admin 각 1g ·
caddy 0.5g ≈ 29.5g + OS ~2g, 스파이크는 8GB 스왑. `docker-compose.prod.yml`의 `mem_limit`와 동일.
e2-standard-4(16GB)로 내리면 ING-1 pg 튜닝을 풀어야 해서 콜드 벡터 인서트 ~2s/row로 회귀 — 비추천.

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

**B. VM 부트스트랩 (VM 위, 1회)**
```bash
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap
./deploy/vm-setup.sh        # docker·스왑·KST·unattended-upgrades·repo clone·env 스캐폴드·백업 타이머
# 체크리스트대로 env/*.env·secrets/ 채우기 (§3 시크릿)
```

**C. 배포/기동**
```bash
cd /opt/value-graph
DOMAIN=<domain> NEXT_PUBLIC_TOSS_CLIENT_KEY=<key> \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
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

**E. 업데이트 / 롤백**
```bash
cd /opt/value-graph && git pull
DOMAIN=<domain> docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build   # 업데이트
git checkout <이전 커밋/태그> && DOMAIN=<domain> docker compose ... up -d --build               # 롤백
# DB까지 롤백해야 하면: 위 복원 드릴 절차를 본 postgres 컨테이너에 적용 (down → pg_data 비우고 복원 → up)
```

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
| 디스크 200GB pd-balanced | $26.00 | $26.00 | $0.13/GB·mo |
| 고정 외부 IP | $3.65 | $3.65 | $0.005/hr (사용 중에도 과금) |
| 디스크 스냅샷 (~70GB 증분 체인) | ~$3.50 | ~$3.50 | ~$0.05/GB·mo |
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
- **이미지 빌드**: VM 위 `compose build`(레지스트리 비용 0). 빌드 중 CPU 스파이크는 베타에서 허용.
- **시간대**: VM은 KST(vm-setup) — 백업 타이머·KIS/OpenDART KST-자정 쿼터 리셋과 일치.
- **worker 일시정지**: `docker compose stop worker` — 전 자동 인제스트 중단(기존 운영 관행 동일).

## 8. 성장 경로 (트리거 기반)

| 트리거 | 액션 | 추가 비용 |
|---|---|---|
| 베타 1개월 안정 | VM 1-yr CUD 전환 | −$63/mo |
| 유료 고객 발생 (결제 원장 RPO 분 단위 필요) | controlplane+studio DB만 **Cloud SQL**(PITR)로 분리 — `DATABASE_URL` 스왑만 | +$60–100/mo |
| rag >60GB / 인제스트가 VM 포화 | rag DB를 **AlloyDB**(`alloydb_scann` — self-host HNSW 인서트 병목 제거)로 — `RAG_DATABASE_URL` 스왑만 (USER_TODO §1-10) | +$200+/mo |
| 동시 턴 증가로 단일 노드 한계 | `--profile scale` redis + `REDIS_URL` + 앱 노드 분리 + LB(SSE 재개 경로 sticky — SC-3.3 잔여) | +VM/LB |
| 배포 자동화 필요 | Artifact Registry(+$1/mo) + Cloud Build/GH Actions CI | ~$0–10/mo |
| 복원 드릴 실패/디스크 성장 | 디스크 리사이즈(온라인) 또는 관리형 DB 조기 이전 | 상황별 |

관련: [SCALING_AUDIT](./SCALING_AUDIT.md)(열린 tail: CR-8/CR-11·HI-12·ME-13·ME-6),
[USER_TODO](./USER_TODO.md) §1(오너 액션), [QUALITY_SPEC](./QUALITY_SPEC.md) §D(QG-14 PG 동시성).
