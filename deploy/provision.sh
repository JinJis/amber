#!/usr/bin/env bash
# One-time GCP provisioning for the single-VM beta deployment (docs/INFRA.md).
# Run from YOUR workstation (needs gcloud auth with project owner/editor):
#
#   PROJECT=my-project DOMAIN=desk.example.com ./deploy/provision.sh
#
# Idempotent-ish: every resource is `describe || create`-guarded, safe to re-run.
# Creates: APIs · VM service account (backup-bucket-only) · static IP · firewall (80/443 world,
# SSH via IAP only) · e2-highmem-4 VM (200GB pd-balanced, Ubuntu 24.04, OS Login) · GCS backup
# bucket (30-day lifecycle) · daily disk-snapshot schedule (7-day retention).
set -euo pipefail

# ── config ───────────────────────────────────────────────────────────────────
PROJECT="${PROJECT:?set PROJECT=your-gcp-project-id}"
REGION="${REGION:-asia-northeast3}"           # Seoul
ZONE="${ZONE:-asia-northeast3-a}"
VM="${VM:-vg-beta}"
MACHINE="${MACHINE:-e2-highmem-4}"            # 4 vCPU / 32GB — see docs/INFRA.md sizing
DISK_GB="${DISK_GB:-200}"
BUCKET="${BUCKET:-gs://${PROJECT}-vg-backups}"
SA_NAME="${SA_NAME:-vg-vm}"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT}" >/dev/null

echo "── APIs"
gcloud services enable compute.googleapis.com storage.googleapis.com iap.googleapis.com

echo "── service account (backup bucket only — app-level GCP auth stays in secrets/gcp-sa.json)"
gcloud iam service-accounts describe "${SA}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" --display-name="ValueGraph VM (backups)"

echo "── static external IP"
gcloud compute addresses describe "${VM}-ip" --region "${REGION}" >/dev/null 2>&1 || \
  gcloud compute addresses create "${VM}-ip" --region "${REGION}"
IP="$(gcloud compute addresses describe "${VM}-ip" --region "${REGION}" --format='value(address)')"

echo "── firewall: 80/443 to tagged VMs; SSH only via IAP range; everything else default-deny"
gcloud compute firewall-rules describe vg-allow-http-https >/dev/null 2>&1 || \
  gcloud compute firewall-rules create vg-allow-http-https \
    --direction=INGRESS --action=ALLOW --rules=tcp:80,tcp:443,udp:443 \
    --source-ranges=0.0.0.0/0 --target-tags=vg-web
    # Tighter variant: replace 0.0.0.0/0 with Cloudflare's published IP ranges
    # (https://www.cloudflare.com/ips/) so only the CDN can reach the origin.
gcloud compute firewall-rules describe vg-allow-iap-ssh >/dev/null 2>&1 || \
  gcloud compute firewall-rules create vg-allow-iap-ssh \
    --direction=INGRESS --action=ALLOW --rules=tcp:22 \
    --source-ranges=35.235.240.0/20 --target-tags=vg-web

echo "── VM ${VM} (${MACHINE}, ${DISK_GB}GB pd-balanced, Ubuntu 24.04 LTS)"
gcloud compute instances describe "${VM}" --zone "${ZONE}" >/dev/null 2>&1 || \
  gcloud compute instances create "${VM}" \
    --zone "${ZONE}" \
    --machine-type "${MACHINE}" \
    --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
    --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-balanced \
    --address "${VM}-ip" \
    --service-account "${SA}" \
    --scopes cloud-platform \
    --tags vg-web \
    --metadata enable-oslogin=TRUE

echo "── GCS backup bucket ${BUCKET} (uniform access, public-access prevention, 30d lifecycle)"
if ! gcloud storage buckets describe "${BUCKET}" >/dev/null 2>&1; then
  gcloud storage buckets create "${BUCKET}" \
    --location "${REGION}" --uniform-bucket-level-access --public-access-prevention
fi
LIFECYCLE="$(mktemp)"
cat > "${LIFECYCLE}" <<'JSON'
{"rule": [
  {"action": {"type": "Delete"}, "condition": {"age": 30}},
  {"action": {"type": "SetStorageClass", "storageClass": "NEARLINE"}, "condition": {"age": 7}}
]}
JSON
gcloud storage buckets update "${BUCKET}" --lifecycle-file="${LIFECYCLE}"
rm -f "${LIFECYCLE}"
gcloud storage buckets add-iam-policy-binding "${BUCKET}" \
  --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin" >/dev/null

echo "── daily disk-snapshot schedule (03:00 KST = 18:00 UTC, keep 7 days)"
gcloud compute resource-policies describe vg-daily-snap --region "${REGION}" >/dev/null 2>&1 || \
  gcloud compute resource-policies create snapshot-schedule vg-daily-snap \
    --region "${REGION}" --max-retention-days=7 \
    --daily-schedule --start-time=18:00 \
    --on-source-disk-delete=apply-retention-policy
gcloud compute disks describe "${VM}" --zone "${ZONE}" \
    --format='value(resourcePolicies)' | grep -q vg-daily-snap || \
  gcloud compute disks add-resource-policies "${VM}" --zone "${ZONE}" \
    --resource-policies=vg-daily-snap

cat <<DONE

✅ Provisioned. Next steps (docs/INFRA.md runbook):
  1. Point Cloudflare DNS: A ${DOMAIN:-your.domain} → ${IP}  (proxied ☁, SSL mode "Full (strict)")
  2. SSH in:   gcloud compute ssh ${VM} --zone ${ZONE} --tunnel-through-iap
  3. On the VM: run deploy/vm-setup.sh (installs docker, swap, clones the repo, scaffolds env/)
  4. Fill env/*.env + secrets/ (SC-0 refuses dev defaults), then:
       DOMAIN=${DOMAIN:-your.domain} docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
  Backup bucket: ${BUCKET}   (set it in deploy/systemd/valuegraph-backup.service)
DONE
