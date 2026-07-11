#!/usr/bin/env bash
# Tool coverage — calls EVERY catalog tool (all 40) through the metered gateway with
# real params and reports a matrix: ✓ real data · ⚠ env-gated (e.g. FRED's bot-wall
# from a datacenter IP) · ✗ failure. No LLM key needed.
#
#   bash scripts/coverage.sh
set -u
cd "$(dirname "$0")/.."
. "$(dirname "$0")/_viz.sh"

CP=http://127.0.0.1:8010; RAG=http://127.0.0.1:8002
A=(-H "X-Admin-Token: dev-admin-token"); J=(-H "Content-Type: application/json")
FAILS=0; WARNS=0; PASS=0; TOTAL=0
jget() { python3 -c "import json,sys;print(json.load(sys.stdin)$1)" 2>/dev/null || true; }

section "Bring up data plane + gateway"
# NEVER `down -v` here: this tears down the USER'S LIVE STACK — `-v` would destroy
# the Postgres volume (price backfills, RAG index, conversations, tenants). Data must
# survive a harness run; the stack is recreated below with volumes intact.
docker compose down >/dev/null 2>&1 || true
docker compose up --build -d datasets rag control-plane >/dev/null 2>&1 || { echo "compose up failed"; exit 1; }
for _ in $(seq 1 40); do
  st=$(docker inspect --format '{{.State.Health.Status}}' valuegraph-platform-control-plane-1 2>/dev/null || echo none)
  [ "$st" = healthy ] && break; sleep 2
done
ok "stack healthy"

# tenant + key with EVERY connector activated
TID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants -d '{"name":"COV"}' | jget "['id']")
PID=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/tenants/$TID/projects -d '{"name":"p"}' | jget "['id']")
KEY=$(curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/keys -d '{"name":"k"}' | jget "['api_key']")
for c in sec_edgar yahoo fred opendart ecos google_news datasets_store rag; do
  curl -s "${A[@]}" "${J[@]}" -X POST $CP/admin/projects/$PID/activations -d "{\"connector_id\":\"$c\"}" >/dev/null
done
# seed one RAG doc so rag__search returns a hit
curl -s "${J[@]}" -X POST $RAG/rag/ingest -d '{"documents":[{"text":"Apple relies on TSMC to fabricate its custom silicon chips.","source":"SEC EDGAR","ticker":"AAPL","url":"https://sec.gov/aapl"}]}' >/dev/null
ok "tenant key issued · 8 connectors activated · RAG seeded"

# tool: $1 label  $2 METHOD  $3 path?query  $4 body(POST)  $5 expect-substr(optional)
tool() {
  local data code body lbl note
  if [ "$2" = GET ]; then
    data=$(curl -s -w $'\n%{http_code}' -H "X-API-KEY: $KEY" "$CP$3")
  else
    data=$(curl -s -w $'\n%{http_code}' -H "X-API-KEY: $KEY" "${J[@]}" -X "$2" "$CP$3" -d "$4")
  fi
  code=${data##*$'\n'}; body=${data%$'\n'*}
  TOTAL=$((TOTAL + 1)); lbl=$(printf '%-34s' "$1")
  if [ "$code" = 200 ]; then
    PASS=$((PASS + 1))
    if [ -n "${5:-}" ] && printf '%s' "$body" | grep -q -- "$5"; then note="$(dim '200')"; else note="$(dim '200 · empty')"; fi
    printf '    %s %s %s\n' "$(green '✓')" "$lbl" "$note"
  elif [ "$code" = 503 ]; then
    WARNS=$((WARNS + 1))
    printf '    %s %s %s\n' "$(yellow '⚠')" "$lbl" "$(dim '503 · env-gated (datacenter IP wall)')"
  else
    FAILS=$((FAILS + 1))
    printf '    %s %s %s\n' "$(red '✗')" "$lbl" "$(red "$code")"
  fi
}

US='ticker=AAPL&market=US'; KR='ticker=005930&market=KR'; ANN='period=annual'

section "SEC EDGAR — US fundamentals (15 tools)"
tool "company_facts"          GET "/company/facts?$US"                          "" 'company_facts'
tool "company_search"         GET "/company/search?q=apple&market=US"           "" 'results'
tool "income_statements"      GET "/financials/income-statements?$US&$ANN"      "" 'income_statements'
tool "balance_sheets"         GET "/financials/balance-sheets?$US&$ANN"         "" 'balance_sheets'
tool "cash_flow_statements"   GET "/financials/cash-flow-statements?$US&$ANN"   "" 'cash_flow'
tool "all_financials"         GET "/financials?$US&$ANN"                        "" 'income'
tool "as_reported"            GET "/financials/as-reported?ticker=AAPL&market=US&period=annual" "" 'periods'
tool "filings"                GET "/filings?$US"                                "" 'filings'
tool "earnings"               GET "/earnings?ticker=AAPL"                       "" 'earnings'
tool "insider_trades"         GET "/insider-trades?$US"                         "" 'insider'
tool "institutional_holdings" GET "/institutional-holdings?filer_cik=0001067983" "" 'holdings'
tool "index_funds"            GET "/index-funds?ticker=SPY&market=US"           "" 'holdings'
tool "gurus"                  GET "/gurus?slug=buffett"                          "" 'holdings'
tool "metrics_snapshot"       GET "/financial-metrics/snapshot?$US"             "" 'snapshot'
tool "comparables"            GET "/comparables?tickers=AAPL,MSFT&market=US"     "" 'comparables'

section "Yahoo Finance — prices + corporate actions + technicals (4 tools)"
tool "prices"          GET "/prices?ticker=AAPL&interval=day&start_date=2024-01-02&end_date=2024-01-08&market=US" "" 'close'
tool "price_snapshot"  GET "/prices/snapshot?$US"                               "" 'snapshot'
tool "corporate_actions" GET "/corporate-actions?ticker=AAPL&market=US"          "" 'dividends'
tool "technical_indicators" GET "/technical-indicators?ticker=AAPL&indicators=sma_20,rsi_14,macd&market=US" "" 'indicators'

section "FRED / DBnomics — US macro (3 tools · cloud-safe via DBnomics/BIS fallback)"
tool "interest_rates"          GET "/macro/interest-rates?bank=FED&market=US"          "" 'interest'
tool "interest_rates_snapshot" GET "/macro/interest-rates/snapshot?bank=FED&market=US" "" 'interest'
tool "economic_indicators"     GET "/macro/indicators?indicator=cpi"                   "" 'observations'

section "OpenDART — KR fundamentals (11 tools)"
tool "company_facts"        GET "/company/facts?$KR"                            "" 'company_facts'
tool "company_search"       GET "/company/search?q=005930&market=KR"           "" 'results'
tool "income_statements"    GET "/financials/income-statements?$KR&$ANN"        "" 'income_statements'
tool "balance_sheets"       GET "/financials/balance-sheets?$KR&$ANN"           "" 'balance_sheets'
tool "cash_flow_statements" GET "/financials/cash-flow-statements?$KR&$ANN"     "" 'cash_flow'
tool "all_financials"       GET "/financials?$KR&$ANN"                          "" 'income'
tool "filings"              GET "/filings?$KR"                                  "" 'filings'
tool "earnings"             GET "/earnings?$KR"                                 "" 'earnings'
tool "insider_trades"       GET "/insider-trades?$KR"                           "" 'insider'
tool "metrics_snapshot"     GET "/financial-metrics/snapshot?$KR"               "" 'snapshot'
tool "comparables"          GET "/comparables?tickers=005930,000660&market=KR"  "" 'comparables'

section "Bank of Korea ECOS — KR macro (2 tools)"
tool "interest_rates"          GET "/macro/interest-rates?bank=BOK&market=KR"          "" 'interest'
tool "interest_rates_snapshot" GET "/macro/interest-rates/snapshot?bank=BOK&market=KR" "" 'interest'

section "Google News (1 tool)"
tool "news"  GET "/news?ticker=NVDA&market=US"  "" 'news'

section "Datasets store — cross-sectional search + derived metrics (3 tools)"
tool "screener"        POST "/financials/search/screener"   '{"filters":[{"field":"revenue","operator":"gt","value":1}],"limit":3}'  'search_results'
tool "line_items"      POST "/financials/search/line-items" '{"tickers":["AAPL"],"line_items":["revenue"],"period":"annual"}'         'search_results'
tool "metrics_history" GET  "/financial-metrics?ticker=AAPL&market=US&period=annual" "" 'metrics'

section "RAG — semantic retrieval (1 tool)"
tool "search"  POST "/rag/search"  '{"query":"Apple TSMC supplier chips","top_k":3}'  'hits'

section "Coverage summary"
echo "    $(green "✓ real data: $PASS")   $(yellow "⚠ env-gated: $WARNS")   $(red "✗ failed: $FAILS")   $(dim "/ $TOTAL tools")"
docker compose down >/dev/null 2>&1
result "TOOL COVERAGE"
exit "$FAILS"
