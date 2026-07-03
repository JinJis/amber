# DESIGN_SYSTEM.md — ValueGraph component templates

> Source of truth for the **web UI visual language**, derived 1:1 from the user's
> wireframe (`docs/wireframes/app-map.dc.html`, open it with `wireframes/support.js`; intent in
> `docs/wireframes/chat-1-app-map.md`). The wireframe is **mid-fidelity light grayscale**: white
> cards on a light gray page, near-black ink for actions, and **the freshness/trust
> signals are the only saturated color**. Implemented in `web/app/globals.css` `:root`
> + component classes; every web component consumes these tokens — **don't hardcode
> hex in components** (the one exception is `ArtifactCard`'s SVG stroke palette, which
> needs literal colors for `<path stroke>`).

## 1. Principles (from the wireframe)
1. **Grayscale by default, trust = the only color.** Black→gray→white everywhere;
   saturated hue appears *only* in freshness dots (🟢🟡🔴⚪) and the calm indigo accent
   used for citations/@groups.
2. **Every artifact card ends in a trust line** — `출처 · as_of · 신선도`. Non-negotiable.
3. **The guardrail is a visible label, not fine print.** Amber callout on the Live feed,
   the builder, news cards, and on any refused turn.
4. **Two typefaces.** `Space Grotesk` for UI; `Space Mono` for every number, ticker,
   timestamp, code, and `[n]` citation marker. (`Caveat` is the wireframe's *designer
   annotation* hand — it is **not** a product typeface; do not ship it in the app UI.)
5. **Hairlines, soft radius, soft shadow.** 1px borders, 9–12px card radius, the two
   shadow recipes below. No heavy chrome.

## 2. Tokens (`globals.css :root`)
| Token | Value | Use |
|---|---|---|
| `--bg` | `#E9E9EB` | app backdrop (behind the shell) |
| `--panel` | `#ffffff` | primary card / surface |
| `--panel-2` | `#FAFAFB` | subtle inset (rails, chat scroll, raised insets) |
| `--panel-3` | `#FCFCFD` | faintest surface |
| `--text` | `#26262A` | primary text |
| `--text-2` | `#4A4A50` | secondary body |
| `--muted` | `#86868C` | meta / labels |
| `--faint` | `#A6A6AC` | placeholder / faintest |
| `--line` | `#E4E4E7` | default hairline |
| `--line-2` | `#D8D8DC` | stronger card outline |
| `--line-3` | `#F0F0F2` | faintest divider |
| `--ink` / `--paper` | `#1A1B1E` | near-black: primary action bg, active rail, dark accents |
| `--on-ink` | `#ffffff` | text on ink |
| `--focus` | `#1A1B1E` | active input border |
| `--accent` | `#6E72B0` | citations `[n]`, @group, links (calm indigo, used sparingly) |
| `--accent-bg` / `--accent-fg` | `#EEF0F6` / `#5A5E86` | @group chips |
| `--fresh` / `--aging` / `--stale` / `--gap` | `#1FA463` / `#D9A300` / `#D1483A` / `#A6A6AC` | freshness dots — the only saturated colors |
| `--warn-fg` / `--warn-bg` / `--warn-line` | `#8A6A00` / `#FBF3DA` / `#EFE0AE` | guardrail label |
| `--mark` | `#FCEFC4` | verbatim highlight inside filing snippets |
| `--ui` | `Space Grotesk, …` | UI font |
| `--mono` | `Space Mono, …` | numbers/tickers/timestamps/code |
| `--shadow-card` | `0 1px 2px rgba(0,0,0,.04), 0 6px 20px rgba(0,0,0,.04)` | cards |
| `--shadow-pop` | `0 8px 26px rgba(0,0,0,.06)` | modals / popovers |

## 3. Component templates (class → wireframe section)
- **App shell** `.shell` (rail · main · rightpane) — wireframe **A**. Rail = 6 returning
  destinations; right pane = Live context with the guardrail label always pinned.
- **Message bubbles** `.msg .bubble` — wireframe A/B. User bubble = **ink** (`--ink` bg,
  light text); assistant = white card with hairline. `[n]` markers (`.anch`) = `--accent`.
- **Inline artifact card** `.artifact` — wireframe A/B/D. Title row (freshness dot + 차트/표
  · ↻ · 📌), SVG chart, then the **trust line** (`출처 · as_of · 신선도`, drawn gaps).
- **Live Context source previews** `.srcprev` (`.filing`/`.web`/`.data`) — wireframe
  "화면 상세" Live panel. **Live Context shows only *evidence*** — the sources that actually
  produced the answer (cited `[n]` or backing an artifact); consulted-but-unused sources
  stay in the message's 도구·출처 list. Each renders in its **native form** with the cited
  passage highlighted (`--mark` + amber rule): filing → a mini PDF page (page badge +
  highlighted line over skeleton text), web/news → browser chrome (URL bar from the real
  host) + headline + highlighted phrase, data/metric → an **extracted-data table**
  (`.sp-table`, cited row highlighted) + computation + the **canonical filing link** (SEC
  index page / DART rcpNo — never a bare directory listing). Surrounding text is skeleton
  (we hold only the snippet + a link — no full-text redistribution). Clicking opens `SourceViewer`.
- **Source viewer** `.sv-*` (`SourceViewer.tsx`) — wireframe **Screen 08**. A preview
  expands into a modal: the source full-size with the passage highlighted + a margin pin,
  and a right "이 원문을 인용한 곳" panel (freshness · as_of · source) with 원문 열기 ↗ /
  인용 복사. Honest: renders the extracted passage we hold, never a fabricated document.
- **Trust legend** `.legend` — wireframe C. One legend, reused wherever a dot appears.
  Shows **freshness** (computed). Confidence tiers (verified/derived/estimated) are the
  *spec intent* (wireframe C) but are **not rendered live** until the backend emits a
  `confidence` field — we don't fake it (honesty-over-fake-data).
- **Guardrail label** `.guard` — wireframe C/E/G. Amber callout; shown on the Live feed,
  the builder, news cards, and on any **refused** turn (the `done` SSE `refused` flag).
- **Board** `.board` / `.board-grid` — wireframe **D**. Pinned artifacts; each remembers
  tool+args and re-fetches a fresh `as_of` on open.
- **Builder modal** `.modal` + `.src-row`/`.tool-list` — wireframe **E**. Data-source rows
  expand to reveal the tools inside (transparency); guardrail label is always present.
- **Watchlists / @groups** `.wl-*` — wireframe **F**. Search → ⭐ favorite into a named
  `@handle` group; that handle is the unit the composer and builder bind to.
- **Prompt library** `.tabs`/`.prompt-card` — mine ↔ community, clone-import pattern.

## 4. Primitive library — `web/components/ui.tsx`
One module owns the recurring patterns; every screen composes these instead of
re-deriving markup/classes. This is what keeps the language unified.

| Primitive | API | Used by |
|---|---|---|
| `Button` | `variant: primary\|ghost\|danger`, `size: md\|sm` | composer, header, builder, prompt lib, watchlists |
| `Chip` | `tone: default\|accent\|ink`, `dot?`, `onClick?` | composer @groups, builder tags |
| `Card` | `head?`, `foot?`, `elevated?` | generic surface (artifact/source cards keep bespoke layout) |
| `FreshnessDot` / `TrustLegend` | `f` ∈ fresh/aging/stale/gap | everywhere a figure appears (single source) |
| `GuardrailLabel` | `icon?`, children | refused turns, builder, (Live feed uses `.live-label`) |
| `Mascot` | `size?` | rail brand, header, watchlists, builder |
| `Modal` | `title`, `onClose`, `wide?`, `footer?` | builder, prompt library, watchlists |

Tokens live in `globals.css :root`; primitives own the structural classNames
(`.btn`, `.chip-ui`, `.card-ui`, `.modal`, `.fdot`, `.guard`, …) that consume them.
`SourceCard`/`ArtifactCard` import their trust primitives from `ui` so there is exactly
one `FreshnessDot`.

## 5. Rules
- Build new screens by composing `ui.tsx` primitives; reach for raw markup only for
  genuinely bespoke layout (charts, the source-card C internals).
- Consume tokens; never hardcode hex in components (except SVG strokes).
- Numbers, tickers, timestamps, `[n]` → `.mono`.
- Any user-visible figure shows source + as_of + freshness.
- Guardrail label is visible, never hidden behind a tooltip.
- Korean user-facing strings stay inline for now (i18n layer is a later task).
