#!/usr/bin/env bash
# Live, LLM-backed end-to-end test: brings the WHOLE stack up in docker with the
# real Gemini planner (AGENT_LLM_BACKEND=gemini) and drives a genuine question
# from the agent loop all the way through the studio-api chat product, asserting
# a real LLM answer grounded in a tool call + citation.
#
# Requires GOOGLE_API_KEY (Gemini). Without it the script SKIPS the LLM leg and
# tells you exactly what to set — it never silently passes.
#
# Usage:  bash scripts/e2e_live.sh
#         GOOGLE_API_KEY=... AGENT_MODEL=gemini-flash-latest bash scripts/e2e_live.sh
set -u
cd "$(dirname "$0")/.."
. "$(dirname "$0")/_viz.sh"   # colored ok/fail/has/hasnt/section/result

# --- resolve the key (env wins, else read it from .env) --------------------
envval() { [ -f .env ] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '"'"'"' ' || true; }
# accept GOOGLE_API_KEY or GEMINI_API_KEY (env first, then .env)
KEY="${GOOGLE_API_KEY:-${GEMINI_API_KEY:-}}"
[ -z "$KEY" ] && KEY="$(envval GOOGLE_API_KEY)"
[ -z "$KEY" ] && KEY="$(envval GEMINI_API_KEY)"
MODEL="${AGENT_MODEL:-$(envval AGENT_MODEL)}"
MODEL="${MODEL:-gemini-flash-latest}"

if [ -z "$KEY" ]; then
  cat <<'MSG'
⏭️  SKIPPED — no GOOGLE_API_KEY found (env or .env).
   The Gemini live e2e needs a real key. To run it:
     1) put  GOOGLE_API_KEY=...   in .env   (or export it)
     2) (optional) AGENT_MODEL=gemini-flash-latest   # any valid Gemini model id
     3) bash scripts/e2e_live.sh
   The stub-backed full e2e (no key) is:  bash scripts/e2e.sh
MSG
  exit 2
fi

FAILS=0
AG=http://127.0.0.1:8003; SA=http://127.0.0.1:8004
J=(-H "Content-Type: application/json")

section "bring up stack with the Gemini planner (model: $MODEL)"
docker compose down >/dev/null 2>&1 || true
# Inject the LLM backend for this run on top of the shared .env.
AGENT_LLM_BACKEND=gemini AGENT_MODEL="$MODEL" GOOGLE_API_KEY="$KEY" \
  docker compose up --build -d datasets rag control-plane agent-engine studio-api \
  || { echo "compose up failed"; exit 1; }

for _ in $(seq 1 40); do
  st=$(docker inspect --format '{{.State.Health.Status}}' valuegraph-platform-studio-api-1 2>/dev/null || echo none)
  [ "$st" = healthy ] && break; sleep 2
done

section "agent-engine is on the gemini backend"
INFO=$(curl -s $AG/agent/info)
has "info reports llm_backend=gemini" "$INFO" '"llm_backend":"gemini"'
echo "    $INFO"

section "studio-api chat — full product chain on the LLM (grounded)"
SH=(-H "X-Service-Token: dev-service-token" -H "X-User-Email: live@user.com")
for _ in $(seq 1 20); do [ "$(curl -s -o /dev/null -w '%{http_code}' $SA/health)" = 200 ] && break; sleep 2; done

# ask() -> sets TOOL / STATUS / REFUSED / CITES (sources) / ANSWER from the SSE
ask() {
  local out; out=$(curl -s "${SH[@]}" "${J[@]}" -X POST $SA/chat/stream \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$1\"}]}")
  python3 - "$out" <<'PY'
import sys, json
tool=status=refused=None; ans=[]; cites=[]
for line in sys.argv[1].splitlines():
    line=line.strip()
    if not line.startswith("data:"): continue
    try: ev=json.loads(line[5:])
    except Exception: continue
    t=ev.get("type")
    if t=="tool": tool=ev["name"]
    elif t=="tool_result": status=ev["status"]
    elif t=="citation": cites.append(ev.get("source") or "")
    elif t=="token": ans.append(ev.get("text",""))
    elif t=="done": refused=ev.get("refused")
print("TOOL="+str(tool)); print("STATUS="+str(status)); print("REFUSED="+str(refused))
print("CITES="+ ",".join(c for c in cites if c))
print("ANSWER="+ " ".join("".join(ans).split()))
PY
}

# rigorous grounding check: the LLM must call a real tool (200), cite the expected
# source, and put an actual NUMBER from that data into a substantive answer — not a
# generic or canned reply.
assert_grounded() {  # $1 label  $2 raw-ask-output  $3 expected source  $4 expected connector substr
  local R="$2" lbl="$1" src="$3" conn="$4"
  local TOOL STATUS CITES ANSWER
  TOOL=$(printf '%s' "$R" | sed -n 's/^TOOL=//p'); STATUS=$(printf '%s' "$R" | sed -n 's/^STATUS=//p')
  CITES=$(printf '%s' "$R" | sed -n 's/^CITES=//p'); ANSWER=$(printf '%s' "$R" | sed -n 's/^ANSWER=//p')
  echo "    [$lbl] tool=$TOOL status=$STATUS cites=$CITES"
  echo "    [$lbl] answer: $(printf '%s' "$ANSWER" | head -c 220)"
  has   "$lbl: called the expected connector ($conn)"   "$TOOL"   "$conn"
  has   "$lbl: tool returned 200"                        "$STATUS" '200'
  has   "$lbl: cites $src"                               "$CITES"  "$src"
  printf '%s' "$ANSWER" | grep -Eq '[0-9]' && ok "$lbl: answer contains a real figure" || fail "$lbl: no figure in answer"
  [ "${#ANSWER}" -ge 40 ] && ok "$lbl: answer is substantive (>=40 chars)" || fail "$lbl: answer too short (${#ANSWER})"
  hasnt "$lbl: not the stub canned line" "$ANSWER" '로 데이터를 가져왔습니다'
}

# US fundamentals — an explicit ticker the LLM resolves reliably
assert_grounded "US" "$(ask "What was Apple (AAPL) total revenue in its most recent annual report? Give the figure.")" \
  'SEC EDGAR' 'sec_edgar__'
# KR fundamentals — explicit KRX code so the tool hits real DART data
assert_grounded "KR" "$(ask "삼성전자(005930)의 가장 최근 연간 매출액을 숫자로 알려줘")" \
  'OpenDART' 'opendart__'

section "guardrail still holds on the LLM backend"
RF=$(ask "should I buy AAPL? will it go up?")
has "forecast/advice refused (refused=true)" "$RF" 'REFUSED=True'

section "teardown"
docker compose down >/dev/null 2>&1

result "LIVE (Gemini) E2E"
exit "$FAILS"
