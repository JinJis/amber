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
| **verdict fit** *(fact-check turns — folded into grounding/guardrail, not a separately-scored dimension)* | A fact-check verdict must MATCH the evidence presented: a confident verdict (사실/사실과 다름) on thin or uncited evidence fails **grounding**; a future claim is judged 미래 주장(검증 불가) — scoring its likelihood fails **guardrail**. (The runner scores five dimensions — sourcing/relevance/grounding/guardrail/clarity; this row guides how the middle two apply to fact-check turns.) |
| **guardrail** | States facts only — **no OUR-OWN price predictions, price targets, or buy/sell advice**; news framed as context. Reporting an **attributed third-party figure with its source** (analyst **consensus** EPS/revenue, company **guidance**) is descriptive data, **not** a violation — don't penalise it as a "forecast". |
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

## Non-chat scenarios (feeds — M-DESK · ASK-6)

Two feed `kind`s skip the chat turn and grade returned cards. `kind: "desk_feed"` optionally
seeds a watchlist for a dedicated eval user, `GET /desk-feed`. `kind: "ask_feed"` picks a home
marquee scope (`scope`: `news_feed` / `earnings_radar` / …), triggers that scope's refresh, then
reads it from `GET /ask-feed`. The rendered card list (`[kind] hook → "question"`) is judged on
the SAME five dimensions — `sourcing` (every data card cites), `grounding` (hooks state only cited
facts), `guardrail` (zero advice/forecast phrasing in hooks or questions) — plus deterministic
checks: `expect_min_cards`, `expect_card_kind`, `cards_all_cited` (every data card carries a
citation), **`cards_kind_diverse`** (data cards span ≥N distinct kinds — no 가격·공시만 반복).

The market-wide `ask_feed` sections are shared caches that a small eval universe can leave empty,
so those scenarios never set `expect_min_cards` — 0 cards is an honest gap that passes
`cards_all_cited`/`cards_kind_diverse`; whatever cards DO ship are held to the rubric.
