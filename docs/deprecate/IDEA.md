# IDEA.md — product idea parking lot

> Exploratory product ideas, **not commitments.** The committed, dependency-ordered plan lives in
> [`ROADMAP.md`](./ROADMAP.md); the felt experience in [`UX_SPEC.md`](./UX_SPEC.md). An idea here is
> **promoted to the roadmap only with explicit approval** (same rule as any roadmap addition). Each entry:
> the idea, why it fits, a rough shape, what it touches, open questions. Newest on top.

---

## 4. Multi-agent (A2A) re-architecture — orchestrator + capability-carded sub-agents
*(captured 2026-06-21)*

**The idea.** Re-cast the engine as the Google **A2A (Agent2Agent)** pattern: every datasource and every
capability becomes a **sub-agent** that publishes an **Agent Card** (a JSON "business card": identity,
endpoint, auth, streaming, and **skills** — each skill's id/description/input-output). An **orchestrator
agent** reads the cards, decides *which* sub-agents to call and *how*, then aggregates their results into one
sourced response. Sub-agents we'd carve out: **financials (SEC/DART), prices, macro, news, filing-research
(RAG), watchlist/@group resolver, and the provenance/evidence agent** (find the cited figure/passage in the
PDF and highlight it). Refs: [A2A AgentCard](https://agent2agent.info/docs/concepts/agentcard/) ·
[A2A spec](https://a2a-protocol.org/latest/specification/) ·
[ADK multi-agent (supervisor→sub-agents)](https://codelabs.developers.google.com/instavibe-adk-multi-agents/instructions) ·
[Gemini Enterprise Agent Platform / Agent Engine](https://cloud.google.com/blog/products/ai-machine-learning/introducing-gemini-enterprise-agent-platform).

**Honest assessment — we already have the seeds, so this is *formalization*, not a from-scratch rewrite.**
- The **connector manifest/`/catalog`** is already a capability registry → a proto-Agent-Card (provenance,
  params, cost, entitlement per resource). **Agent Cards can be *generated* from it.**
- The **agent-engine planner** (Gemini function-calling over catalog-derived tools) is already an orchestrator.
- The **gateway** already does discovery-time auth/entitlement/metering. **Entitlement maps cleanly onto A2A
  discovery: a tenant only "sees" the Agent Cards for connectors it activated.**
So the gap is: (1) emit standard Agent Cards, (2) reshape the single tool-loop into explicit
orchestrator↔sub-agent delegation, (3) keep our invariants (Gemini-only one router · gateway-only data ·
deterministic data).

**The key design judgment (CLAUDE.md §9 — "deterministic *data*, not deterministic *logic*").** Do **not**
turn every datasource into an LLM agent — that adds an LLM hop (latency + cost + non-determinism) where a
deterministic API call sufficed. Two tiers of sub-agent:
- **Skill/tool agents (deterministic, no LLM):** the datasource fetchers and **especially the evidence/PDF-
  highlight agent** — they get an Agent Card + uniform A2A interface but stay pure API/PyMuPDF. This is most
  of what was listed (재무제표, provenance, pdf 하이라이트).
- **Reasoning sub-agents (LLM, only where judgment is real):** e.g. a **filing-research/RAG** agent
  (synthesize narrative), maybe a **financial-analysis** specialist. These earn their LLM cost.
The orchestrator (LLM) is the only mandatory reasoning layer; everything deterministic stays deterministic.

**Why it fits.** (1) **Ecosystem** pillar — A2A is interop: external agents can call *our* sub-agents, and we
can call *external* A2A data agents; "clone an analyst" becomes "compose carded agents." (2) **Modularity** —
each domain (and the evidence engine) is independently testable/scalable/ownable. (3) **Trust** — the
provenance/evidence agent as a first-class carded skill makes "show me the source" a composable capability.

**Rough shape (incremental — each phase ships value alone; no big-bang).**
- **P0 · Agent Cards from the catalog (cheap, standards-aligned).** Serve `/.well-known/agent-card.json` per
  domain, generated from the manifest; entitlement-filtered per tenant. No internal change → instant A2A
  discoverability + a clean capability contract.
- **P1 · Orchestrator refactor.** Turn the planner loop into an explicit **supervisor** that delegates to
  **deterministic skill-agents** (still gateway-entitled; Gemini for the supervisor only). Same data path,
  clearer structure; provenance/evidence becomes a skill-agent.
- **P2 · LLM specialist sub-agents** where they add value (filing-research/RAG synthesis; financial-analysis).
- **P3 · A2A server/client edges** — expose our sub-agents over A2A (others call us) + call external A2A
  agents, all through the gateway's auth/meter.
- **P4 · Runtime (optional).** Consider **ADK** as a *self-hosted library* for graph orchestration (the user
  runs their own server — prefer self-host over the managed Agent Engine to avoid lock-in); adopt only if it
  beats our own loop.

**What it touches.** `agent-engine` (planner→orchestrator; sub-agent runtime), `datasets`/`control-plane`
(Agent-Card emission + entitlement-filtered discovery), the eval harness (must score multi-agent flows), and
`docs/ARCHITECTURE.md`. Reuses: catalog, gateway, Gemini router, the PH-PROV3 evidence engine (becomes the
evidence skill-agent).

**Open questions / risks.** Multi-hop **latency + token cost** (mitigate: deterministic tiers, parallel
sub-agent calls, caching); **determinism** (keep data/evidence deterministic — only the supervisor reasons);
**partial-result aggregation + error handling** across sub-agents; **eval** must cover orchestration quality;
**self-host ADK vs managed Agent Engine** (lean self-host per the "user runs own server" constraint); whether
the payoff (interop/ecosystem) justifies the complexity now vs. after the PH-PROV3 evidence work lands.
**Recommendation:** start at **P0 (Agent Cards from the catalog)** — low-cost, standards-compliant, reversible
— and commit to P1+ only once the evidence generalization (PH-PROV3d/e) lands. **Do not expand the roadmap
without approval.**

## 1. Pin-to-Dashboard — chat → sourced live artifacts → your own financial dashboard ("Datadog for investing")
*(captured 2026-06-19)*

**The idea (the north-star loop).** You **ask** anything — economy, finance, an investment idea, a single
ticker — in **chat**. The desk answers not with prose but with **graphs, tables, and live data, each carrying
its `source` + `as_of` + `freshness`** (and, via [PH-PROV2](#), a **highlighted screenshot of the exact filing
line** the number came from). Any of those artifacts you find worth watching, you **📌 pin** — and they live on
**your own dashboard** that **auto-refreshes** like a Datadog/Grafana monitoring board. The one-off *pull* (an
ask) becomes a durable, self-curated, always-current **monitoring surface** you built yourself.

**Why it fits.** This is the literal realization of the three pillars: **trust by construction** (every pinned
figure stays sourced + freshness-stamped, gaps drawn), **pull→push** (a pinned card auto-refreshes and feeds
brief/alert triggers), **clone ecosystem** (a board is shareable/cloneable with the reader's own watchlist
substituted). It's "not a chatbot" made concrete — the chat is the *query bar* of a personal Bloomberg, and the
dashboard is the product you keep coming back to.

**Status: PARTIALLY BUILT.** **U3 Board** is exactly the MVP of this — `📌 핀` on every chat artifact card →
the **보드** rail → a grid of pinned live cards with `↻새로고침` (U3-03a/03b, both ✅). The **open frontier**
(IDEA): turning that grid into a real dashboard — layout/resize, **grouping by watchlist / `@group`**, multiple
boards, alert tie-in to **briefs + the Disclosure Calendar** ("this pinned figure changed"), and pinning the
*PH-PROV2 evidence screenshot* itself, not just the number. Cross-links the [Research Desk as a tool](#2-the-research-desk-as-a-tool--frameworks-live-blocks-hypothesis-journaling-portfolio-check-up)
(frameworks/portfolio) and the [Insight Canvas](#3-the-insight-canvas--a-composable-live-sourced-authoring-surface)
(prose around the same live blocks). **Do not expand the roadmap without approval.**

## 2. The Research Desk as a *tool* — frameworks, live blocks, hypothesis journaling, portfolio check-up
*(captured 2026-06-19)*

**The idea.** Lean hard into "**not a chatbot, a productivity tool**" — a modern, web-native personal
Bloomberg + an investor's journaling/collaboration surface. The user does the thinking; we give them the
**best kitchen** (live, sourced data + frameworks) to cook with. Three motivating vignettes:
- **나만의 인사이트 노트** — today people screenshot, draw on tables, color-code, hand-collect… too fiddly.
  Instead: collect material conversationally (chat) → **pin to the Board** → spin it into a report. *(This is
  exactly the [Insight Canvas](#3-the-insight-canvas--a-composable-live-sourced-authoring-surface) below —
  the Board is its MVP substrate.)*
- **반려주식 관리 ("pet stocks")** — people get stuck holding losers and feel they must study endlessly;
  there's too much to learn. Make finding the *relevant, sourced* facts easy — and **kill hallucination**
  (every figure traced to its filing — this is what PH-PROV2 visual evidence is for).
- **포트폴리오 점검** — paste a screenshot of your brokerage portfolio → the desk checks it against
  frameworks/simulations: macro/micro, FX, P&L state, **risk-reward (손익비)** math, what-if "if I took
  action X, how could it move" laid out game-theory-style, and classic investor traps to watch.

**Why it fits — and why it's clean, legally.** Nobody thinks Excel / Notion / TradingView *recommends*
stocks; they're tools the user drives. Framing the desk as a **tool** (user pulls data and composes it),
not an advisor, keeps it clear of investment-advisory regulation — *and* matches our existing **guardrail**
(no advice / no forecasting, shown not hidden). Targets **high-engagement mid/advanced investors** building
their own philosophy → higher retention + willingness to pay (subscription, not BYO-key).

**Concrete features (sketches).**
- **Framework templates ("framework as a template").** Don't start from a blank board — ship famous
  investor models / book formulas as plug-in templates. *e.g.* 손익비 & Kelly-bet sizing board, Minervini
  VCP (volatility-contraction) tracker, rate-cycle asset-allocation view. (Generalizes prompt-import + the
  gallery clone pattern to **board/framework templates**.)
- **Live data blocks.** Notion-block-style: drop a watchlist company's live valuation/chart next to your
  prose (reuses the U3 artifact spec — live, sourced, freshness-stamped). = the Insight Canvas blocks.
- **Hypothesis-verification journaling.** At buy time, record *why* (the thesis). The system tracks the
  post-thesis price + fundamentals via the data plane and helps you **retrospect** ("was your hypothesis
  right?") weeks later. A deterministic, sourced feedback loop on your own decisions.
- **Portfolio check-up.** Import holdings (brokerage screenshot → OCR, or manual/CSV) → run framework
  calculators (risk-reward, exposure, concentration), **user-parameterized** what-if, and a "common traps"
  checklist. Calculators are deterministic; the data is sourced.

**What it touches.** `web` (board/canvas + template gallery — big lift) · `studio-api` (Note/Board template
models, portfolio model; mirror the prompt-import clone pattern) · `agent-engine` (framework calculators as
**deterministic tools**, never hand-rolled advice) · OCR for screenshot import (a new capability;
PaddleOCR/Upstage for KR) · governance/metering (PH-11/PH-12) for live-block refresh quotas.

**Open questions / risks.**
- **Guardrail tension (important).** "Simulation / what-if / strategy check / 손익비" must stay
  **user-parameterized + sourced + deterministic** — a calculator the user drives, never a model-generated
  forecast, score, or buy/sell suggestion. Reconcile every such feature with invariant #5 (no
  forecasting/advice, shown). If a framework implies a recommendation, it's out.
- **Hallucination = the whole brand.** Every figure must trace to a source (PH-PROV2). Frameworks compute
  from sourced inputs only; gaps drawn, never filled.
- **OCR reliability** for brokerage screenshots (formats vary, KR/EN); wrong import → wrong analysis. Needs
  a verify-before-use step.
- **Framework IP/licensing** (named methods/books) — attribute; check before shipping branded templates.
- **Surface sprawl.** chat (pull) · standing analysts (push) · Board/Canvas (compose) · portfolio (check) —
  decide the north-star ordering before committing; the Board is the shared substrate to build first.
- *User's open question to revisit:* which **investment frameworks/formulas (손익비, …)** to ship first as
  the flagship board templates?

**Status:** IDEA. Strong fit with the "research desk = tool" north star; **do not add to the roadmap without
approval.** Natural sequence: **Board (U3-03, done) → evaluate Canvas (#2) → framework templates → portfolio
check-up.** Depends on PH-PROV2 (no-hallucination) being solid first.

## 3. The Insight Canvas — a composable, live-sourced authoring surface
*(captured 2026-06-15)*

**The idea.** Professional investors organize their thinking by screenshotting charts/tables/filings and
pasting them into a blog post or research note. Those screenshots are **dead** — stale, unsourced,
un-refreshable. We already (a) pull any data through one LLM + data plane, (b) render it as **live, sourced
artifacts** (U3: charts/tables with provenance + freshness). So the natural evolution of the chat surface
is a **blog-/doc-like authoring canvas** where the analyst **writes their thesis in prose and drops live
artifacts anywhere in the text** — pulled the same way they ask in chat. A "living research note", not a
dead blog post.

**Why it fits (and is a real differentiator).**
- It's the same engine, one surface up: chat answers an ask; the canvas lets you **compose** many asks into
  a narrative. Artifacts stay **live** (refreshable, freshness-stamped, every figure carries its source) —
  this is *trust by construction* applied to authoring.
- Not a chatbot, not a BI dashboard, not Notion: a **sourced-insight authoring tool** for investors. The
  screenshot-to-blog workflow becomes native, and the output never goes stale silently.
- Composes cleanly with what's planned: **U3** artifacts are the embeddable blocks; **PH-4** source-preview
  cards are inline citations; the **Board** (U3-03) is the "untitled grid" version of a canvas (cards
  without prose); **U5 gallery** makes a note **publishable/cloneable** (others clone it with *their* data
  substituted → the ecosystem pillar, applied to notes); **PH-SOURCES** (reports/blogs/books) becomes the
  research feedstock writers cite.

**Rough shape (sketch, not a spec).**
- A `Note` / `Canvas` doc type: a block editor of **text blocks** + **artifact blocks**.
- Insert an artifact via a `/` command or by dragging a card from chat / the Board → embeds the artifact
  **spec** (not an image), so it re-fetches + re-stamps `as_of` on open. Gaps still drawn.
- Every embedded figure keeps its provenance chip + freshness dot; "stale → update" surfaces inline.
- Share/publish (Phase-2 ecosystem): publish a note like a gallery analyst; clone re-binds its data to the
  reader's watchlist/activations. Export = keep-live (link) or snapshot.

**What it touches.** `web` (block editor — the big lift) · `agent-engine`/`studio-api` (reuse the artifact
spec + a `Note` persistence model, mirrors `PinnedArtifact`) · governance (PH-12) if notes with licensed
data are published.

**Open questions / risks.**
- Block-editor complexity (build vs. adopt a minimal lib) — keep it small first (text + artifact blocks).
- Refresh cost/quotas for many live artifacts in one doc → ties to subscription metering (PH-11/PH-12).
- Relationship to the **Board**: Board = grid of live cards; Canvas = prose + live cards. Likely the Board
  is the MVP and the Canvas is "Board + narrative". Sequence: **U3-03 Board first, then evaluate the Canvas.**
- Authoring ≠ the "pull→push analyst" pillar — this is a third surface (compose), alongside chat (pull) and
  standing analysts (push). Worth deciding if/where it sits in the north star before committing.

**Status:** IDEA. Strong fit; **do not add to the roadmap without approval.** Natural earliest moment to
revisit: after **U3-03 (Board)** ships (the Board is the canvas's MVP substrate).
