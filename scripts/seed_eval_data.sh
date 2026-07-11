#!/usr/bin/env bash
# Seed ONLY the tiny fixed eval universe (US 3 · KR 3) + the History-Lab anchors.
# NEVER the full sp500/kospi boards — a big backfill saturates cloud-disk IOPS and stalls the host.
# (memory: eval-test-fresh-tiny-universe). Serial: one pipeline set at a time, wait for the queue.
set -euo pipefail
DS="${DATASETS_URL:-http://localhost:8000}"

echo "▶ seed: tiny eval universe (eval_us, eval_kr) — prices·financials·corp_actions·news·filing_text"
for preset in eval_us eval_kr; do
  curl -s -X POST "$DS/admin/pipelines/run" -H 'Content-Type: application/json' \
    -d "{\"preset\":\"$preset\",\"pipelines\":[\"prices\",\"financials\",\"corp_actions\",\"news\",\"filing_text\"]}" | head -c 160; echo
done

echo "▶ seed: History-Lab anchors (^GSPC …) — deep price backfill for drawdowns/base-rates"
curl -s -X POST "$DS/admin/pipelines/run" -H 'Content-Type: application/json' \
  -d '{"market":"US","tickers":["^GSPC","^IXIC","^KS11","^VIX","^TNX","KRW=X"],"pipelines":["prices"]}' | head -c 160; echo

echo "▶ waiting for the queue to drain…"
until curl -s "$DS/admin/queue" | python3 -c "import json,sys;t=json.load(sys.stdin)['totals'];sys.exit(0 if (t.get('todo',0)+t.get('doing',0))==0 else 1)" 2>/dev/null; do sleep 6; done
echo "✓ seed complete"
curl -s "$DS/admin/queue" | python3 -c "import json,sys;print('queue:',json.load(sys.stdin)['totals'])"
