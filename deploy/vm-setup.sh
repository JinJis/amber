#!/usr/bin/env bash
# One-time VM bootstrap — run ON the freshly provisioned GCE VM (docs/INFRA.md):
#
#   curl -fsSL https://raw.githubusercontent.com/JinJis/value-graph/development/deploy/vm-setup.sh | bash
#   # or clone first and run ./deploy/vm-setup.sh
#
# Installs docker + compose, an 8GB swapfile, unattended security upgrades, KST timezone,
# clones the repo to /opt/value-graph, scaffolds env/*.env from the examples, installs the
# nightly-backup systemd timer, and prints the go-live checklist. Safe to re-run.
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:JinJis/value-graph.git}"
BRANCH="${BRANCH:-development}"
APP_DIR="${APP_DIR:-/opt/value-graph}"

echo "── timezone (KST — backup timer + KIS/OpenDART KST-midnight quotas assume it)"
sudo timedatectl set-timezone Asia/Seoul

echo "── docker CE + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "${USER}"
  echo "   (log out/in once for the docker group to apply)"
fi

echo "── 8GB swapfile (absorbs pg maintenance_work_mem spikes) + swappiness=10"
if ! sudo swapon --show | grep -q '/swapfile'; then
  sudo fallocate -l 8G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-vg-swappiness.conf >/dev/null
sudo sysctl -p /etc/sysctl.d/99-vg-swappiness.conf >/dev/null

echo "── unattended security upgrades"
sudo apt-get update -qq && sudo apt-get install -y -qq unattended-upgrades >/dev/null
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "── repo → ${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
  sudo mkdir -p "${APP_DIR}" && sudo chown "${USER}:${USER}" "${APP_DIR}"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${APP_DIR}"
fi
cd "${APP_DIR}"

echo "── env scaffold (fill these before first boot — SC-0 refuses dev defaults)"
for f in env/*.env.example; do
  target="${f%.example}"
  [ -f "${target}" ] || cp "${f}" "${target}"
done
mkdir -p secrets/origin-cert

echo "── root .env (compose \${...} interpolation — DOMAIN·registry·mem profile live here)"
if [ ! -f .env ]; then
  PROJECT_ID="$(curl -s -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/project/project-id 2>/dev/null || echo '<project>')"
  cat > .env <<ENVEOF
# compose interpolation vars (NOT container env — that's env/*.env). deploy.sh reads this too.
DOMAIN=CHANGE_ME.example.com
VG_REGISTRY_PREFIX=asia-northeast3-docker.pkg.dev/${PROJECT_ID}/vg/
# NEXT_PUBLIC_TOSS_CLIENT_KEY=   # only needed when building on the VM (fallback mode)
# 16GB 스타터 프로필이면 주석 해제 (docs/INFRA.md §2):
# VG_MEM_PG=8g
# VG_MEM_WORKER=2g
# VG_MEM_RAG=1.5g
# VG_MEM_DATASETS=1.5g
ENVEOF
fi

echo "── Artifact Registry pull auth (VM SA has roles/artifactregistry.reader)"
gcloud auth configure-docker asia-northeast3-docker.pkg.dev --quiet || \
  echo "   ⚠ gcloud not ready — run 'gcloud auth configure-docker asia-northeast3-docker.pkg.dev' later"

echo "── nightly backup timer"
sudo cp deploy/systemd/valuegraph-backup.service /etc/systemd/system/
sudo cp deploy/systemd/valuegraph-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable valuegraph-backup.timer
echo "   ⚠ edit /etc/systemd/system/valuegraph-backup.service → BACKUP_BUCKET, then:"
echo "     sudo systemctl restart valuegraph-backup.timer"

cat <<'CHECKLIST'

✅ VM ready. Go-live checklist (docs/INFRA.md runbook §4):
  [ ] .env                — DOMAIN + VG_REGISTRY_PREFIX (프로젝트 자동 감지됨 — 확인만)
  [ ] env/app.env         — ENV=production, PUBLIC_BASE_URL=https://<domain>
  [ ] env/gemini.env      — GOOGLE_API_KEY
  [ ] env/data-keys.env   — OPENDART/ECOS/FRED/FMP/API_NINJAS/KIS/KRX…
  [ ] env/auth-billing.env — AUTH_GOOGLE_*, RESEND, TOSS keys
  [ ] env/secrets.env     — AUTH_SECRET/SERVICE_TOKEN/ADMIN_TOKEN/ADMINUI_*/GUEST_IP_SALT/BILLING_ENC_KEY
                            (openssl rand -base64 32 each) + non-default POSTGRES password
  [ ] secrets/gcp-sa.json — Document AI + Vertex Ranking SA (optional features)
  [ ] secrets/origin-cert/origin.pem + origin-key.pem — Cloudflare Origin CA cert
  [ ] /etc/systemd/system/valuegraph-backup.service — BACKUP_BUCKET
  then (개발 머신에서 이미지를 push해뒀다면):
      ./deploy/deploy.sh <image-tag>             # AR에서 pull + 롤링 기동
  verify:
      curl -s https://<domain>/ | head -1        # via Cloudflare
      docker compose ps                          # all healthy
      sudo systemctl start valuegraph-backup.service && journalctl -u valuegraph-backup -n 20
CHECKLIST
