#!/usr/bin/env bash
# VM 사이즈 변경 (스타터 16GB ↔ 32GB 등) — run from your workstation.
# GCE는 라이브 리사이즈가 없어 stop→set-machine-type→start (~3-5분 다운타임 1회).
# 디스크·고정IP·데이터 전부 보존. 새벽 등 한산한 시간에 실행할 것.
#
#   PROJECT=<proj> ./deploy/resize-vm.sh e2-highmem-4     # 승격
#   PROJECT=<proj> ./deploy/resize-vm.sh e2-standard-4    # 축소 (32GB 메모리 프로필이면 VG_MEM_* 필요)
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=your-gcp-project-id}"
ZONE="${ZONE:-asia-northeast3-a}"
VM="${VM:-vg-beta}"
MACHINE="${1:?usage: resize-vm.sh <machine-type e.g. e2-highmem-4>}"

gcloud config set project "${PROJECT}" >/dev/null
CURRENT="$(gcloud compute instances describe "${VM}" --zone "${ZONE}" --format='value(machineType.basename())')"
if [ "${CURRENT}" = "${MACHINE}" ]; then
  echo "already ${MACHINE} — nothing to do"; exit 0
fi

echo "── ${VM}: ${CURRENT} → ${MACHINE} (downtime ~3-5 min starts NOW)"
gcloud compute instances stop "${VM}" --zone "${ZONE}"
gcloud compute instances set-machine-type "${VM}" --zone "${ZONE}" --machine-type "${MACHINE}"
gcloud compute instances start "${VM}" --zone "${ZONE}"

cat <<DONE
✅ resized to ${MACHINE}. 컨테이너는 restart:unless-stopped로 자동 기동됩니다.
메모리 프로필 확인 (VM의 root .env — docs/INFRA.md §2):
  - e2-highmem-4(32GB): VG_MEM_* 줄 주석 처리(기본값 복귀)
  - e2-standard-4(16GB): VG_MEM_PG=8g VG_MEM_WORKER=2g VG_MEM_RAG=1.5g VG_MEM_DATASETS=1.5g
바꿨다면 VM에서:  ./deploy/deploy.sh   (동일 태그 재적용 — mem_limit만 갱신됨)
CUD 약정은 사이즈가 확정된 뒤에 걸 것.
DONE
