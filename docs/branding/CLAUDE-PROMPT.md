# Claude Code brief: integrate the finnote brand

## Do not redesign the logo

The logo is final. The SVG files in this directory and the React component in
`Logo.tsx` are the source of truth. Do not invent a new mark, do not
"improve" the paths, do not generate alternatives. Your job is placement,
not design.

If something looks wrong at a given size, adjust size or spacing. Never
edit the path data.

## What finnote is

finnote = **finance + footnote**. An AI investment research product for Korean
retail investors. It reads DART and SEC filings and earnings call transcripts,
answers questions, and always shows the highlighted source passage next to the
answer. Answers come second; the footnote comes first.

The single promise is provenance: no claim without visible evidence.

The logo encodes this. It is a **fin above the waterline** — the wave line
runs on as a price chart and springs up at the end, where a yellow arrowhead
points to the rise. The shark's body is never drawn: the service reads what is
below the surface for you. So the fin is clipped at the waterline, and the wave
covers the cut. The yellow of the arrowhead is the **same yellow as the
footnote chips** in the product — that is the only place yellow is allowed.

## Files to place

```
finnote-mark.svg        primary mark, full color (fin + wave + arrowhead)
finnote-mark-mono.svg   single color, uses currentColor (fin only)
finnote-icon.svg        512x512 app icon on dark square
favicon.svg             compact mark (one wave node), survives 16px
Logo.tsx                React component: mark | lockup | compact | mono | invert | app
brand.css               color and type tokens
```

## Tasks

1. Copy `brand.css` into the global stylesheet and import it before any
   component styles. Every color in the app comes from these tokens. Do not
   hardcode hex values anywhere else.

2. Copy `Logo.tsx` into the components directory. Use `<Logo />` in the
   header (lockup, size 28), `<Logo variant="compact" />` under 32px, and
   `<Logo variant="invert" />` on a deep-sea (dark) background.

3. Wire up the favicon and app icons:

   - `favicon.svg` as the SVG favicon
   - Generate PNG fallbacks at 32, 180 (apple-touch-icon), 192, 512 from
     `finnote-icon.svg`
   - Add a web manifest with `theme_color: #0E2A3F` and
     `background_color: #FFFCF6`

4. Build an OG image at 1200x630: deep-sea `#0A2133` background, the wordmark
   in `--font-brand` (Nunito 800, "fin" in ocean, "note" in paper) with a
   single waterline beneath it. No tagline, no gradient.

5. Apply `.evidence-highlight` from `brand.css` to any cited source span in
   the answer UI. This class is the product — the yellow highlighter on paper.
   Do not restyle it with a generic yellow, and never spend yellow on anything
   that is not evidence.

## Hard constraints

- Fonts by role: **Nunito** (logo/wordmark only), **Pretendard** (UI + answer +
  Korean body), **Noto Serif KR** (원문 인용 — quoted source text), **mono**
  (numbers + metadata). Never set headings in Nunito; the answer and the source
  must be told apart by typeface.
- The background is **paper `#FFFCF6`**, not white. The app should read as a
  document, not a dashboard.
- No gradients, no drop shadows except on things that actually float
  (popovers/sheets), no glassmorphism, no glow.
- Dark mode is the deep sea (`#0A2133`). Ocean 700 becomes aqua 300; the
  highlighter yellow stays exactly the same — evidence color is mode-invariant.
- The mark must be legible at 16px. If it is not, use `favicon.svg` /
  `variant="compact"` at that size.
- Never letterspace or restyle the wordmark. It is lowercase, always.
- Market colors follow the **viewed market**, not a user setting: KR is red-up /
  blue-down, US is green-up / red-down (`[data-market]`). Do not use up/down
  color as decoration.

## Accessibility floor

- The `<Logo />` component already carries `role="img"` and `aria-label`.
  Do not add a redundant alt.
- `.evidence-highlight` must not rely on color alone. The bottom seal (inset
  underline) is load-bearing. Keep it.
- Respect `prefers-reduced-motion` on any logo animation. The loading fin swims
  across the waterline; when motion is reduced it rests, it does not spin.
