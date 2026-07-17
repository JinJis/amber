#!/usr/bin/env bash
# Nightly Postgres backup → GCS (CR-11). Runs on the VM via systemd timer (deploy/systemd/).
#
#   BACKUP_BUCKET=gs://vg-beta-backups ./deploy/backup.sh
#
# Dumps ALL logical DBs (rag/datasets/controlplane/studio — includes the billing ledger and the
# 20GB RAG corpus) with pg_dumpall from inside the postgres container, gzips, sanity-checks the
# size, uploads to GCS (same-region transfer is free), and prunes local copies older than 2 days.
# GCS-side retention is the bucket's 30-day lifecycle rule (deploy/provision.sh), not this script.
set -euo pipefail

BACKUP_BUCKET="${BACKUP_BUCKET:?set BACKUP_BUCKET=gs://your-backup-bucket}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-valuegraph-platform}"
PG_CONTAINER="${PG_CONTAINER:-${COMPOSE_PROJECT}-postgres-1}"
PG_USER="${PG_USER:-rag}"
LOCAL_DIR="${LOCAL_DIR:-/var/backups/valuegraph}"
MIN_BYTES="${MIN_BYTES:-1000000}"   # <1MB = something is wrong (schema alone exceeds this)

STAMP="$(date +%F_%H%M)"
OUT="${LOCAL_DIR}/pg_${STAMP}.sql.gz"

mkdir -p "${LOCAL_DIR}"

echo "[backup] dumping ${PG_CONTAINER} → ${OUT}"
docker exec "${PG_CONTAINER}" pg_dumpall -U "${PG_USER}" --clean --if-exists | gzip > "${OUT}"

SIZE="$(stat -c%s "${OUT}")"
if [ "${SIZE}" -lt "${MIN_BYTES}" ]; then
  echo "[backup] FAIL: dump is only ${SIZE} bytes (<${MIN_BYTES}) — refusing to upload" >&2
  exit 1
fi
echo "[backup] dump OK ($(numfmt --to=iec "${SIZE}" 2>/dev/null || echo "${SIZE}B"))"

echo "[backup] uploading to ${BACKUP_BUCKET}/pg/"
gcloud storage cp "${OUT}" "${BACKUP_BUCKET}/pg/" --quiet

# local prune: keep 2 days (GCS keeps 30 via lifecycle)
find "${LOCAL_DIR}" -name 'pg_*.sql.gz' -mtime +2 -delete

echo "[backup] done: ${BACKUP_BUCKET}/pg/$(basename "${OUT}")"
