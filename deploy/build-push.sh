#!/usr/bin/env bash
# Build all service images and push them to Artifact Registry — run on the DEV machine
# (or CI), NOT on the prod VM. The VM then only PULLS (fast deploys, no build CPU spike,
# exact rollback by tag).
#
#   PROJECT=<gcp-project> ./deploy/build-push.sh            # tag = current git short SHA
#   PROJECT=<gcp-project> VG_TAG=v1.2.3 ./deploy/build-push.sh
#
# Pushes each image twice: :<tag> (immutable, for deploy/rollback) and :latest (convenience).
# Registry storage stays bounded by the repo cleanup policy set in provision.sh
# (keep 10 most recent versions, delete >30d).
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT="${PROJECT:?set PROJECT=your-gcp-project-id}"
REGION="${REGION:-asia-northeast3}"
AR_REPO="${AR_REPO:-vg}"
TAG="${VG_TAG:-$(git rev-parse --short HEAD)}"
PREFIX="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}"
SERVICES=(datasets control-plane rag agent-engine studio-api web admin)

if [ -n "$(git status --porcelain)" ]; then
  echo "⚠ working tree is dirty — the image tag ${TAG} won't match the code exactly" >&2
fi

# NEXT_PUBLIC_* is inlined into the web bundle at `npm run build` — an unset key silently
# ships a keyless Toss billing panel (compose emits no warning because of the :- default).
if [ -z "${NEXT_PUBLIC_TOSS_CLIENT_KEY:-}" ] \
    && ! grep -q '^NEXT_PUBLIC_TOSS_CLIENT_KEY=..*' .env 2>/dev/null; then
  if [ -z "${ALLOW_KEYLESS_TOSS:-}" ]; then
    echo "✋ NEXT_PUBLIC_TOSS_CLIENT_KEY is not set (shell or root .env) — the web image would" >&2
    echo "   ship a keyless Toss billing panel. Export it, or ALLOW_KEYLESS_TOSS=1 to build anyway." >&2
    exit 1
  fi
  echo "⚠ building WITHOUT a Toss client key (ALLOW_KEYLESS_TOSS=1)" >&2
fi

echo "── docker → Artifact Registry auth"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo "── build (local names; DOMAIN is only needed to parse the compose override)"
DOMAIN="${DOMAIN:-build.local}" VG_REGISTRY_PREFIX="" VG_TAG=latest \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build

echo "── tag + push :${TAG} and :latest"
for s in "${SERVICES[@]}"; do
  docker tag "valuegraph-${s}:latest" "${PREFIX}/valuegraph-${s}:${TAG}"
  docker tag "valuegraph-${s}:latest" "${PREFIX}/valuegraph-${s}:latest"
  docker push "${PREFIX}/valuegraph-${s}:${TAG}"
  docker push "${PREFIX}/valuegraph-${s}:latest"
done

cat <<DONE

✅ pushed ${#SERVICES[@]} images to ${PREFIX} @ ${TAG}
Deploy on the VM (root .env should hold DOMAIN + VG_REGISTRY_PREFIX=${PREFIX}/):
    ./deploy/deploy.sh ${TAG}
Rollback later:
    ./deploy/deploy.sh <previous-tag>     # tags: gcloud artifacts docker tags list ${PREFIX}/valuegraph-web
DONE
