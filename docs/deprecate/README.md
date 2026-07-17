# Deprecated docs

Docs that are **no longer the source of truth** — kept for reference/history only. Don't pull
tasks or guide new work from anything in this folder.

| File | What it was |
|---|---|
| `ROADMAP.md` | Old phased task tracker (PH hardening + U2–U6 + CE waves). Superseded by `docs/ROADMAP.md` (2026-07-03). |
| `UX_SPEC.md` | Old screen-by-screen UX spec ("research desk", standing analysts, etc.). Superseded by `docs/UX_SPEC.md`. |
| `DESIGN_SYSTEM.md` | Old web visual language / component templates derived from the wireframes. Superseded. |
| `wireframes/` | Old `.dc.html` wireframes + intent notes. Superseded. |
| `REFACTOR_PLAN.md` | Backend refactor task list (RF-01..18). **All 18 tasks complete** — retired 2026-07-12; kept as history of what moved where. |
| `FRONTEND_REFACTOR_PLAN.md` | Frontend refactor task list (FE-01..14). FE-01..06 done; the rest targeted since-removed surfaces (프롬프트 라이브러리) or flag-off surfaces (대시보드) — retired 2026-07-12. A future FE refactor should start from a fresh audit, not this list. |
| `ASK_SURFACE_SPEC.md` | "물어보기" 표면 개편 스펙 (M-ASK, ASK-1..10). **All shipped**; the entry surface was since superseded by ENTRY-v6 (5-섹션 탐구 홈) — retired 2026-07-17. Current contract = ROADMAP M-ASK row + code (`askfeed.py`, `CockpitEntry.tsx`). |
| `PUBLISH_SPEC.md` | 공유·팩트체크·노트 스펙. M-SHARE + QT-2 shipped; M-FACT removed (2026-07-05) and M-NOTE absorbed into the since-removed M-NB — retired 2026-07-17. Nothing pending. |
| `RAG_INGEST_BATCHING_SPEC.md` | RAG 인제스트 원자성·배치 개편 (ING-1). **Fully implemented + live-verified** (2026-07-12) — retired 2026-07-17. |
| `UX_PROPOSALS.md` | 수치 원장(LG-1..5) + 관제탑 엔트리(ENT-1..5) 제안. Both shipped (all ✅) and the entry proposal superseded by ENTRY-v4/v6 — retired 2026-07-17. |

Still current: see the **docs map in the repo-root `CLAUDE.md`** — the active set lives in `docs/`
(`ROADMAP.md`, `ARCHITECTURE.md`, `UX_SPEC.md`, `HISTORY_LAB_SPEC.md`,
`NOTEBOOK_SPEC.md`(§B 스탠딩 알림만 유효), `VIRAL_SPEC.md`, `QUALITY_SPEC.md`,
`IDEA.md`, `DATA_EXPANSION.md`, `USER_TODO.md`).
