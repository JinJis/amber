# deploy/ — GCP 단일 VM 배포 자산

캐노니컬 문서: [`docs/INFRA.md`](../docs/INFRA.md) (아키텍처·비용·런북·백업/복구). 여기는 퀵스타트만.

```bash
# 1) 워크스테이션에서 GCP 리소스 생성 (VM·IP·방화벽·백업 버킷·스냅샷 스케줄)
PROJECT=<gcp-project> DOMAIN=<your.domain> ./deploy/provision.sh
#    → 출력된 IP를 Cloudflare A 레코드로 (프록시 ☁ ON, SSL "Full (strict)")
#    → Cloudflare Origin CA 인증서 발급 → secrets/origin-cert/{origin.pem,origin-key.pem}

# 2) VM 부트스트랩 (docker·스왑·repo·env 스캐폴드·백업 타이머)
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap
./deploy/vm-setup.sh
#    → 체크리스트대로 env/*.env + secrets/ 채우기 (ENV=production은 dev 기본값 부팅 거부)

# 3) 기동
cd /opt/value-graph
DOMAIN=<domain> NEXT_PUBLIC_TOSS_CLIENT_KEY=<key> \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# admin 콘솔 (루프백 전용): IAP 터널
gcloud compute ssh vg-beta --zone asia-northeast3-a --tunnel-through-iap -- -L 8005:127.0.0.1:8005
```

| 파일 | 역할 |
|---|---|
| `provision.sh` | GCP 리소스 생성 (idempotent, 워크스테이션에서) |
| `vm-setup.sh` | VM 1회 부트스트랩 |
| `Caddyfile` | TLS 종단(Origin CA) + SSE flush 리버스 프록시 |
| `backup.sh` + `systemd/` | 야간 pg_dumpall → GCS (03:30 KST, GCS 30일 보존) |
| `../docker-compose.prod.yml` | prod 오버라이드: caddy·mem_limit·전 포트 루프백·worker 헬스체크 |
