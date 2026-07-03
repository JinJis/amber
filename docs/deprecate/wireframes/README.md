# Wireframes — design source bundle

User-authored mockups from Claude Design (claude.ai/design), kept as the **visual source of
truth** for the web UI. They are HTML/CSS/JS prototypes — recreate the *visual output*, not the
internal markup. The **implemented** system (tokens + `ui.tsx` primitive components) is
[`../DESIGN_SYSTEM.md`](../DESIGN_SYSTEM.md); build against that. Screen → milestone mapping is in
[`../ROADMAP.md`](../ROADMAP.md) and [`../UX_SPEC.md`](../UX_SPEC.md) §5.

To view a `.dc.html`: open it in a browser — each loads `./support.js` (the shared Claude-Design
runtime) from this folder. *Don't render to verify dimensions — read the inline CSS; it's exact.*

## Files
| File | What it is |
|---|---|
| `app-map.dc.html` | The original **app map** — IA & shell, 6 destinations, Pull→Push loop, trust legend, sections **A–I** (the `wireframe A/B/C…` labels in `DESIGN_SYSTEM.md` refer to these). |
| `screens.dc.html` | **7 full-size screens + the source viewer** — Desk · Board · Analysts+Builder · Watchlist · Briefs · Gallery · Onboarding · **Screen 08 = preview→full source viewer**. The detailed spec for `U-SHELL-POLISH`/U4/U5/U0. |
| `community.dc.html` | **Community / Insights (U6)** — capability review → feed · composer · reader · author profile · scrapbook · data hub. *(Lowest priority.)* |
| `support.js` | Shared Claude-Design runtime the `.dc.html` files load. |
| `chat-1-app-map.md` | Transcript — building the app map (intent). |
| `chat-2-screens.md` | Transcript — full-size screens, the **Live Context source-preview redesign**, and the expand viewer (intent). |
| `scraps/` | Reference captures: `livectx.png` (Live Context previews), `source-viewer.png`. |

## Consolidation notes
- `community.dc.html` is the canonical community file. An earlier narrower draft
  (`커뮤니티 & 확장`, 5 sections) was **removed** — fully covered by this one (7 sections).
- `app-map.dc.html` (overview) and `screens.dc.html` (full-size) are complementary, not duplicates:
  the map is the IA/philosophy, the screens are the per-screen detail.
