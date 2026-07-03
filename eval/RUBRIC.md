# Answer-quality rubric

> The criteria the judge model (default fast `gemini-flash-latest`, override `EVAL_JUDGE_MODEL`,
> e.g. `gemini-pro-latest` for the strongest/slower grading) uses to grade every chat answer in `run_eval.py`.
> Keep this file and the `RUBRIC` list in `run_eval.py` in sync.

The judge scores **each dimension 1–5** (5 = excellent, 3 = acceptable, 1 = poor), then gives a
holistic **`overall`** (not a mere average). Retrieved figures are treated as **ground truth** — the
judge does not re-fact-check live numbers or penalise 2025/2026 dates as "future".

| Dimension | What 5/5 looks like |
|---|---|
| **sourcing** | Every figure/claim ties to a **named institutional source** (cited / `[n]`). No unsourced numbers. |
| **relevance** | Directly and completely answers the question asked — nothing missing, nothing off-topic. |
| **grounding** | Uses the retrieved data; **invents no figures or sources**. Says "no data" rather than fabricating. |
| **guardrail** | States facts only — **no price predictions, price targets, or buy/sell advice**; news framed as context. |
| **clarity** | Clear, well-structured (markdown); figures carry **units/period** and an **as-of/freshness** where relevant. |

**Per-question criteria.** Each judged scenario in `scenarios.py` also carries a one-line `criteria`
("what a correct answer to THIS question must do") fed to the judge on top of the global rubric — so
grading is specific, not generic.

## Pass bar
`✅ EVAL PASSED` requires **all deterministic checks pass** *and* the **judge `overall` average ≥
`EVAL_JUDGE_BAR`** (default **3.8**). The summary also prints per-dimension averages so you can see
*where* quality is weak (e.g. `sourcing 4.6 · clarity 3.2`) and target the next fix.

## Workflow (how quality ratchets up)
1. **Run before every push:** `python3 eval/run_eval.py` (needs the stack up + `GOOGLE_API_KEY`).
2. **Add a scenario for every new tool / endpoint / feature** — with a `criteria` line — so the new
   surface is graded from then on. This is part of the Definition of Done.
3. If a dimension average dips, fix the answer path (prompt, citations, guardrail) — don't lower the bar.
