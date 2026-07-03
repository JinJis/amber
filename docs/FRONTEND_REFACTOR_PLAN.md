# Frontend Refactoring Plan (FE-01 … FE-14)

> **Goal:** reduce structural debt in the `web/` Next.js (App Router) frontend **without changing
> behavior or UI**. Pure structure/maintainability work.
>
> **⚠️ No test suite.** The frontend has no test runner (devDeps are `typescript` + `@types/*` only).
> So each task's verification is **`npx tsc --noEmit` clean + `next build` succeeds + a live smoke**
> of the affected surface — not unit tests. Adding a runner (vitest + testing-library) is **FE-14**,
> optional/foundational. Refactor carefully: there is no safety net beyond the type-checker + build.
>
> **Process:** one task per PR/commit; **tag the `FE-id`**. Pull tasks in phase order (0→1→2→3).
> Update the **Status** column in the same change. Derived from a 5-area parallel audit (2026-06-25).

## Guardrails — refactors must PRESERVE these (CLAUDE.md §4 + UI invariants)

- **Read `/mnt/skills/public/frontend-design/SKILL.md` before any visual change.** This effort is
  structure-only: **no UI/visual/behavior change** (same DOM output, same styles, same interactions).
- **Browser holds only an Auth.js session.** Keys/tenant-key stay server-side; the BFF (`/api/*`)
  re-derives identity from the session and proxies to studio-api with the service token + user email.
  Never trust a client-supplied user id.
- **No `localStorage`/`sessionStorage`** in preview/artifact contexts.
- **Charts stay canvas** (`lightweight-charts`) — never render the graph with DOM nodes.
- **Provenance is never dropped** — every figure shows `source`+`as_of`+`freshness`+`cadence`.
- **Only `periodic` widgets (`cadence != one_shot`) carry a bell**; the alert trigger is **derived from
  cadence/category metadata, never a name-regex** (invariant #9). `lib/alerts.ts` already honors this.
- **Gemini-only** (the builder exposes no model choice); **no forecasting**; the **guardrail refusal
  label stays shown**; the builder groups tools by **catalog category**, selecting individual tools.

## Status legend

`TODO` · `WIP` · `DONE` · `BLOCKED` · `DROPPED`. Sev = maintainability impact. Effort: S/M/L.

---

## Phase 0 — Shared foundations (low-risk, high-leverage; do first)

| ID | Task | Sev | Eff | Status |
|---|---|---|---|---|
| **FE-01** | Created `lib/types.ts` as the single source: `Artifact` (+ chart child types), `Citation`, the chat/SSE types (`ToolUse`/`Think`/`Clarify`/`ClarifyOption`/`SubAgent`/`Msg`), and `WidgetSpec`. Moved the defs out of `ArtifactCard`/`SourceCard`/`Chat` (importing a *type* no longer drags in a *component*); the components **re-export** them for back-compat → zero importer churn. **`BoardCanvas`'s `spec: any` → `WidgetSpec` deferred to FE-10** (the union needs narrowing at every `spec.x` access — a cascade best done with the WidgetFrame split). tsc clean (1 known react-grid-layout baseline error) + `next build` passes + page renders. | high | M | DONE |
| **FE-02** | Created `lib/format.ts` (`currencyOf`/`fmtBig`/`fmtPrice`/`fmtVol`/`fmt`). **Audit overstated the duplication** — these weren't copied across files; they lived in `ArtifactCard` and `TradeChart` *imported `fmtBig` from it*, coupling a chart to a card module. Moved them to lib; `ArtifactCard` + `TradeChart` now import from `lib/format` (TradeChart fully decoupled from ArtifactCard — types→`lib/types`, format→`lib/format`). `BoardCanvas` doesn't use them. tsc clean + `next build` passes. | med | S | DONE |
| **FE-03** | Created `lib/widgets.ts` (`widgetKind(spec)` — the artifact/source/text classifier from `BoardCanvas.kindOf` — + coarse `widgetKindLabel`); migrated `BoardCanvas` and `PinPicker` onto it. **Removed the real duplicate:** `ui.tsx`'s `CADENCE_LABEL` (byte-identical to `lib/alerts`, its own comment admitted "mirrors") → `CadenceTag` now uses `cadenceLabel` from `lib/alerts` (no cycle — `lib/alerts` imports nothing). Left `WidgetGallery.kindLabel` alone (a *different* concept — the artifact's fine-grained display type 차트/표·값/내러티브). tsc clean + `next build` passes. | med | S | DONE |

## Phase 1 — Cross-cutting hooks + the BFF/modal primitives

| ID | Task | Sev | Eff | Status |
|---|---|---|---|---|
| **FE-04** | Created `lib/hooks.ts` (`useFetch<T>`, `useAsyncOp`, `useDebounce`). Migrated `PromptLibrary`'s 3 mutations (create/remove/import) onto `useAsyncOp` (`setBusy(true);try{}finally{setBusy(false)}` → `run(fn)` — identical busy semantics, now also captures errors). **Other modals adopt the hooks incrementally as Phase 2 touches them** — kept FE-04 safe: `Watchlists`'s search debounce is deliberately NOT migrated yet (its spinner shows *immediately* on keystroke vs after the debounce settles, a spinner-timing nuance that's risky to rewrite without a test net). tsc clean + `next build` passes. | high | M | DONE |
| **FE-05** | Added `streamStudioEvents(path, init)` to `lib/studio.ts` — the streaming twin of `proxyStudio`, reusing `studioFetch` for auth + the service-token/email headers. The two SSE routes (`api/chat`, `api/runs/[id]/stream`) — the only ones that hand-rolled the auth+fetch+pipe — now delegate to it (≈30 → 9–11 lines each); `SSE_HEADERS` centralized. The error path now guards consistently (chat gained the upstream-ok check; happy path identical). Live-verified: both routes 401 without a session (auth preserved), page 200. *(The audit's "40× BFF copy-paste" was wrong — the ~38 JSON routes already use `proxyStudio`.)* | med | S | DONE |
| **FE-06** | Gave `ui.tsx`'s `Modal` a `className` prop (the reason AlertSheet/WidgetGallery had re-implemented the shell — they needed `alert-sheet`/`widget-gallery` on the modal div) + a11y (`role="dialog"` · `aria-modal` · `aria-label` · **Escape-to-close**). Migrated `AlertSheet` + `WidgetGallery` onto `<Modal className=… title=… footer=…>` — identical DOM (backdrop > modal{class} > head > children > foot), additive a11y. **Focus-trap (Tab cycling) deferred to FE-13** (more edge-cases, riskier without tests). tsc clean + `next build` + page 200. | med | M | DONE |

## Phase 2 — God-component splits

| ID | Task | Sev | Eff | Status |
|---|---|---|---|---|
| **FE-07** | `Chat.tsx` (797): extract `useChatStream` — the SSE reader + the `applyEvent` reducer + conversation tracking (replace the `viewConvRef`/`streamConvRef` dance with an `AbortController` per stream so fast conversation-switches can't orphan a reader). | high | L | TODO |
| **FE-08** | `Chat.tsx`: extract `<MessageList>` (memoized per-message sub-components so a `token` event doesn't re-render the whole list) + `<ChatComposer>` (input/@mention/ticker-fill state). Chat drops to a thin shell. | high | M | TODO |
| **FE-09** | `ArtifactCard.tsx` (458): replace the per-`kind` if-chain (candlestick/timeseries/table/kpi/narrative) with a `kind → renderer` registry (mirrors the backend RF-08 artifact handlers). Extract `ResponsiveTable` to its own file. | med | M | TODO |
| **FE-10** | `BoardCanvas.tsx` (394): extract `<WidgetFrame>` (the per-widget chrome: drag handle · freshness dot · cadence tag · bell-gate · refresh/delete) so BoardCanvas keeps only grid+DnD+pin-CRUD; memoize `toLayout(items)`; derive the spec fields once (not per render). | high | M | TODO |
| **FE-11** | `TradeChart.tsx` (441): split the 360-line `useEffect` into focused effects (data load · range/scale · drawing) and extract `useChartDrawing`; stabilize the unstable `a` (artifact) dependency so the chart isn't recreated every render. Cleanup (ResizeObserver + `chart.remove`) preserved. | med | M | TODO |

## Phase 3 — Provenance · a11y · tests

| ID | Task | Sev | Eff | Status |
|---|---|---|---|---|
| **FE-12** | `<ProvenanceFooter source as_of freshness cadence category>` shared component, composed by `ArtifactCard` / `SourceCard` / `FilingViewer` so the four trust signals render the same way everywhere (today they're split across headers/footers/tags). | high | M | TODO |
| **FE-13** | a11y pass: `SourceCard`'s `role="button"` div gets `tabIndex` + key handlers; grid widget toolbar buttons + drag/resize handles get `aria-label`s; opaque glyphs (⠿/✕/↻/🔔) get accessible names. (Modal a11y lands in FE-06.) | med | M | TODO |
| **FE-14** | *(optional, foundational)* add a test runner (vitest + @testing-library/react) and seed the high-value tests the audit wanted: TradeChart create/teardown lifecycle, FilingViewer highlight fallback chain, `toLayout` collision/compaction, and SSE-event parsing. | med | L | TODO |

---

## Deferred / rejected (value vs. risk)

- **BFF route factory / 40× dedup** — overstated: `proxyStudio`/`studioFetch` already exist; only the
  2 SSE routes need work (FE-05). No mass rewrite warranted.
- **`lib/alerts.ts` regex removal** — there is no regex; the cadence→trigger derivation is already
  table-driven (invariant #9 honored). Nothing to do.
- **Board state context / global store** — the modals don't currently mutate board state; prop-passing
  is fine. Revisit only if alerts gain inline-edit. No state library (Redux/Zustand) needed.
- **zod request validation in BFF** — studio-api already validates; adding a client-side schema layer
  is net-new surface, not a refactor. Out of scope.

## Per-task Definition of Done

`npx tsc --noEmit` clean · `next build` (or `docker compose build web`) succeeds · the affected surface
live-smoked (same UI/behavior) · no `localStorage`/provenance/sandbox/Gemini-only invariant violated ·
this file's **Status** flipped to `DONE` in the same commit, tagged with the `FE-id`.
