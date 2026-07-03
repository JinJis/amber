# Quality evaluation framework

Goes beyond pass/fail wiring tests: it grades whether the platform actually does
the **user-desired thing** — a user builds an agent with chosen data sources, asks
a question, and the agent fetches from the *right* source (MCP / data plane / RAG),
grounds the answer in real numbers, cites the source, respects its data-source
restrictions, and refuses out-of-scope (forecast/advice) requests.

It drives the **real product path** for every scenario:

```
provision user → POST /agents (chosen data_sources) → POST /chat/stream → parse SSE → grade
```

## Run

Needs the stack up (Gemini backend for real natural-language answers):

```bash
docker compose up -d                 # AGENT_LLM_BACKEND=gemini in .env, GOOGLE_API_KEY set
python3 eval/run_eval.py             # stdlib only — no extra deps
```

Env: `STUDIO_URL` (`:8004`), `RAG_URL` (`:8002`), `SERVICE_TOKEN`, `EVAL_USER`,
`GOOGLE_API_KEY` (enables the judge), `EVAL_JUDGE_MODEL` (the judge model — default
fast `gemini-flash-latest`; set `gemini-pro-latest` for the strongest/slower grading),
`EVAL_JUDGE_BAR` (pass threshold, default 3.8), `EVAL_TODAY`.

## What it checks

Per scenario (`scenarios.py`), each present check is one graded item:

| check | meaning |
|---|---|
| `expect_connector` | the agent called the right tool (e.g. `sec_edgar__`, `ecos__`, `rag__search`) |
| `forbid_connectors` | a restricted agent never reached a disallowed source (e.g. SEC-only agent ≠ yahoo) |
| `expect_status` | the tool call returned 200 (real data, not an error) |
| `expect_cite` | the answer cites the right source (per-chunk for RAG) |
| `answer_regex` / `answer_contains` | the answer is **grounded** — contains the real figure / fact |
| `expect_refused` | guardrail refuses forecast/advice (EN + KR) |
| `expect_artifact` | an inline artifact (U3) was emitted — a `kind` (e.g. `"timeseries"`) or `True` for any |
| `expect_computation` | a self-computed figure (valuation/backtest/screener) carries its **계산 근거** trace (PH-DATA-6) |
| `expect_cite_url` | a citation carries an **external source-page URL** (the in-app viewer renders it) — a host substring or `True` |
| `judge` | **deep-model rubric judge** — scores 5 dimensions 1–5 (see [`RUBRIC.md`](./RUBRIC.md)) |

Each judged scenario also carries a one-line **`criteria`** ("what a correct answer to THIS question
must do"), fed to the judge on top of the global rubric.

**Pass bar:** every deterministic check passes **and** judge `overall` average ≥ `EVAL_JUDGE_BAR`
(default 3.8). The summary prints **per-dimension averages** (`sourcing · relevance · grounding ·
guardrail · clarity`) so you can see where quality is weak. Full rubric: [`RUBRIC.md`](./RUBRIC.md).

A scenario may use `question` (single turn) or `turns` (a multi-turn conversation —
the driver feeds each real assistant answer back in and grades the last turn).

The LLM-judge is told today's date and that figures are live ground truth from
primary sources, so it grades *relevance/specificity/tone* — it does **not**
fact-check fresh numbers (a judge's training cutoff can't, and would wrongly flag
2025/2026 data as "future").

## Scenarios (current — 17)

US fundamentals → SEC EDGAR · KR fundamentals → OpenDART · US prices → Yahoo ·
KR prices → Yahoo (.KS) · macro → Bank of Korea ECOS · news → Google News ·
filings → SEC EDGAR · insider trades → SEC EDGAR (Form 4) · RAG retrieval → cited
disclosure · valuation metrics → financial-metrics · multi-company comparison ·
**honesty: no-data → say so, don't fabricate** · data-source restriction honoured ·
guardrail refusal (KR + EN) · **multi-turn** follow-up inherits the company (KR + US).

**계산 근거 (PH-DATA-6) — self-computed figures expose their derivation:** valuation DCF/DDM/RIM ·
"how was it computed" transparency · KR valuation · assumption-sensitivity · multi-turn re-compute ·
backtest (보유·기간·지표) · quant screener (필터·팩터 공식). **Source-page viewer — sourced figures
carry a viewable external URL:** core CPI / unemployment / treasury / PCE / euro HICP series pages
(BLS · DBnomics) · ECOS rate · news article · SEC filing (재무·13F·Form 4) · DART filing.

**Add a scenario (with `criteria`) for every new tool / endpoint / feature**, and run the eval before
pushing — that's how the bar ratchets up (Definition of Done, `../CLAUDE.md` §5).

## Found-and-fixed (the framework earns its keep)

The first run surfaced three real product bugs, since fixed:

1. **Macro misrouting** — a KR-only connector tool (ECOS) called without `market`
   re-routed through the gateway to the US connector (FRED) → 400. Fixed: the agent
   tool client forces `market` for single-market connectors.
2. **Generic RAG citation** — answers cited "Platform RAG" instead of each passage's
   real source. Fixed: per-chunk provenance citations.
3. **English-only guardrail** — Korean forecast/advice ("오를까? 사야 할까?") slipped
   through. Fixed: Korean refusal patterns.

Each fix has a regression test in `agent-engine/tests/test_agent.py`.
