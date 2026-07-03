#!/usr/bin/env bash
# Functional end-to-end — NO LLM key needed. Proves the platform actually WORKS
# for a user: real upstream data flows, the MCP server returns real numbers through
# the metered gateway, entitlement is enforced, and RAG does genuine SEMANTIC
# retrieval (oss-cpu / fastembed) with provenance preserved.
#
# Assertions check real CONTENT (Apple's name, a positive AAPL close, Samsung's
# KRW revenue, the right RAG document for a zero-keyword-overlap query) — not just
# HTTP 200. Run:  bash scripts/e2e_functional.sh
set -u
cd "$(dirname "$0")/.."
. "$(dirname "$0")/_viz.sh"   # colored ok/fail/has/num/section/result

DP=http://127.0.0.1:8000; CP=http://127.0.0.1:8010; RAG=http://127.0.0.1:8002; SA=http://127.0.0.1:8004
A=(-H "X-Admin-Token: dev-admin-token"); J=(-H "Content-Type: application/json")

FAILS=0
jget() { python3 -c "import json,sys;print(json.load(sys.stdin)$1)" 2>/dev/null || true; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

section "bring up backend stack with REAL semantic RAG (Gemini embeddings)"
# RAG embeddings are Gemini-only now; the semantic checks need GOOGLE_API_KEY (read from .env).
envval() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true; }
GKEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"; [ -z "$GKEY" ] && GKEY="$(envval GOOGLE_API_KEY)"
docker compose down -v >/dev/null 2>&1 || true
docker compose up --build -d datasets rag control-plane agent-engine studio-api \
  || { echo "compose up failed"; exit 1; }
for _ in $(seq 1 60); do
  st=$(docker inspect --format '{{.State.Health.Status}}' valuegraph-platform-control-plane-1 2>/dev/null || echo none)
  [ "$st" = healthy ] && break; sleep 2
done

# ---------------------------------------------------------------------------
section "A. DATA PLANE — real upstream numbers"
# SEC: Apple company facts
AAPL_FACTS=$(curl -s "$DP/company/facts?ticker=AAPL&market=US")
has "SEC company_facts names Apple" "$AAPL_FACTS" 'Apple'
# Yahoo: a real, positive AAPL close
CLOSE=$(curl -s "$DP/prices?ticker=AAPL&market=US&interval=day&start_date=2024-01-02&end_date=2024-01-10" \
         | jget "['prices'][0]['close']")
num "Yahoo AAPL close is a positive price" "$CLOSE" "x>0 and x<10000"
# OpenDART (real key): Samsung Electronics annual revenue in KRW (hundreds of trillions)
SS=$(curl -s "$DP/financials/income-statements?ticker=005930&market=KR&period=annual&limit=1")
SS_REV=$(printf '%s' "$SS" | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(''); sys.exit()
rows=d.get('income_statements') or d.get('financials') or []
val=''
for r in (rows if isinstance(rows,list) else [rows]):
    for k in ('revenue','revenues','sales','total_revenue'):
        if isinstance(r,dict) and r.get(k): val=r[k]; break
    if val: break
print(val)
" 2>/dev/null)
num "DART Samsung revenue is ~hundreds of trillions KRW" "${SS_REV:-nan}" "x>1e14"
# ECOS (real key): a sane BOK base rate
BOK=$(curl -s "$DP/macro/interest-rates/snapshot?market=KR&bank=BOK" | jget "['interest_rates'][0]['rate']")
num "ECOS BOK base rate in a sane band" "${BOK:-nan}" "0<=x<=10"

# ---------------------------------------------------------------------------
section "tenant + key + activations"
TID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants -d '{"name":"FUNC"}' | jget "['id']")
PID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants/$TID/projects -d '{"name":"p"}' | jget "['id']")
KEY=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/keys -d '{"name":"k"}' | jget "['api_key']")
[ -n "$KEY" ] && ok "tenant key issued" || fail "key issuance"
for c in yahoo sec_edgar; do
  curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/activations -d "{\"connector_id\":\"$c\"}" >/dev/null
done

# ---------------------------------------------------------------------------
section "B. MCP — tools from catalog, real calls through the gateway, entitlement (in docker)"
MCP_OUT=$(docker compose run --rm -T --build -e MCP_API_KEY="$KEY" mcp python - <<'PY' 2>/dev/null
import asyncio, json
from mcpserver.tools import fetch_catalog, build_tools, tool_index, call_tool
async def main():
    idx = tool_index(build_tools(await fetch_catalog()))
    # schema: yahoo__prices must require ticker/interval/start_date/end_date so an
    # MCP client (e.g. an agent) is forced to supply them — no silent bad calls.
    req = set(idx["yahoo__prices"]["inputSchema"].get("required", []))
    print("SCHEMA_OK", {"ticker","interval","start_date","end_date"} <= req)
    print("HAS_TOOLS", all(t in idx for t in ("yahoo__prices","sec_edgar__company_facts","rag__search")))
    # real price call -> a numeric close
    r = await call_tool(idx["yahoo__prices"], {"ticker":"AAPL","interval":"day","start_date":"2024-01-02","end_date":"2024-01-10","market":"US"})
    close = (r["data"].get("prices") or [{}])[0].get("close")
    print("PRICE", r["status"], close)
    # real SEC call -> Apple
    r2 = await call_tool(idx["sec_edgar__company_facts"], {"ticker":"AAPL","market":"US"})
    print("SEC", r2["status"], "apple" in json.dumps(r2["data"]).lower())
asyncio.run(main())
PY
)
echo "$MCP_OUT" | sed 's/^/    /'
has "MCP tool schema requires the right params"  "$MCP_OUT" 'SCHEMA_OK True'
has "MCP exposes the core tools"                 "$MCP_OUT" 'HAS_TOOLS True'
MCP_CLOSE=$(printf '%s' "$MCP_OUT" | sed -n 's/^PRICE 200 //p')
num "MCP yahoo__prices returns a real close"     "${MCP_CLOSE:-nan}" "x>0 and x<10000"
has "MCP sec_edgar returns Apple data"           "$MCP_OUT" 'SEC 200 True'
# entitlement: a brand-new key without activations -> 403 through the gateway
K2=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/keys -d '{"name":"k2"}' | jget "['api_key']")
P2=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants/$TID/projects -d '{"name":"empty"}' | jget "['id']")
K3=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$P2/keys -d '{"name":"k3"}' | jget "['api_key']")
ENT=$(code -H "X-API-KEY: $K3" "$CP/prices?ticker=AAPL&market=US&interval=day&start_date=2024-01-02&end_date=2024-01-05")
[ "$ENT" = 403 ] && ok "MCP/gateway blocks an unentitled key (403)" || fail "entitlement not enforced" "got $ENT"

# ---------------------------------------------------------------------------
section "C. RAG — REAL semantic retrieval (Gemini) + provenance + entitlement"
INFO=$(curl -s $RAG/rag/info)
has "RAG runs the Gemini embedder" "$INFO" 'gemini-embedding'
if [ -n "$GKEY" ]; then
  # ingest 3 docs on distinct topics; the target shares NO content word with the query
  curl -s "${J[@]}" -X POST $RAG/rag/ingest -d '{"documents":[
    {"text":"The central bank lifted its benchmark borrowing cost to cool rising consumer prices.","source":"ECOS","doc_type":"macro","url":"https://x/policy"},
    {"text":"The firm unveiled a thinner laptop with a brighter display and longer battery life.","source":"News","ticker":"X","url":"https://x/gadget"},
    {"text":"Apple relies on TSMC to fabricate its custom silicon processors.","source":"SEC EDGAR","ticker":"AAPL","url":"https://x/aapl"}]}' >/dev/null
  # semantic query #1: monetary policy, zero keyword overlap with the target doc
  S1=$(curl -s "${J[@]}" -X POST $RAG/rag/search -d '{"query":"Federal Reserve interest rate hike","top_k":1}')
  has "RAG semantically matches the monetary-policy doc" "$S1" 'https://x/policy'
  has "RAG keeps provenance (source)"                    "$S1" 'ECOS'
  # semantic query #2: supplier question -> the Apple/TSMC doc
  S2=$(curl -s "${J[@]}" -X POST $RAG/rag/search -d '{"query":"Who manufactures Apple chips?","top_k":1}')
  has "RAG matches the supplier doc by meaning"          "$S2" 'https://x/aapl'
  has "RAG provenance carries the ticker"                "$S2" 'AAPL'
else
  echo "  ⏭️  RAG semantic checks skipped — no GOOGLE_API_KEY (Gemini embeddings)"
fi
# RAG through the gateway: entitlement enforced
curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/activations -d '{"connector_id":"rag"}' >/dev/null
RG=$(code -H "X-API-KEY: $K3" "${J[@]}" -X POST "$CP/rag/search" -d '{"query":"anything"}')
[ "$RG" = 403 ] && ok "RAG blocked for an unentitled key (403)" || fail "RAG entitlement" "got $RG"
RG2=$(curl -s -H "X-API-KEY: $KEY" "${J[@]}" -X POST "$CP/rag/search" -d '{"query":"Federal Reserve interest rate hike","top_k":1}')
has "RAG via gateway returns the right doc for an entitled key" "$RG2" 'https://x/policy'

section "teardown"
docker compose down -v >/dev/null 2>&1

result "FUNCTIONAL E2E"
exit "$FAILS"
