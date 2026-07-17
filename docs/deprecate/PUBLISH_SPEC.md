# PUBLISH SPEC — 공유형 리서치 데스크 (M-SHARE · M-FACT · M-NOTE)

> Companion to [`ROADMAP.md`](./ROADMAP.md) v3. The desk today produces trustworthy answers
> **for the user**; this layer makes every answer **worth sharing** — to 텔레그램/카카오 정보방,
> Threads/X, and analyst notes — while carrying its provenance WITH it. UX conventions follow
> [`UX_SPEC.md`](./UX_SPEC.md); invariants (CLAUDE.md §2 + ROADMAP §2) apply unchanged.

---

## 1. Thesis — why sharing is the growth engine

In KR retail-finance communities the unit of currency is a **chart image with a claim**. Today
those images are unsourced screenshots ("받은 짤"); rumors spread faster than corrections. Our
whole stack — sourced artifacts, evidence highlights, History Lab, guardrails — is exactly what
those images lack. The product move:

1. **Every artifact becomes a share-grade image in one tap** — with the source · as_of ·
   (histor­ical) label **baked into the pixels**, plus a link back to the live, verifiable
   original. The provenance IS the watermark; the watermark IS the brand.
2. **Fact-check as a first-class flow** — paste a claim from a 정보방, get a cited verdict
   card. The most shareable object in any rumor-driven room is the receipt.
3. **Notes** — compose a conversation's artifacts + prose into a 1-page, report-grade image/PDF
   (the deferred Insight Canvas, reborn chat-first).

Growth loop: share card → viewer opens the **public read-only page** (no login) → "직접
확인해보기" CTA → sign-up → their first desk feed. Every shared image is an ad that proves the
product.

## 2. What we have vs what's missing (2026-07-04 review)

| Building block | Status | Gap for publish |
|---|---|---|
| Chart PNG export (title + sourced footer) | ✅ client-side (PH-VIZ-6) | client-only, no server render, no aspect presets, no share link, no OG page |
| Evidence highlight viewers (공시 원문) | ✅ in-app | can't capture a highlighted passage as a share card |
| History Lab artifacts (analogue/base_rates) | ✅ M1 | the single most shareable content we have — needs the share tap |
| Desk feed cards | ✅ M-DESK | "오늘의 데스크" 자체가 데일리 공유물 후보 |
| RAG (filings/news/transcripts/decks) | ✅ | fact-check needs a claim→verdict composition, not just search |
| Number audit (모든 숫자=툴 값) | ⬜ QT-2 | **prerequisite for share** — a wrong shared number is brand death |
| Public surface | ✗ none | everything requires login; no share tokens, no OG tags |
| Report composition (multi-artifact note) | ✗ none (Idea #3 deferred) | needed for "리포트에 들어갈 이미지" |
| Era news (HL-5) / transcript archive (EC-1) | ⬜ | share-worthy depth content |

## 3. M-SHARE — 공유 파이프라인

### 3.1 Architecture

```
artifact JSON ──POST /render (web, server-side)──▶ PNG/SVG (aspect preset, theme, watermark)
      │
      └─ POST /shares (studio-api) ──▶ ShareLink{token} ──▶ GET /s/{token} (web, PUBLIC)
                                                              ├─ OG image = the rendered PNG
                                                              └─ read-only artifact + "직접 확인" CTA
```

- **Server-side renderer** (`web` route `POST /api/render`, node runtime): renders a dedicated
  headless page (`/render/artifact?payload=…`) with Playwright-core/chromium already used by
  datasets evidence — or, simpler v1: reuse the CLIENT export path (the share tap happens in the
  browser; server render is only needed for OG images → generate the OG PNG at share-create time
  from the client and upload it with the share). **v1 decision: client-render, upload on create**
  — zero new infra; server render becomes v2 if programmatic/scheduled shares need it.
- **ShareLink model** (studio-api): `{token(pk, 22ch urlsafe), user_email, kind(artifact|verdict|
  note), payload(JSON snapshot), image_path?, title, created_at, revoked}`. The payload is a
  **snapshot** (immutable — a share never silently changes), with `as_of` visible; the public
  page shows "이 자료의 최신 버전 보기" that re-runs tool+args for logged-in users.
- **Public page** `web /s/[token]`: no auth (middleware allowlist); renders the artifact
  read-only (same components, `bare`), OG/Twitter meta pointing at the stored image, provenance
  footer + HistoricalLabel, CTA. Revoked → 410.
- **Privacy**: sharing is explicit (share tap → confirm sheet with preview). No account data on
  the page beyond an optional display name. Tokens unguessable; revocable from a "내 공유" list.

### 3.2 The share card (pixels)

Aspect presets: `1:1` (Threads/카톡), `4:5` (Threads tall), `16:9` (X/블로그), `A4-page` (M-NOTE).
Light theme only v1 (matches brand). Layout = artifact body + a **provenance strip** baked in:

```
┌────────────────────────────────────────────┐
│  (artifact: chart / base-rates table / …)  │
│                                            │
├────────────────────────────────────────────┤
│ ⏳ 과거 기록 · 전망 아님   (history kinds만)  │
│ 출처 SEC EDGAR · as of 2026-07-02          │
│ ValueGraph 로고 · vg.link/s/aB3… (QR 없음)  │
└────────────────────────────────────────────┘
```

Rules: the strip is **not removable**; history kinds always include the HistoricalLabel; numbers
on the image must have passed the number audit (§5). 텍스트 최소 12px(모바일 가독).

### 3.3 Tasks

- **SH-1 · Share snapshot + link (studio-api + BFF)** — ShareLink model/routes
  (`POST /shares`, `GET /shares`, `DELETE /shares/{token}` revoke, public `GET /shares/{token}`
  no-auth read), payload snapshot, rate limit (per-user/day). Tests: create/read/revoke/410,
  snapshot immutability, public route needs no service headers.
- **SH-2b · Aspect-preset card images + OG (web + studio-api)** — ✅ done. The share sheet renders
  the artifact into a grayscale PNG at 1:1 / 4:5 / 16:9 (`web/lib/shareCard.ts`, canvas 2D, no libs)
  with the **provenance strip baked in** (non-removable; history kinds carry '과거 기록 · 전망 아님';
  source · as_of · ValueGraph + short link). 저장 · 이미지 복사(clipboard); the 1:1 auto-uploads as the
  share's OG image (`PUT /shares/{token}/image`, base64 PNG stored on the share row — no volume), served
  publicly by `GET /shares/{token}/image` and used as `og:image`/`summary_large_image` on `/s/{token}`.
  Numbers are already QT-2-gated at share-create, so an image can't carry an unsupported figure.
- **SH-2 · Share tap + card composer (web)** — 공유 버튼 on every ArtifactCard/HistoryArtifact
  (＋대시보드 옆): opens a sheet → aspect preset preview (client-render to canvas, reusing the
  PH-VIZ-6 pipeline generalized beyond TradeChart: html-to-canvas for table/base_rates/analogue
  via SVG serialization) → "링크+이미지 만들기" → uploads PNG, creates ShareLink, copies link.
  Tests: strip always present, history label on history kinds, revoke hides.
- **SH-3 · Public share page (web)** — `/s/[token]` public route + OG/Twitter meta + read-only
  render + CTA + revoked/expired states. Middleware auth exception. Tests: no-auth fetch, OG
  tags, 410.
- **SH-4 · Evidence highlight card** — ✅ done. The SourceViewer side panel gains "이 문단 카드로 ↗":
  captures the user's TEXT selection (window.getSelection, ≥4 chars) or the cited snippet into a
  `quote` artifact {passage(verbatim), doc_title, source, as_of, url} → the existing share pipeline
  (studio-api kind `quote`, public page renders the blockquote). No screenshot — the passage is text,
  and it needs no number audit. The share card image renders the quote too.
  ORIGINAL: the evidence viewer gains "이 문단 카드로": captures the
  highlighted passage (text, not screenshot) into a quote-card artifact {passage, doc title,
  filing date, url} → same share pipeline. The 원문 인증짤. Tests: quote fidelity (verbatim),
  source fields mandatory.
- **SH-5 · 데스크 브리핑 카드** — ✅ done. "오늘의 데스크" 헤더에 "↗ 오늘 브리핑 공유": composes the
  day's top 3 SOURCED cards (nudge/continue excluded) into one artifact (kind reuses `table`: hooks +
  source column) → the share pipeline + the 1:1 card image. Each hook was already number-audited at
  feed generation (uncited cards dropped), so the composite carries only sourced lines.
  ORIGINAL: "오늘의 데스크" 상단에 "오늘 브리핑 공유": composes the day's
  top 3 cards into one 1:1 card. (Small; reuses SH-1/2.)

- **SH-ANSWER · 답변 전체 공유 (studio-api + web)** — ✅ done. Every finished chat answer gets a
  🔗 공유 button; the sheet snapshots the WHOLE answer (kind=`answer`) — prose + inline `{{figure:N}}`
  artifacts + `[n]` citations + the QT-2 audit/number-ledger — as a **pure content payload with NO
  user identity** (no email, no conversation id). The public page `/s/{token}` renders it read-only via
  the exact chat answer components (article typography, inline figures, LG-4 numeral highlights, 판정
  strip) plus every cited source as a read-only `SourceCard` (source · as_of · snippet · 원문↗ link) —
  so provenance/evidence travel WITH the answer; the in-app highlight viewer stays behind the sign-up
  CTA (it needs a tenant key). QT-2 gate applies unchanged (an answer with an unsupported number is
  refused). Tests: studio answer create/read + no-identity assertion + trust-floor refusal; shareCard
  `plainText`/`answerCardLines`.
  - **Link-only sharing + rich OG (2026-07-08).** The aspect-preset picker + image save/copy UI
    (old SH-2b) was **removed** — every share is just a link. The OG preview is generated silently:
    `renderOgCard` draws a **1200×630 (1.91:1 — the exact unfurl ratio, nothing crops/breaks)** card
    from the REAL content with **measure-based layout** (`wrapMeasured` — real glyph width, clean
    wrap, ellipsis on overflow, lead never collides with the footer): brand + ✓출처 chip, the
    question as a bold ≤3-line hook, a body lead, and a footer strip (출처 names + as_of · short
    link · '과거 기록 · 전망 아님' on history kinds). The OG *description* is the answer lead
    (`plainText`, 160 chars) so text-only unfurls (카카오/텔레그램) also show real content. Pure
    builders `ogCardForAnswer`/`ogCardForArtifact` are unit-tested.
  - **Folded source panel (2026-07-10).** The public answer page's 근거 is a RIGHT side panel on
    desktop (sticky, ≥900px) / below the article on mobile — **collapsed by default**; a [n] click
    opens it and scrolls to that card.

- **MOBILE-1 · 전면 모바일 레이아웃 (web)** — ✅ done. A `useIsMobile()` (matchMedia ≤720px) switches
  the desktop 3-column grid into a single scrolling column: the rail becomes a left **drawer** (☰ in a
  mobile top bar), the 근거 패널 becomes a **bottom sheet** (tap an answer → slides up, ✕ to close),
  modals (공유 시트 · SourceViewer) go full-bleed bottom-sheet, and the composer is safe-area docked.
  `viewport` (device-width, viewport-fit=cover, theme-color) + notch-safe insets ship the app as a
  phone web-app. The **public share page is mobile-first** (SNS links open on phones). Pure CSS with
  `!important` grid override beats the SSR inline style (no desktop-grid flash before hydration).

## 4. M-FACT — 근거 기반 팩트체크

### 4.1 Flow

User pastes a claim ("삼성전자 이번 분기 영업이익 15조 넘었대", "S&P 지금 낙폭 역대 3위래") →
intake classifies `fact_check=true` (LLM, no regex) → decompose claim into checkable sub-claims →
parallel tool gather (filings/financials/prices/history/news/RAG) → **verdict artifact**:

```
kind: "verdict"
{ claim: str,                              // verbatim, quoted
  verdict: "사실" | "대체로 사실" | "사실과 다름" | "확인 불가" | "미래 주장(검증 불가)",
  confidence: "high" | "medium" | "low",   // evidentiary support, not probability
  findings: [{ point: str, supports: bool, citation_idx: int }],   // 근거 for/against, each cited
  corrected: str | null,                   // what the record actually says, sourced
  method: str }                            // how it was checked (tools used)
```

Guardrail fit: verification of PAST/CURRENT facts only. A claim about the future ("다음 달
급등한대") → verdict `미래 주장(검증 불가)` + optionally the historical base rate as context
(labeled). Never scores the future claim itself.

### 4.2 Tasks

- **FC-1 · Intake + orchestration (agent-engine)** — intake flag `fact_check` (like narrative/
  news_brief); orchestration recipe: extract sub-claims → parallel verify (each sub-claim gets
  its own tool calls) → compose verdict. Tests: mocked-LLM intake flag; verdict composition from
  stubbed findings; future-claim → 미래 주장 verdict.
- **FC-2 · Verdict artifact (agent-engine models + web renderer)** — kind `verdict`: big verdict
  chip (사실=green-ish? NO — grayscale + freshness palette only: verdict rendered as text chip
  with ✓/△/✕/? glyphs, per DESIGN tokens), claim quote block, findings list each with [n],
  corrected statement, method footer. Share-ready by construction (SH pipeline). Tests: renderer
  requires ≥1 cited finding; uncited verdict refuses to render.
- **FC-3 · Eval + rubric** — +3 scenarios: true claim (실적 수치 확인 → 사실 with 공시 citation),
  false claim (틀린 수치 → 사실과 다름 + corrected), future claim (→ 미래 주장 + optional base
  rate). Rubric note: verdict must match the evidence; a confident verdict on thin evidence
  fails grounding.
- **FC-4 · 정보방 입력 UX (web)** — composer detects a pasted multi-line claim? NO heuristics —
  a "팩트체크" chip in the composer (and Desk-feed card kind `fact_check_nudge` when news
  clusters conflict). Explicit, not inferred.

## 5. Prerequisite pulled forward — QT-2 number audit

Before shares ship, the number audit (M-QUANT/QT-2) must land: every numeral in prose/cards
matches a tool value; mismatch → regenerate/drop. Shared images make hallucinated numbers
permanent — this is the trust floor for the whole publish layer. (Scope: audit only; the full
compute engine QT-1/3/4 stays where it is in the roadmap.)

## 6. M-NOTE — 인사이트 노트 (report-grade composition)

Chat-first rebirth of Insight Canvas (Idea #3): a NOTE = title + ordered blocks
(`prose | artifact-ref | quote(evidence) | verdict-ref`), composed from a conversation.

- **NT-1 · Note model + BFF (studio-api)** — `Note{id, user_email, title, blocks JSON,
  created/updated}`; CRUD; block artifact-refs store tool+args snapshots (refreshable).
- **NT-2 · "이 대화를 노트로" (agent-engine)** — one synthesis pass turns the conversation's
  artifacts+citations into a structured draft note (prose cited [n], artifacts referenced, no
  new numbers — audit applies). User edits blocks in a minimal editor (reorder/delete/edit
  prose only, v1).
- **NT-3 · Note render → A4 image/PDF** — the note as a 1-page report (A4 preset in the SH
  renderer): title, byline(optional), blocks, full source list footer. PDF via browser print
  stylesheet v1.
- **NT-4 · Note share** — SH pipeline with kind=note; public page renders the whole note.

## 7. Data depth that makes content share-worthy (already-specced, resequenced)

1. **HL-5 era news** (M0 tail) — "그때 신문" quote cards are prime share material.
2. **EC-1 transcript archive + EC-2 surprise history** — earnings-season share cards.
3. **KR macro expansion (ECOS beyond rates: CPI/실업률/성장)** — new small task **DATA-KR-1**;
   KR 팩트체크 needs official KR macro.
4. **HL-8 chart panes + M2 히스토리 랩 surface** — the demo-day share factory.

## 8. Sequencing (v3 build order — rationale)

```
QT-2(number audit) → M-SHARE(SH-1..3) → M-FACT(FC-1..3) → SH-4/5 · FC-4
  → HL-5 · HL-8 → M2(히스토리 랩) → M-NOTE → EC(M3) → FI(M4) → M-QUANT 완성 → M5(UX) → M6
```

- Audit first (trust floor) — small, high-leverage.
- Share before fact-check: fact-check's killer distribution IS the share card.
- M2 before M-NOTE: the lab generates the artifacts notes want.
- Analytics/data milestones (M-QUANT full, M3, M4) interleave after the publish loop exists,
  because every new artifact kind immediately gains distribution.

## 9. Non-goals (publish layer)

No engagement mechanics inside the product (likes/follows/feeds) — distribution happens on the
user's platforms; we make the artifact. No dark theme v1. No screenshot-OCR fact-check v1
(text paste only). No public write surface. And as everywhere: no forecasts on anything that
leaves the app — the label travels with the pixels.
