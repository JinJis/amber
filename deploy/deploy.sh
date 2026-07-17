#!/usr/bin/env bash
# Rolling (near-)zero-downtime update — run ON the VM (docs/INFRA.md §4-E).
#
#   DOMAIN=<domain> ./deploy/deploy.sh              # pull latest development + rolling update
#   DOMAIN=<domain> ./deploy/deploy.sh <git-ref>    # deploy/rollback to a specific commit/tag
#
# How the "no downtime" works on a single VM:
#   1. New images are BUILT while the old stack keeps serving (the slow part, web ~minutes).
#   2. Services restart ONE AT A TIME in dependency order, each gated on its healthcheck
#      (`up -d --no-deps --wait`) — unchanged services are no-ops.
#   3. studio-api/agent-engine have stop_grace_period=45s → in-flight SSE turns drain
#      before the old container dies (a turn longer than that is cut; SC-2.3 refunds quota).
#   4. The web swap gap (~2-5s) is absorbed by Caddy `lb_try_duration 30s` — new requests
#      wait and retry instead of 502.
#   5. postgres is NEVER restarted here (it would take everything down) — upgrade it
#      manually in a maintenance window.
# NEVER run scripts/test_all.sh / e2e / coverage on this VM — they `docker compose down`
# the live stack (they are dev-machine harnesses).
set -euo pipefail

cd "$(dirname "$0")/.."
: "${DOMAIN:?set DOMAIN=your.domain (compose interpolation needs it)}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
REF="${1:-}"

echo "── 1/5 fetch code"
if [ -n "${REF}" ]; then
  git fetch --all --tags
  git checkout --detach "${REF}"
  echo "   checked out ${REF} (rollback/pin mode)"
else
  git pull --ff-only
fi
git log --oneline -1

echo "── 2/5 build new images (old stack keeps serving)"
"${COMPOSE[@]}" build

echo "── 3/5 rolling restart (health-gated, one service at a time; unchanged = no-op)"
# dependency order: data plane → gateway → agent → studio → worker/admin → web last.
for s in datasets rag control-plane agent-engine studio-api worker admin; do
  echo "   ↻ ${s}"
  "${COMPOSE[@]}" up -d --no-deps --wait "${s}"
done
echo "   ↻ web (swap gap absorbed by Caddy lb_try)"
"${COMPOSE[@]}" up -d --no-deps web

echo "── 4/5 caddy (recreate only if image/config changed + in-place Caddyfile reload)"
"${COMPOSE[@]}" up -d --no-deps caddy
"${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null \
  || echo "   (caddy reload skipped — container was just recreated)"

echo "── 5/5 prune old image layers"
docker image prune -f >/dev/null

"${COMPOSE[@]}" ps
echo "✅ deployed $(git log --oneline -1 | cut -d' ' -f1). Verify: curl -s https://${DOMAIN}/ | head -1"
