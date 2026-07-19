# Claude Code brief: integrate the Amber brand

## Do not redesign the logo

The logo is final. The SVG files in `assets/` and the React component in
`Logo.tsx` are the source of truth. Do not invent a new mark, do not
"improve" the paths, do not generate alternatives. Your job is placement,
not design.

If something looks wrong at a given size, adjust size or spacing. Never
edit the path data.

## What Amber is

An AI investment research product for Korean retail investors. It reads
DART and SEC filings and earnings call transcripts, answers questions, and
always shows the highlighted source passage next to the answer.

The single promise is provenance: no claim without visible evidence.

The logo encodes this. The nugget is amber, the only material that seals
something organic and keeps it visible and unaltered. The three bars inside
are lines of text. The middle bar is dark and short: that is the cited
evidence, sealed and legible.

## Files to place

```
assets/amber-mark.svg        primary mark, full color
assets/amber-mark-mono.svg   single color, uses currentColor
assets/amber-icon.svg        512x512 app icon on dark square
assets/favicon.svg           simplified, survives 16px
Logo.tsx                 React component: mark | lockup | mono
brand.css                color and type tokens
```

## Tasks

1. Copy `brand.css` into the global stylesheet and import it before any
   component styles. Every color in the app comes from these tokens. Do not
   hardcode hex values anywhere else.

2. Copy `Logo.tsx` into the components directory. Use `<Logo />` in the
   header (lockup, size 28), and `<Logo variant="mark" />` for any compact
   context.

3. Wire up the favicon and app icons:

   - `favicon.svg` as the SVG favicon
   - Generate PNG fallbacks at 32, 180 (apple-touch-icon), 192, 512 from
     `amber-icon.svg`
   - Add a web manifest with `theme_color: #412402` and
     `background_color: #FBF9F5`

4. Build an OG image at 1200x630: dark `#412402` background, mark centered
   at 240px, wordmark below in `--font-display`. No tagline, no gradient.

5. Apply `.evidence-highlight` from `brand.css` to any cited source span in
   the answer UI. This class is the product. It is the logo rendered in
   text. Do not restyle it with a generic yellow.

## Hard constraints

- Two fonts only: Inter Tight for display, Pretendard for body and Korean.
  Load Pretendard from a CDN, subset to Korean.
- No gradients, no drop shadows, no glassmorphism, no glow.
- Dark mode must work. Every color is already tokenized for it.
- The mark must be legible at 16px. If it is not, use `favicon.svg` instead
  of `amber-mark.svg` at that size.
- Never letterspace or restyle the wordmark. It is lowercase, always.
- No fintech clichés anywhere in the UI: no upward arrows, no bull imagery,
  no green-up red-down as a decorative motif.

## Accessibility floor

- The `<Logo />` component already carries `role="img"` and `aria-label`.
  Do not add a redundant alt.
- `.evidence-highlight` must not rely on color alone. The bottom border is
  load-bearing. Keep it.
- Respect `prefers-reduced-motion` on any logo animation. If you animate the
  mark on load, the bars should settle, not spin.
