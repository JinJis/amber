#!/usr/bin/env bash
# End-to-end test of the whole platform via docker compose:
#   catalog -> tenant/key -> entitlement (403/200) -> data plane + RAG through the
#   gateway -> metering -> MCP tools. Exits non-zero on any failed assertion.
#
# Usage:  bash scripts/e2e.sh
set -u
cd "$(dirname "$0")/.."
. "$(dirname "$0")/_viz.sh"   # colored ok/fail/check/has/section/result

FAILS=0
jget() { python3 -c "import json,sys;print(json.load(sys.stdin)$1)" 2>/dev/null || true; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

DP=http://127.0.0.1:8000; CP=http://127.0.0.1:8010; RAG=http://127.0.0.1:8002
A=(-H "X-Admin-Token: dev-admin-token"); J=(-H "Content-Type: application/json")
PRICE_Q="ticker=AAPL&market=US&interval=day&start_date=2024-01-02&end_date=2024-01-05"

# The platform is Gemini-only (the legacy stub planner was removed), so the agent legs need a
# real key. Skip cleanly (exit 2) when none is present — same contract as e2e_live.sh.
envval() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true; }
GKEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"; [ -z "$GKEY" ] && GKEY="$(envval GOOGLE_API_KEY)"
if [ -z "$GKEY" ]; then
  echo "⏭️  SKIPPED — no GOOGLE_API_KEY (env or .env). The full e2e drives the Gemini agent; put a key in .env to run it."
  exit 2
fi

section "bring up stack (build)"
# Start from a clean slate: drop volumes so SQLite schemas match the current models
# (create_all does not ALTER existing tables when columns are added). Backend services only
# (web is skipped to keep the e2e fast). Gemini planner via .env (GOOGLE_API_KEY).
docker compose down -v >/dev/null 2>&1 || true
docker compose up --build -d datasets rag control-plane agent-engine studio-api \
  || { echo "compose up failed"; exit 1; }
for _ in $(seq 1 40); do
  st=$(docker inspect --format '{{.State.Health.Status}}' valuegraph-platform-control-plane-1 2>/dev/null || echo none)
  [ "$st" = healthy ] && break; sleep 2
done
echo "  control-plane health: $(curl -s $CP/health)"

section "catalog"
N=$(curl -s $DP/catalog | jget '["count"]'); check "data-plane catalog connectors>=8" "$([ "${N:-0}" -ge 8 ] && echo yes)" yes
NC=$(curl -s $CP/catalog | jget '["count"]'); check "gateway catalog passthrough>=8" "$([ "${NC:-0}" -ge 8 ] && echo yes)" yes

section "tenant / project / key"
TID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants -d '{"name":"E2E"}' | jget '["id"]')
PID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants/$TID/projects -d '{"name":"p"}' | jget '["id"]')
KEY=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/keys -d '{"name":"k"}' | jget '["api_key"]')
[ -n "$KEY" ] && ok "key issued (${KEY:0:14}...)" || fail "key issuance"
H=(-H "X-API-KEY: $KEY")

section "entitlement: prices (yahoo)"
check "no key -> 401" "$(code "$CP/prices?$PRICE_Q")" 401
check "not activated -> 403" "$(code "${H[@]}" "$CP/prices?$PRICE_Q")" 403
curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/activations -d '{"connector_id":"yahoo"}' >/dev/null
R=$(curl -s -D /tmp/eh "${H[@]}" "$CP/prices?$PRICE_Q")
has "activated -> real AAPL prices" "$R" '"ticker":"AAPL"'
has "x-connector: yahoo header" "$(cat /tmp/eh)" 'x-connector: yahoo'

section "entitlement: company/facts (sec_edgar not activated)"
check "sec_edgar not activated -> 403" "$(code "${H[@]}" "$CP/company/facts?ticker=AAPL&market=US")" 403

section "RAG through the gateway"
curl -s "${J[@]}" -X POST $RAG/rag/ingest \
  -d '{"documents":[{"text":"Apple relies on a limited number of suppliers; TSMC fabricates its custom silicon chips.","source":"SEC EDGAR","doc_type":"10-K","ticker":"AAPL","url":"https://sec.gov/aapl"}]}' >/dev/null
check "rag not activated -> 403" "$(code "${H[@]}" "${J[@]}" -X POST $CP/rag/search -d '{"query":"Apple TSMC chips supplier"}')" 403
curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/activations -d '{"connector_id":"rag"}' >/dev/null
RR=$(curl -s -D /tmp/rh "${H[@]}" "${J[@]}" -X POST $CP/rag/search -d '{"query":"Apple TSMC chips supplier","top_k":3}')
has "rag search -> provenance 'SEC EDGAR'" "$RR" 'SEC EDGAR'
has "x-connector: rag header" "$(cat /tmp/rh)" 'x-connector: rag'

section "metering"
U=$(curl -s "${A[@]}" $CP/admin/projects/$PID/usage)
has "usage records yahoo" "$U" 'yahoo'
has "usage records rag" "$U" '"rag"'

section "MCP tools via gateway (run inside docker — no host uv)"
MCP_OUT=$(docker compose run --rm -T --build -e MCP_API_KEY="$KEY" mcp python - <<'PY' 2>/dev/null
import asyncio
from mcpserver.tools import fetch_catalog, build_tools, tool_index, call_tool
async def main():
    idx = tool_index(build_tools(await fetch_catalog()))
    print("TOOLS", "yahoo__prices" in idx, "rag__search" in idx)
    r = await call_tool(idx["yahoo__prices"], {"ticker":"AAPL","interval":"day","start_date":"2024-01-02","end_date":"2024-01-05","market":"US"})
    print("CALL", r["status"], r["connector"])
asyncio.run(main())
PY
)
echo "$MCP_OUT" | sed 's/^/    /'
has "MCP exposes yahoo__prices + rag__search" "$MCP_OUT" 'TOOLS True True'
has "MCP yahoo call -> 200 via yahoo" "$MCP_OUT" 'CALL 200 yahoo'

section "Agent Engine (Gemini planner)"
AG=http://127.0.0.1:8003
for _ in $(seq 1 20); do [ "$(curl -s -o /dev/null -w '%{http_code}' $AG/health)" = 200 ] && break; sleep 2; done
RF=$(curl -s "${J[@]}" -X POST $AG/agent/run -H "X-API-KEY: $KEY" -d '{"task":"should I buy AAPL?"}')
has "agent refuses forecast/advice" "$RF" '"refused":true'
AR=$(curl -s "${J[@]}" -X POST $AG/agent/run -H "X-API-KEY: $KEY" -d '{"task":"What is AAPL price?"}')
has "agent used yahoo__prices tool" "$AR" 'yahoo__prices'
has "agent answer cites Yahoo Finance" "$AR" 'Yahoo Finance'

section "studio-api chat (full product backend chain)"
SA=http://127.0.0.1:8004
for _ in $(seq 1 20); do [ "$(curl -s -o /dev/null -w '%{http_code}' $SA/health)" = 200 ] && break; sleep 2; done
SC=$(curl -s "${J[@]}" -X POST $SA/chat/stream -H "X-Service-Token: dev-service-token" -H "X-User-Email: e2e@user.com" \
  -d '{"messages":[{"role":"user","content":"What is AAPL price?"}]}')
has "studio chat streams a tool event" "$SC" 'yahoo__prices'
has "studio chat answer cites Yahoo Finance" "$SC" 'Yahoo Finance'
has "studio chat persists a conversation" "$SC" 'conversation'
CV=$(curl -s -H "X-Service-Token: dev-service-token" -H "X-User-Email: e2e@user.com" $SA/conversations)
has "studio lists the saved conversation" "$CV" '"id"'

section "F1: agent builder (templates + custom agent restricts data sources)"
SH=(-H "X-Service-Token: dev-service-token" -H "X-User-Email: e2e@user.com")
AGENTS=$(curl -s "${SH[@]}" $SA/agents)
has "studio seeds provided templates" "$AGENTS" 'tpl_desk'
CONS=$(curl -s "${SH[@]}" $SA/connectors)
has "studio exposes connectors for the builder" "$CONS" 'sec_edgar'
# create an agent restricted to SEC filings only (no yahoo prices)
AID=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/agents \
  -d '{"name":"Filings only","data_sources":["sec_edgar"]}' | jget '["id"]')
[ -n "$AID" ] && ok "custom agent created ($AID)" || fail "custom agent creation"
# a price question routed through this agent must NOT reach yahoo (restricted out)
SCA=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/chat/stream \
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"AAPL price?\"}],\"agent_id\":\"$AID\"}")
has "agent-scoped chat uses the allowed SEC source" "$SCA" 'sec_edgar__'
printf '%s' "$SCA" | grep -q 'yahoo__prices' && fail "restricted agent leaked yahoo__prices" || ok "restricted agent blocked yahoo__prices"

section "F2: prompt library (community catalog + import)"
COMM=$(curl -s "${SH[@]}" $SA/prompts/community)
has "studio seeds community prompts" "$COMM" 'cpr_earnings'
# personal library starts without the community prompt; import puts an editable copy there
PID=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/prompts/cpr_earnings/import | jget '["id"]')
[ -n "$PID" ] && ok "imported community prompt ($PID)" || fail "prompt import"
MINE=$(curl -s "${SH[@]}" $SA/prompts)
has "imported prompt lands in personal library" "$MINE" "$PID"
has "imported copy records its source" "$MINE" 'cpr_earnings'

section "F3: dashboard (template -> widget -> alert -> delivery)"
# provided dashboard templates are seeded
TPLS=$(curl -s "${SH[@]}" $SA/templates)
has "studio seeds dashboard templates" "$TPLS" 'dt_semi'
# default board exists (auto-created)
BID=$(curl -s "${SH[@]}" $SA/boards | jget '["boards"][0]["id"]')
[ -n "$BID" ] && ok "default dashboard exists ($BID)" || fail "default dashboard"
# materialize a template's widgets onto the board
NW=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/board/from-template -d "{\"template_id\":\"dt_semi\",\"board_id\":\"$BID\"}" | jget '["created"]')
[ "${NW:-0}" -ge 1 ] && ok "template created $NW widgets" || fail "from-template widget creation"
PINS=$(curl -s "${SH[@]}" "$SA/board?board_id=$BID")
has "dashboard now has widgets" "$PINS" 'yahoo__prices'
# create a board-scope alert (telegram), then fire it — no creds => simulated, but a sourced delivery is recorded
ALID=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/alerts \
  -d "{\"name\":\"E2E 금리\",\"scope\":\"board\",\"board_id\":\"$BID\",\"trigger_type\":\"rate\",\"params\":{\"target\":\"@mc\"},\"schedule\":{\"freq\":\"event\"},\"channels\":[\"telegram\"]}" | jget '["id"]')
[ -n "$ALID" ] && ok "alert created ($ALID)" || fail "alert creation"
FIRE=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/alerts/$ALID/fire)
has "alert fires (simulated, no creds)" "$FIRE" 'simulated'
has "delivery carries as_of + source" "$FIRE" 'as_of'
has "delivery carries a desk deep link" "$FIRE" 'deeplink'
DLV=$(curl -s "${SH[@]}" "$SA/deliveries?alert_id=$ALID")
has "delivery recorded in the feed" "$DLV" "$ALID"
# channel link shows connected
curl -s "${SH[@]}" "${J[@]}" -X POST $SA/channels -d '{"channel":"slack","config":{"webhook_url":"https://example.invalid/hook"}}' >/dev/null
CHS=$(curl -s "${SH[@]}" $SA/channels)
has "linked channel reports connected" "$CHS" 'connected'

section "teardown"
docker compose down -v >/dev/null 2>&1

result "E2E"
exit "$FAILS"
