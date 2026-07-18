# deploy/ — GCP 단일 VM 배포 자산

캐노니컬 문서: [`docs/INFRA.md`](../docs/INFRA.md) (아키텍처·비용·런북·백업/복구). 여기는 퀵스타트만.

```bash
# 1) 워크스테이션: GCP 리소스 생성 (idempotent — 이미 있으면 전부 스킵, 중복 생성 0)
PROJECT=<gcp-project> DOMAIN=<your.domain> ./deploy/provision.sh
#    VM·고정IP·방화벽·백업 버킷(30d)·스냅샷(7d)·Artifact Registry(최근 10버전 클린업)
#    스타터(16GB, −$44/mo): MACHINE=e2-standard-4 (+기동 시 VG_MEM_* 프로필 — INFRA §2)
#    → 출력된 IP를 Cloudflare A 레코드로 (프록시 ☁, SSL "Full (strict)") + Origin CA 인증서 발급

# 2) 개발 머신: 이미지 빌드 → Artifact Registry 푸시 (태그 = git SHA)
PROJECT=<gcp-project> NEXT_PUBLIC_TOSS_CLIENT_KEY=<key> ./deploy/build-push.sh

# 3) VM 부트스트랩 1회 (docker·스왑·repo·env+.env 스캐폴드·AR 인증·백업 타이머)
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap
./deploy/vm-setup.sh    # → 체크리스트대로 .env(DOMAIN·레지스트리)·env/*.env·secrets/ 채우기

# 4) 배포·업데이트·롤백 = 전부 한 명령 (무중단 롤링: pull → 서비스별 헬스 게이트 스왑)
./deploy/deploy.sh <태그>        # 업데이트 (AR pull, 수십 초)
./deploy/deploy.sh <이전 태그>   # 롤백 (재빌드 없음 — 이전 이미지 그대로)

# VM 사이즈 변경 (16GB ↔ 32GB, 다운타임 ~3-5분 1회 — 새벽에)
PROJECT=<gcp-project> ./deploy/resize-vm.sh e2-highmem-4

# admin 콘솔 (루프백 전용): IAP 터널
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap -- -L 8005:127.0.0.1:8005
```

| 파일 | 역할 |
|---|---|
| `provision.sh` | GCP 리소스 생성 — 전부 `describe \|\| create` 가드 (재실행 안전) |
| `build-push.sh` | 개발 머신에서 이미지 빌드 → AR 푸시 (:git-sha + :latest) |
| `deploy.sh` | 무중단 롤링 업데이트/롤백 — AR pull(기본) 또는 VM 빌드(폴백) → 서비스별 `--wait` → SSE 드레인 → Caddy 갭 흡수 |
| `resize-vm.sh` | VM 머신타입 변경 (stop→set→start, 데이터·IP 보존) |
| `vm-setup.sh` | VM 1회 부트스트랩 |
| `Caddyfile` | TLS 종단(Origin CA) + SSE flush + 배포 갭 lb 재시도 |
| `backup.sh` + `systemd/` | 야간 pg_dumpall → GCS (03:30 KST, GCS 30일 보존) |
| `../docker-compose.prod.yml` | prod 오버라이드: caddy·AR 이미지명(`VG_REGISTRY_PREFIX`/`VG_TAG`)·mem_limit(`VG_MEM_*`)·전 포트 루프백·worker 헬스체크·SSE 드레인 grace |

**비용 무증식**: AR 클린업(최근 10버전·30일), GCS 라이프사이클(30일), 스냅샷 보존(7일),
`deploy.sh`의 로컬 이미지 prune, 로그 캡(10MB×5) — 상한 없는 성장은 pg 데이터뿐(INFRA §8).
