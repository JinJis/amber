#!/usr/bin/env bash
# Rolling (near-)zero-downtime update — run ON the VM (docs/INFRA.md §4-E).
#
#   ./deploy/deploy.sh <image-tag>     # registry mode: pull that tag from AR + rolling swap
#   ./deploy/deploy.sh                 # registry mode: pull :latest
#   VG_REGISTRY_PREFIX= ./deploy/deploy.sh [git-ref]   # fallback: build on the VM
#
# Config comes from the VM's root .env (compose ${...} interpolation reads it):
#   DOMAIN=your.domain
#   VG_REGISTRY_PREFIX=asia-northeast3-docker.pkg.dev/<project>/vg/   # trailing slash!
# Rollback = deploy an older tag: ./deploy/deploy.sh <previous-tag> (images are immutable,
# no rebuild — identical bytes to what ran before).
#
# How the "no downtime" works on a single VM:
#   1. Images are PULLED (or built, fallback mode) while the old stack keeps serving.
#   2. Services swap ONE AT A TIME in dependency order, each gated on its healthcheck
#      (`up -d --no-deps --wait`) — unchanged services are no-ops.
#   3. studio-api/agent-engine have stop_grace_period=45s → in-flight SSE turns drain
#      before the old container dies (longer turns are cut; SC-2.3 refunds quota).
#   4. The web swap gap (~2-5s) is absorbed by Caddy `lb_try_duration 30s`.
#   5. postgres is NEVER restarted here — upgrade it manually in a maintenance window.
# NEVER run scripts/test_all.sh / e2e / coverage on this VM — they `docker compose down`
# the live stack (they are dev-machine harnesses).
set -euo pipefail

cd "$(dirname "$0")/.."
# Load the root .env so DOMAIN/VG_* are visible to this script too (compose reads it itself) —
# but CALLER-supplied overrides must win (e.g. `VG_REGISTRY_PREFIX= ./deploy/deploy.sh` for
# build-fallback mode): remember them before sourcing, restore after.
_CALLER_REG_SET="${VG_REGISTRY_PREFIX+x}"; _CALLER_REG="${VG_REGISTRY_PREFIX-}"
_CALLER_TAG_SET="${VG_TAG+x}"; _CALLER_TAG="${VG_TAG-}"
# shellcheck source=/dev/null
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -n "${_CALLER_REG_SET}" ]; then export VG_REGISTRY_PREFIX="${_CALLER_REG}"; fi
if [ -n "${_CALLER_TAG_SET}" ]; then export VG_TAG="${_CALLER_TAG}"; fi
: "${DOMAIN:?set DOMAIN in the root .env (compose interpolation needs it)}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
ARG="${1:-}"

echo "── 1/5 sync deploy config (compose files · Caddyfile · env examples)"
git pull --ff-only || echo "   (git pull skipped — detached/diverged; deploying images only)"

if [ -n "${VG_REGISTRY_PREFIX:-}" ]; then
  export VG_TAG="${ARG:-${VG_TAG:-latest}}"
  echo "── 2/5 pull images: ${VG_REGISTRY_PREFIX}valuegraph-*:${VG_TAG} (old stack keeps serving)"
  # idempotent AR docker auth for THIS user (per-user ~/.docker/config.json — a sudo/root or
  # different-OS-Login-user deploy would otherwise fail 'unauthenticated' despite correct IAM)
  AR_HOST="${VG_REGISTRY_PREFIX%%/*}"
  gcloud auth configure-docker "${AR_HOST}" --quiet 2>/dev/null \
    || echo "   ⚠ gcloud auth configure-docker failed — pull may be unauthenticated"
  # plain pull — NOT --ignore-buildable: every service also has build:, so that flag would
  # skip them all and silently pull nothing. A real pull failure (auth/missing tag) must
  # abort HERE, before the rolling swap touches the running stack.
  "${COMPOSE[@]}" pull
  UP_FLAGS=(--no-build)
else
  if [ -n "${ARG}" ]; then
    git fetch --all --tags && git checkout --detach "${ARG}"
    echo "   checked out ${ARG} (build-mode rollback/pin)"
  fi
  echo "── 2/5 build images on the VM (fallback mode — old stack keeps serving)"
  "${COMPOSE[@]}" build
  UP_FLAGS=()
fi

echo "── 3/5 rolling swap (health-gated, one service at a time; unchanged = no-op)"
# worker right after datasets (same image) to minimize the schema/code-skew window.
for s in datasets worker rag control-plane agent-engine studio-api admin; do
  echo "   ↻ ${s}"
  "${COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 300 ${UP_FLAGS[@]+"${UP_FLAGS[@]}"} "${s}"
done
echo "   ↻ web (swap gap absorbed by Caddy lb_try)"
"${COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 120 ${UP_FLAGS[@]+"${UP_FLAGS[@]}"} web

echo "── 4/5 caddy (recreate only if image/config changed + in-place Caddyfile reload)"
"${COMPOSE[@]}" up -d --no-deps caddy
"${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null \
  || echo "   (caddy reload skipped — container was just recreated)"

echo "── 5/5 prune superseded local images (registry keeps rollback history; keep ~1 week local)"
# -a: old SHA-tagged images stay TAGGED after a swap, so dangling-only prune (-f alone) would
# never reclaim them and VM disk would grow every deploy. until=168h keeps a week of local
# tags for instant rollback without a pull.
docker image prune -af --filter "until=168h" >/dev/null

"${COMPOSE[@]}" ps
echo "✅ deployed ${VG_TAG:-$(git log --oneline -1 | cut -d' ' -f1)}. Verify: curl -s https://${DOMAIN}/ | head -1"
