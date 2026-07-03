#!/usr/bin/env bash
# One command to run EVERYTHING in Docker — no host `uv`/`npm`/`pytest` needed.
# Unit tests run in the uv base image (source mounted, deps cached in volumes); the
# web build is a docker build; the e2e harnesses + eval drive `docker compose`.
#
#   bash scripts/test_all.sh
#
# GOOGLE_API_KEY (in .env or env) enables the live-Gemini e2e + the quality
# eval; without it they skip cleanly (not counted as failures). Only `docker` + a
# POSIX shell are required on the host.
set -u
cd "$(dirname "$0")/.."
. "$(dirname "$0")/_viz.sh"   # colors + section()
FAIL=0
step() { echo; hr; section "$1"; }

UVIMG="ghcr.io/astral-sh/uv:python3.11-bookworm-slim"
# Run a service's unit tests inside the uv image: source bind-mounted, a per-service
# .venv volume + a shared uv cache so repeat runs are fast. $2 = extra `uv run` args.
unit() {
  # AUTH_DISABLED=true: behind the gateway the data plane trusts the caller (the control plane is
  # the auth), so the datasets route tests expect auth off — same as the real stack's .env.
  docker run --rm \
    -v "$PWD/$1:/app" -v "vg_uvcache:/root/.cache/uv" -v "vg_venv_${1//[^a-zA-Z0-9]/_}:/app/.venv" \
    -w /app -e UV_COMPILE_BYTECODE=0 -e UV_LINK_MODE=copy -e AUTH_DISABLED=true "$UVIMG" \
    sh -lc "rm -f studio.db; uv run --extra dev ${2:-} pytest -q"
}

step "Unit tests (in docker — uv image, no host uv)"
for d in datasets control-plane mcp agent-engine studio-api; do
  echo "-- $d"; unit "$d" "" || FAIL=1
done
echo "-- rag (pipeline on a fake embedder; live semantic skips without GOOGLE_API_KEY)"; unit rag "" || FAIL=1

step "Web build (docker build)"
docker compose build web >/dev/null 2>&1 && echo "  web build ok" || { echo "  web build FAILED"; FAIL=1; }

step "Tool coverage — every catalog tool through the gateway (no key)"
bash scripts/coverage.sh || FAIL=1

step "Docker e2e — full product chain (Gemini; skips cleanly without GOOGLE_API_KEY)"
bash scripts/e2e.sh; rc=$?
if [ "$rc" = 2 ]; then echo "  (skipped — no GOOGLE_API_KEY)"; elif [ "$rc" != 0 ]; then FAIL=1; fi

step "Docker e2e — functional (real data + MCP + semantic RAG, no key)"
bash scripts/e2e_functional.sh || FAIL=1

step "Docker e2e — live Gemini (skips cleanly without GOOGLE_API_KEY)"
bash scripts/e2e_live.sh; rc=$?
if [ "$rc" = 2 ]; then echo "  (skipped — no GOOGLE_API_KEY)"; elif [ "$rc" != 0 ]; then FAIL=1; fi

step "Quality eval (skips cleanly without GOOGLE_API_KEY)"
docker compose up -d >/dev/null 2>&1
for _ in $(seq 1 40); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8004/health 2>/dev/null)" = 200 ] && break; sleep 2
done
python3 eval/run_eval.py; rc=$?
if [ "$rc" = 2 ]; then echo "  (skipped — no GOOGLE_API_KEY)"; elif [ "$rc" != 0 ]; then FAIL=1; fi

echo; hr
if [ "$FAIL" = 0 ]; then echo "$(green "✅ ALL TESTS PASSED")"; else echo "$(red "❌ SOME TESTS FAILED")"; fi
exit "$FAIL"
