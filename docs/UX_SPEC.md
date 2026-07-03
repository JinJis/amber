# UX SPEC — Chat-first Research Desk (v2, 2026-07-03)

> Source of truth for every screen this roadmap touches. Replaces
> `docs/deprecate/UX_SPEC.md` + `docs/deprecate/DESIGN_SYSTEM.md` for **new** work; the
> deprecated docs remain the reference for already-shipped surfaces we are not changing.
> Referenced by ROADMAP tasks — section numbers are stable (§5.4, §6.4 are cited there).
>
> **Chat-first decision (2026-07-03):** 대시보드(보드) and 알림봇 are feature-flagged off
> (`FEATURE_BOARD` / `FEATURE_ALERTS`, default false). This spec designs for the rail
> **탐색 · 히스토리 · 관심 · 설정**. Flag-on layouts are unchanged legacy behavior.

---

## 1. Design principles (ranked — when they conflict, the higher one wins)

1. **Trust is visible, not stated.** Every number wears its provenance
   (`source · as_of · freshness`); every historical statistic wears `과거 기록 · 전망 아님`.
   If we can't source it, we draw the gap and say why. The guardrail refusal is a designed
   moment, not an error state.
2. **History is overlaid, not searched.** The killer interaction is *seeing* today's line on
   top of 2000/2008/2020 — comparison first, tables second. Prefer aligned charts, day-0
   anchors, and scrubbing over query forms.
3. **The desk speaks first.** The user should never face a blank composer. Turn zero is a
   sourced briefing of questions worth asking (Desk Home, §3); turn N ends with follow-up
   chips that scratch the next itch.
4. **One glance, one meaning.** Grayscale surface; the *only* saturated pixels are trust
   signals (freshness dots) and the amber guardrail. Numbers/tickers/dates are always
   `Space Mono`; prose is `Space Grotesk`. No decorative color, no gradients on data.
5. **Everything composes back into chat.** Every card, chart, era, and event chip can become
   a chat turn (tap → pre-filled composer, editable). Chat is the workbench; other views are
   instruments that hand context back to it.

## 2. Information architecture

### 2.1 Rail (left, 210px — existing shell grid)

```
◇ 로고/마스코트
─────────────
▸ 탐색        (chat + Desk Home zero state)          — default view
▸ 히스토리    (History Lab)                           — lands with M2
▸ 관심        (watchlists/@groups)
─────────────
▸ 설정        (agents 빌더 · 프롬프트 · 계정)
[대시보드]    only if FEATURE_BOARD
[알림봇]      only if FEATURE_ALERTS
─────────────
새 대화  ⌘K
대화 히스토리 (탐색 선택 시)
```

- View state serializes to the URL (`?v=history&regime=gfc-2008&t=^GSPC&d=2008-10-10`) so
  every lab/chat state is shareable and restorable (UX-1). Still a SPA — no route split.
- 설정 absorbs the agent builder + prompt library entries (they open as the existing modals).

### 2.2 Command palette — ⌘K (UX-2)

Single input, three result groups, fuzzy-matched client-side over already-loaded data:
**이동** (watchlist tickers, regimes by name/alias, recent conversations), **실행**
("새 대화", "히스토리 랩에서 ^KS11 열기", prompt library entries), **질문** (fallthrough:
any unmatched text → new chat turn). Enter on a ticker opens 탐색 with the ticker context
header (§2.3) armed. No server round-trip before Enter.

### 2.3 Ticker context header (UX-3)

When a conversation/lab has a focused ticker, a persistent strip under the top bar:

```
 삼성전자 005930.KS   74,300  ▼1.8%   낙폭 −12.4% (고점 2026-05-02) · 5y 하위 8% ⓘ   다음 공시 D-6 (분기보고서)
 └ mono, price   └ day move  └ from /history/drawdowns + percentile chip      └ disclosure calendar
```

Every segment sourced (hover → mini provenance popover); the percentile chip appears only
after M0 and opens History Lab on click. Missing pieces render as gap chips, not blanks.

## 3. Desk Home — the turn-zero briefing (M-DESK)

The empty state of 탐색. Replaces the blank "무엇이든 물어보세요" moment.

### 3.1 Layout

```
┌────────────────────────────────────────────────────────────────┐
│  좋은 아침이에요. 오늘의 데스크입니다.          2026-07-03 (목) │
│  ── 물어볼 만한 것 ──────────────────────────  새로 고침 ↻     │
│                                                                │
│  ┌ price_move ────────────┐  ┌ filing_new ──────────────────┐ │
│  │ ▼ 삼성전자 −3.2%        │  │ ● TSLA 8-K (2시간 전)         │ │
│  │ 5y 일간 변동 하위 2%    │  │ Item 5.02 — 임원 변경         │ │
│  │ "과거 이 정도 하락 뒤   │  │ "이번 8-K에 뭐가 들어있어?"   │ │
│  │  기록 보여줘"        →  │  │                            →  │ │
│  │ Yahoo · 10:32 · 🟢      │  │ SEC EDGAR · 08:15 · 🟢        │ │
│  └────────────────────────┘  └──────────────────────────────┘ │
│  ┌ earnings_upcoming ─────┐  ┌ news_cluster ────────────────┐ │
│  │ D-2  TSLA 실적          │  │ @반도체 관련 헤드라인 6건      │ │
│  │ 컨센서스 EPS $0.71      │  │ "HBM 증설 뉴스 정리해줘"   →  │ │
│  │ "컨센서스 확인하기"  →  │  │ Google News · 09:00 · 🟢      │ │
│  │ FMP · 07:00 · 🟢        │  └──────────────────────────────┘ │
│  └────────────────────────┘  ┌ continue_thread ─────────────┐ │
│                              │ 어제: "TSLA 마진 분석" 이어서 │ │
│                              └──────────────────────────────┘ │
│  ── 컴포저 ────────────────────────────────────────────────── │
│  [ @그룹, 종목, 또는 아무거나 물어보세요…               ⏎ ]   │
└────────────────────────────────────────────────────────────────┘
```

Card anatomy (all kinds share it): **kind icon + hook line(s)** (the sourced fact, numbers
in mono) → **suggested question** (quoted, `--accent` on hover) → **ProvenanceFooter**
(source · as_of · freshness dot). Tap anywhere → composer pre-filled with the question
(editable — never auto-send). Hook deep-links (8-K, chart) open their viewers directly
without consuming the suggestion.

### 3.2 States

- **No watchlist** → the grid leads with the `watchlist_nudge` card: "관심그룹을 만들면
  데스크가 매일 아침 이 자리를 채워둡니다" + inline quick-add (ticker search input + market
  preset chips reused from onboarding). Below it, 2–3 market-wide cards (지수 move, econ
  calendar) so the screen is never empty even pre-signup-value.
- **Feed loading** → skeleton cards (pulse), composer immediately usable.
- **Nothing notable** (weekend/quiet) → graceful empty: "장 마감 · 새 이벤트 없음" + 3
  capability cards drawn from `_CAPABILITY_MENU` ("이런 것도 볼 수 있어요: 낙폭 히스토리 /
  어닝콜 원문 / 공시 증거 뷰어") — a features tour disguised as suggestions.
- **Feed error** → capability cards + quiet retry; never a blocking error panel.

### 3.3 Behavior contract

- Feed = studio-api cache (TTL 30–60min, stale-while-revalidate; invalidate on watchlist
  change). Generated by `POST /agent/desk-feed` (DK-1): parallel gateway gather → Gemini
  synthesis → 4–8 cards; **cards without citations are dropped**.
- Tone: descriptive curiosity ("주목할 변화", "확인해보기"), never advice ("매수 기회" is
  refused at generation). `this_day_history` cards carry `HistoricalLabel` (§6.4).
- Returning mid-conversation users land in their thread, not Desk Home; "새 대화" always
  starts at Desk Home.

## 4. Chat turn anatomy (탐색)

Unchanged shipped structure (thinking stream → answer with `[n]` → artifacts → sources →
follow-up chips), extended by this roadmap:

- **`analogue` artifact** (HL-7): day-offset chart, current path `--ink` 2px, matches in
  muted grays by rank, dashed aftermath right of the day-0 divider; hover a path → tooltip
  (기간, 유사도, regime chip); header actions: `히스토리 랩에서 열기`. `HistoricalLabel` in
  the footer. **No averaged path is ever drawn.**
- **`base_rates` artifact** (HL-7): event sentence + `n=87 (군집화)` chip, horizon table
  (mono columns: n / 중앙값 / p25–p75 / 최악·최고 / 상승마감비율), histogram strip with
  zero-line, event-date chips (first 12 + expander) → each chip deep-links the lab scrubber.
- **Chart panes** (HL-8): underwater toggle, regime shading on ≥5Y ranges (4%-alpha ink
  zones + tiny mono era labels), vol-context ribbon under the legend.
- **Computed artifacts** (M-QUANT, QT-3): `scatter` (x/y units labeled, zero/identity
  reference lines only — **never a fitted/trend line**) and `distribution` (bins + "현재 값"
  marker); every computed artifact carries a `계산 근거` chip opening ComputationPanel
  (method → formula → sourced inputs → steps). Unit-mismatched series never share an axis —
  auto second pane or rebase-to-100 with a note chip (QT-4).
- **Follow-up chips**: existing behavior; new History-Lab and earnings chips per HL-9/EC-4.
- **Refusal turn**: unchanged amber GuardrailLabel, but prediction-adjacent asks get the
  productive redirect: "전망 대신, 과거의 기록을 보여드릴게요" + the descriptive offer
  (HL-6) — refusal that converts into the killer feature.

## 5. History Lab (히스토리) — M2

One screen, four zones. Anchor state = `{anchor_ticker, regime_slug?, scrub_date}`.

```
┌ 상단: 세기 리본 (HL-10) ──────────────────────────────────────────┐
│  S&P500 ▾ (로그)  1927━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 2026 │
│           ░87░    ░닷컴░      ░GFC░   ░covid░ ░22░        ● now   │
└───────────────────────────────────────────────────────────────────┘
┌ THEN (닷컴버블 · ^IXIC) ────────┐┌ NOW (^GSPC · 오늘) ────────────┐
│  rebased=100, day0=고점         ││  rebased=100, day0=고점        │
│  ─── 경로 (D 이후는 회색)       ││  ─── 현재 경로                 │
│        ▼ 스크러버 위치 D        ││        ▼ 동일 오프셋 마커      │
└─────────────────────────────────┘└────────────────────────────────┘
│  비교 스트립: 낙폭 그때 −78% / 지금 −12% · 경과 214d/38d ·        │
│  회복 그때 4,196d  ── 유사도 0.81 ──  과거 기록 · 전망 아님       │
├ 스크러버 ────────────────────────────────────────────────────────┤
│  2000-03-10 ●━━━━━━━━━━━━━○━━━━━━━━━━ 2002-10-09   ▶ 재생        │
└───────────────────────────────────────────────────────────────────┘
우측 레일 (HL-12): 그날의 신문(D 기준, NYT 헤드라인 카드) ·
                   매크로 스냅샷 as-of D (FFR/CPI/실업률/10Y) ·
                   D 부근 공시 (앵커 종목) — 전부 evidence 뷰어 딥링크
하단 드로어 (HL-13): 베이스레이트 랩
```

- **§5.1 Century ribbon**: weekly bars, log scale; regimes as clickable shaded chips; anchor
  switcher (S&P500/NASDAQ/KOSPI/사용자 종목 — insufficient history → gap chip with
  `required/available` from the API's 422). Keyboard: ←/→ moves regime focus, Enter loads.
- **§5.2 THEN|NOW**: synchronized crosshair by day-offset; rebase toggle (%/log); scrubbing
  never refetches (series preloaded) — 60fps target; ▶ 재생 auto-advances D (1 day/150ms,
  space to pause).
- **§5.3 Era rail**: bound to D with 150ms settle debounce; hard rule — **nothing with
  `as_of > D` renders** (point-in-time invariant); each card: headline (Grotesk), date
  (mono), source, deep-link. KR regimes pre-provider show the era-news gap chip
  ("KR 뉴스 아카이브 준비 중 — 미국 신문으로 대체하지 않습니다").
- **§5.4 Base-rate lab (drawer)**: condition preset chips (`일간 −5%↓` `낙폭 20%↑`
  `변동성 상위 5%`) + numeric steppers + horizon multi-select → `base_rates` artifact
  inline; event chips ↔ scrubber jumps. "챗에서 이어서 질문" handoff (HL-14) composes a
  turn carrying `{ticker, regime, D, event spec}` as context.
- **§5.5 Entry points**: chat artifact "히스토리 랩에서 열기"; ⌘K regime names; ticker
  header percentile chip; Desk Home `this_day_history` card.

## 6. Components & visual language

Tokens are unchanged (`globals.css` — grayscale + `--accent` indigo + freshness colors +
amber guardrail; Space Grotesk/Mono; existing shadows/radii). New/extended primitives live
in `web/components/ui.tsx` unless noted:

- **§6.1 Chart conventions** (TradeChart): current/primary series `--ink`; historical
  comparatives `#B9B9BE → #8A8A90` by rank; aftermath = dashed; regime zones = ink at 4%
  alpha, labels 10px mono `--muted`; day-0 divider = 1px `--line-2` vertical with `D0` tag.
  Never a saturated series color.
- **§6.2 ProvenanceFooter** (UX-4, promoted to a primitive): `source · as_of · FreshnessDot
  · CadenceTag [· 계산 근거]` — one line, mono, mandatory on every artifact/card. Renderers
  **refuse to draw** data-bearing cards without it (dev-mode throw).
- **§6.3 GapChip**: `⬚ 데이터 없음 — {reason}` (e.g. "KOSPI 1997년 이전 · 제공처 미보유");
  used in ribbon, rail, header. Gaps are drawn, never faked (invariant).
- **§6.4 HistoricalLabel**: the descriptive-statistics badge — pill, `--panel-2` bg,
  `--line-2` border, `--text-2` text, ⏳ glyph, fixed copy **"과거 기록 · 전망 아님"**.
  Deliberately *not* amber (it marks safe-by-design content, not a refusal); sits beside
  ProvenanceFooter on every base_rates/analogue/this_day_history render. Non-dismissable.
- **§6.5 Desk cards** (§3): compose Card + ProvenanceFooter + kind icon; hover raises
  `--shadow-card→pop`; entire card is one tap target with a visible `→` affordance.

## 7. Degraded & flagged states

- Flags off (default): no 📌/🔔 anywhere; onboarding = market → watchlist → guided first
  conversation (UX-5: pre-composed "히스토리 랩 투어" turn + the trust-education moment
  "우리는 전망하지 않습니다 — 역사를 보여줍니다").
- Missing upstream key (NYT 미설정 등): the dependent card/rail section renders a GapChip
  with the env var name in admin, silently absent for end users beyond the gap.
- Slow feed/tools: skeletons everywhere; composer and rail never block on data.

## 8. Accessibility & motion (UX-4 acceptance)

Focus traps in modals/drawers; scrubber is a proper `range` input (←/→ = 1d, PgUp/PgDn =
5d, Home/End = 경계); charts expose a data-table fallback (`시트로 보기`) for screen
readers; `aria-live=polite` for streaming tokens and rail updates; all motion ≤200ms
ease-out and disabled under `prefers-reduced-motion`; hit targets ≥40px; contrast ≥4.5:1
(the gray ramp above passes on `--panel`).
