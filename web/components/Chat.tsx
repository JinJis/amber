"use client";

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import AgentBuilder, { Agent, Category } from "./AgentBuilder";
import BoardCanvas from "./BoardCanvas";
import BotHome from "./BotHome";
import { ShareSheet } from "./ShareSheet";
import Onboarding from "./Onboarding";
import NotebookView from "./NotebookView";
import { NotebookPicker, type PinPayload } from "./NotebookPicker";
import CockpitEntry from "./CockpitEntry";
import Watchlists, { Watchlist } from "./Watchlists";
import { ContextPanel, evidenceOf, uniqueTools } from "./EvidencePanel";
import { type LedgerRow } from "../lib/evidence";
import { SourceViewer } from "./SourceViewer";
import { Button, Chip, GuardrailLabel, Mascot, FreshnessDot } from "./ui";
import type { Features } from "../lib/features";
import { FeaturesProvider } from "../lib/features-context";
// 답변 본문 렌더링 클러스터 + 스트림 파트는 chat/ 하위로 분리(FE-2). 테스트가
// `../components/Chat`에서 임포트하므로 아래 심볼들은 그대로 재익스포트한다.
import { AnswerArticle, linkifyCitations, makeMdComponents, splitFigures, type NumCtx } from "./chat/answer";
import { ClarifyChips, SubAgentCards, ThinkingLive } from "./chat/parts";
export { AnswerArticle, linkifyCitations, makeMdComponents, splitFigures };
export type { NumCtx };
// Chat / SSE-event + Artifact/Citation shapes now live in lib/types.ts (FE-01).
import type {
  Artifact, Citation, ClarifyOption, Msg, SubAgent, ToolUse,
} from "../lib/types";

// Map a citation-carrying SSE event (streamed `citation` or the `done` list) to a Citation.
// cadence/category ride along so a pinned widget knows if it can carry an alert; table +
// evidence_image_url let the source card reach the original filing/page.
function toCitation(ev: any): Citation {
  return {
    tool: ev.tool, source: ev.source, url: ev.url, index: ev.index, kind: ev.kind,
    doc_type: ev.doc_type, as_of: ev.as_of, freshness: ev.freshness,
    cadence: ev.cadence, category: ev.category,
    snippet: ev.snippet, ticker: ev.ticker, page: ev.page,
    table: ev.table, evidence_image_url: ev.evidence_image_url, used: ev.used,
  };
}


export default function Chat({ name, features }: { name: string; features: Features }) {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // background-run tracking: which conversation is currently DISPLAYED, and which one is
  // actively being streamed into the UI. Generation lives server-side, so leaving a chat
  // just stops rendering here (the server keeps going); re-entering resumes via its run.
  const viewConvRef = useRef<string | null>(null);
  const streamConvRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // agents
  const [agents, setAgents] = useState<Agent[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [agentId, setAgentId] = useState<string>(""); // "" = default agent
  const [builder, setBuilder] = useState<{ open: boolean; base: Agent | null }>({ open: false, base: null });
  const inputRef = useRef<HTMLInputElement>(null);

  // shell view + watchlists / @groups. Dashboard is home (when enabled); 탐색(explore) is the chat
  // surface and the fallback when a feature-flagged surface is off.
  const [view, setView] = useState<"dashboard" | "explore" | "watch" | "bot" | "notes">(
    features.dashboard ? "dashboard" : "explore");
  const [nbPin, setNbPin] = useState<PinPayload | null>(null);   // NB-2: asset awaiting 노트 담기
  const [standingDone, setStandingDone] = useState<Set<number>>(new Set());  // SA-1: subscribed turns
  const [handles, setHandles] = useState<string[]>([]);
  const [mention, setMention] = useState<string[]>([]); // open @-autocomplete suggestions
  const [onboarded, setOnboarded] = useState<boolean | null>(null);  // null = checking; false = show onboarding
  const [viewer, setViewer] = useState<Citation | null>(null);  // expanded source viewer
  // LG-3: [n] ↔ 근거 패널 two-way link. hover mirrors; click scrolls+flashes the card.
  const [hoverCite, setHoverCite] = useState<number | null>(null);
  const [flashCite, setFlashCite] = useState<{ n: number; ts: number } | null>(null);
  // ENT-1: the empty-state composer placeholder rotates today's REAL questions (from the desk feed).
  const [todayQs, setTodayQs] = useState<string[]>([]);
  const [phIdx, setPhIdx] = useState(0);
  useEffect(() => {
    if (todayQs.length < 2) return;
    const t = setInterval(() => setPhIdx((i) => (i + 1) % todayQs.length), 4000);
    return () => clearInterval(t);
  }, [todayQs]);
  // RIGHT CONTEXT PANEL: which assistant turn's context is pinned in the panel. null = follow
  // the latest answer live (so a streaming turn's assets fill the panel as they arrive).
  const [focusIdx, setFocusIdx] = useState<number | null>(null);
  // RIGHT CONTEXT PANEL width (px) — drag the panel's left edge to resize; clamped to a sane range.
  const [ctxWidth, setCtxWidth] = useState(420);
  function startCtxResize(e: ReactMouseEvent) {
    e.preventDefault();
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    const onMove = (ev: globalThis.MouseEvent) => {
      // panel hugs the right edge, so its width = viewport width − cursor X
      setCtxWidth(Math.max(320, Math.min(820, window.innerWidth - ev.clientX)));
    };
    const onUp = () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  // chat session/history — persisted in studio-api; resume a past conversation.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [convs, setConvs] = useState<{ id: string; title: string }[]>([]);

  async function loadHistory() {
    try {
      const r = await fetch("/api/conversations");
      if (r.ok) setConvs((await r.json()).conversations ?? []);
    } catch {}
  }
  async function openConversation(id: string) {
    viewConvRef.current = id;   // claim the view first so any other stream stops rendering
    setConversationId(id);
    setFocusIdx(null);          // panel follows the latest answer of the opened conversation
    setView("explore");
    setBusy(false);
    setLoadError(null);
    try {
      const r = await fetch(`/api/conversations/${id}/messages`);
      if (!r.ok) { setLoadError(id); return; }  // IMP-5: silent blank thread → visible banner
      const msgs = ((await r.json()).messages ?? []) as
        { role: string; content: string; citations?: Citation[]; artifacts?: Artifact[]; audit?: Msg["audit"] }[];
      setMessages(msgs.map((m) => ({
        role: m.role === "assistant" ? "assistant" : "user",
        content: m.content,
        citations: m.citations ?? [],
        artifacts: m.artifacts ?? [],   // persisted → inline {{figure:N}} cards survive reload
        audit: m.audit ?? undefined,    // persisted → 판정 + 본문 수치 하이라이트 survive reload
        used: (m.citations ?? []).map((c) => c.index).filter((n): n is number => n != null),
      })));
      // resume an in-flight answer: if this conversation is still generating, tail its run live
      const ar = await fetch(`/api/conversations/${id}/active-run`);
      const runId = ar.ok ? (await ar.json()).run_id : null;
      if (runId && viewConvRef.current === id) await tailRun(id, runId);
    } catch {
      setLoadError(id);}
  }
  function newChat() {
    viewConvRef.current = null;
    setMessages([]); setConversationId(null); setInput("");
    setView("explore");
    setBusy(false);
    setFocusIdx(null);
  }

  // ASK-2: 담기 언어는 "+노트북" 하나 — 모든 근거(차트/표·출처 카드·수치)는 리서치 노트북으로
  // 직행한다 (보드 핀 분기 제거; FEATURE_BOARD는 FLAG-1로 꺼진 죽은 가지였음).
  function pinArtifact(a: Artifact) {
    setNbPin({ kind: "pin_artifact", payload: a as unknown as Record<string, unknown>,
               title: a.title || "차트·표" });
  }
  function pinCitation(c: Citation) {
    setNbPin({ kind: "pin_citation", payload: c as unknown as Record<string, unknown>,
               title: c.source || "출처" });
  }
  // SA-1: one-tap 질문 구독 — the question is THIS turn's user message; the probe is the
  // periodic source the answer actually used (recorded by the engine, cadence-gated).
  async function subscribeStanding(i: number, m: Msg) {
    const q = messages[i - 1]?.role === "user" ? messages[i - 1].content : null;
    const offer = m.standing_offer;
    if (!q || !offer) return;
    try {
      const r = await fetch("/api/standing", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, ticker: offer.ticker ?? null, market: offer.market ?? null,
                               cadence: offer.cadence, probe: offer.probe }),
      });
      if (r.ok) setStandingDone((prev) => new Set(prev).add(i));
    } catch { /* the chip stays tappable */ }
  }

  function pinLedger(row: Record<string, unknown>, c: Citation | null) {
    setNbPin({ kind: "pin_ledger", title: `수치 ${row.raw}`,
               payload: { ...row, source: c?.source ?? null, as_of: c?.as_of ?? null, url: c?.url ?? null } });
  }

  async function loadAgents() {
    try {
      const r = await fetch("/api/agents");
      if (!r.ok) return;
      const list: Agent[] = (await r.json()).agents ?? [];
      setAgents(list);
      // land on the fully-loaded Gemini default agent (tpl_desk), not the bare/stub default
      const def = list.find((x) => x.id === "tpl_desk") || list.find((x) => x.is_template);
      if (def) setAgentId((prev) => prev || def.id);
    } catch {}
  }
  async function loadHandles() {
    try {
      const r = await fetch("/api/watchlists");
      if (r.ok) setHandles(((await r.json()).watchlists ?? []).map((w: Watchlist) => w.name));
    } catch {}
  }
  useEffect(() => {
    loadAgents();
    loadHandles();
    loadHistory();
    (async () => {
      try {
        const r = await fetch("/api/me");
        setOnboarded(r.ok ? !!(await r.json()).onboarded : true);  // on error, don't block the app
      } catch { setOnboarded(true); }
    })();
    (async () => {
      try {
        const r = await fetch("/api/connectors");
        if (r.ok) setCategories((await r.json()).categories ?? []);
      } catch {}
    })();
  }, []);

  // @handle autocomplete: when the text ends with "@token", suggest matching groups
  function onInput(v: string) {
    setInput(v);
    const m = v.match(/@([^\s@]*)$/);
    if (m) {
      const tok = m[1].toLowerCase();
      setMention(handles.filter((h) => h.toLowerCase().includes(tok)).slice(0, 6));
    } else setMention([]);
  }
  function pickHandle(h: string) {
    setInput((v) => v.replace(/@([^\s@]*)$/, `@${h} `));
    setMention([]);
    inputRef.current?.focus();
  }

  const selected = agents.find((a) => a.id === agentId) || null;

  // apply one SSE event to the LAST (assistant) message — shared by live send + run resume.
  function applyEvent(ev: any) {
    setMessages((prev) => {
      const next = [...prev];
      const a = { ...next[next.length - 1] };
      if (ev.type === "token") a.content += ev.text || "";
      else if (ev.type === "thinking") a.thinking = [...(a.thinking || []), { phase: ev.phase, text: ev.text }];
      else if (ev.type === "tool") a.tools = [...(a.tools || []), { name: ev.name, label: ev.label }];
      else if (ev.type === "clarify") {
        // origin = the user question these choices refine into a follow-up
        const origin = [...next].reverse().find((m) => m.role === "user")?.content || "";
        a.clarify = { prompt: ev.prompt, options: ev.options || [], multi: !!ev.multi, origin };
      }
      else if (ev.type === "suggestions") a.suggestions = (ev.items || []) as string[];
      else if (ev.type === "subagent") {
        const list = [...(a.subagents || [])];
        const card: SubAgent = { id: ev.id, title: ev.title, status: ev.status, sources: ev.sources, steps: ev.steps };
        const k = list.findIndex((s) => s.id === ev.id);
        if (k >= 0) list[k] = card; else list.push(card);
        a.subagents = list;
      }
      else if (ev.type === "artifact" && ev.artifact) {
        const dup = (a.artifacts || []).some((x) => x.title === ev.artifact.title);
        if (!dup) a.artifacts = [...(a.artifacts || []), ev.artifact as Artifact];
      }
      else if (ev.type === "citation") {
        const cite = toCitation(ev);
        const dup = (a.citations || []).some((c) => c.source === cite.source && c.url === cite.url);
        if (!dup) a.citations = [...(a.citations || []), cite];
      }
      // done: guardrail flag + the evidence set (which [n] actually backed the answer)
      else if (ev.type === "done") {
        if (ev.refused) a.refused = true;
        if (ev.audit) a.audit = ev.audit;   // QT-2 — gates share-card minting
        if (ev.standing_offer) a.standing_offer = ev.standing_offer;   // M-SA
        if (Array.isArray(ev.used)) a.used = ev.used;
        // PH-PROV3d: the done list is authoritative — its citations carry the evidence
        // image re-anchored on the figure the answer actually cited. Replace the streamed set.
        if (Array.isArray(ev.citations) && ev.citations.length) {
          a.citations = ev.citations.map(toCitation);
        }
        // PH-VIZ-2: the done list carries the chart artifacts enriched with sourced
        // event markers + price lines (added after later tool results landed).
        if (Array.isArray(ev.artifacts) && ev.artifacts.length) {
          a.artifacts = ev.artifacts as Artifact[];
        }
      }
      next[next.length - 1] = a;
      return next;
    });
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight);
  }

  // Read an SSE body and apply events to the displayed assistant bubble. Generation lives
  // server-side, so if the user navigates to another conversation we just STOP rendering here
  // (the run keeps going); re-entering resumes it. `initialConv` is the conversation we expect
  // (null for a brand-new chat — learned from the first `run` event).
  async function consumeStream(body: ReadableStream<Uint8Array>, initialConv: string | null) {
    let myConv = initialConv;
    if (myConv) streamConvRef.current = myConv;
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = block.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev: any;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          if (ev.type === "run") {  // first event: learn conversation id (new chats) + claim the stream
            myConv = ev.conversation_id || myConv;
            if (myConv) { setConversationId(myConv); streamConvRef.current = myConv; if (viewConvRef.current === null) viewConvRef.current = myConv; }
            continue;
          }
          // user moved to a different conversation → stop rendering (the run keeps generating server-side)
          if (myConv && viewConvRef.current !== myConv) { try { await reader.cancel(); } catch {} return; }
          if (ev.type === "conversation") { setConversationId(ev.id); continue; }
          applyEvent(ev);
        }
      }
    } finally {
      if (streamConvRef.current === myConv) streamConvRef.current = null;  // free it for a later re-tail
      if (viewConvRef.current === myConv) setBusy(false);                  // only if we're still here
    }
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    const history: Msg[] = [...messages, { role: "user", content: text }];
    const startConv = conversationId;  // may be null → a new conversation
    setMessages([...history, { role: "assistant", content: "", tools: [], citations: [] }]);
    setInput("");
    setBusy(true);
    setFocusIdx(null);  // panel follows the new answer as its assets stream in
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: history.map((m) => ({ role: m.role, content: m.content })),
          agent_id: agentId || null,
          conversation_id: startConv,  // resume/append to the same conversation
        }),
      });
      if (!res.body) throw new Error("no stream");
      await consumeStream(res.body, startConv);
    } catch {
      setMessages((prev) => {
        const next = [...prev];
        next[next.length - 1] = { ...next[next.length - 1], content: "(문제가 발생했어요. 잠시 후 다시 시도해 주세요.)" };
        return next;
      });
      setBusy(false);
    } finally {
      loadHistory();  // refresh the sidebar history (new conversation title shows up)
    }
  }

  // Resume a conversation whose answer is still generating server-side: append a live
  // assistant bubble and tail the background run (replays buffered events, then live).
  async function tailRun(convId: string, runId: string) {
    if (streamConvRef.current === convId) return;  // already streaming this one
    setBusy(true);
    setMessages((prev) => [...prev, { role: "assistant", content: "", tools: [], citations: [] }]);
    try {
      const res = await fetch(`/api/runs/${runId}/stream?from=0`);
      if (res.body) await consumeStream(res.body, convId);
      else setBusy(false);
    } catch { setBusy(false); }
    finally { loadHistory(); }
  }

  function onSaved(saved: Agent, deletedId?: string) {
    setBuilder({ open: false, base: null });
    loadAgents();
    if (deletedId && agentId === deletedId) setAgentId("");
    else if (!deletedId) setAgentId(saved.id);
  }

  // RIGHT CONTEXT PANEL focus: a pinned turn (focusIdx) wins; otherwise the panel tracks the
  // latest assistant answer so a live stream's assets fill it. `panelStreaming` is true only
  // while that latest answer is still generating.
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { lastAssistantIdx = i; break; }
  }
  const panelIdx = focusIdx != null && messages[focusIdx]?.role === "assistant" ? focusIdx : lastAssistantIdx;
  const panelMsg = panelIdx >= 0 ? messages[panelIdx] : null;
  // LG-3: an [n] click in answer i pins that answer to the panel AND flashes its card there.
  const citeClickFor = (i: number) => (n: number) => {
    setFocusIdx(i);
    setFlashCite({ n, ts: Date.now() });
  };
  const [shareArt, setShareArt] = useState<Artifact | null>(null);  // SH-2 share sheet
  const [loadError, setLoadError] = useState<string | null>(null);  // IMP-5: conv-load failure banner
  const panelStreaming = busy && panelIdx === messages.length - 1;

  return (
    <FeaturesProvider value={features}>
    {onboarded === false && (
      <Onboarding onDone={() => { setOnboarded(true); setView("explore"); loadHandles(); }} />
    )}
    {shareArt && (
      <ShareSheet a={shareArt} audit={panelMsg?.audit ?? null} onClose={() => setShareArt(null)} />
    )}
    <div className={`shell ${view === "explore" && messages.length > 0 ? "with-ctx" : "no-right"}`}
      style={view === "explore" && messages.length > 0 ? { gridTemplateColumns: `210px minmax(0,1fr) ${ctxWidth}px` } : undefined}>
      <nav className="rail">
        <div className="rail-brand"><span className="mascot" aria-hidden /><span className="wordmark">ValueGraph</span></div>
        <button className="rail-new" onClick={newChat}>
          <span className="ic">✎</span><span>탐구 시작하기</span>
        </button>
        {features.dashboard && (
          <button className={`rail-item ${view === "dashboard" ? "on" : ""}`} onClick={() => setView("dashboard")}>
            <span className="ic">📊</span><span className="lbl">대시보드</span>
          </button>
        )}
        <button className={`rail-item ${view === "notes" ? "on" : ""}`} onClick={() => setView("notes")}>
          <span className="ic">📓</span><span className="lbl">노트</span>
        </button>
        <button className={`rail-item ${view === "watch" ? "on" : ""}`} onClick={() => setView("watch")}>
          <span className="ic">⭐</span><span className="lbl">관심종목</span>
        </button>
        {features.alerts && (
          <button className={`rail-item ${view === "bot" ? "on" : ""}`} onClick={() => setView("bot")}>
            <span className="ic">🔔</span><span className="lbl">알림봇</span>
          </button>
        )}
        {convs.length > 0 && (
          <div className="rail-hist">
            <div className="rail-hist-h">최근 대화</div>
            {convs.slice(0, 12).map((c) => (
              <button key={c.id} className={`rail-conv ${c.id === conversationId ? "on" : ""}`}
                title={c.title} onClick={() => openConversation(c.id)}>{c.title || "(제목 없음)"}</button>
            ))}
          </div>
        )}
        <div className="rail-spacer" />
        <div className="rail-foot">
          <span className="acct-ava" aria-hidden />
          <div className="acct-meta">
            <span className="acct-name" title={name}>{(name?.split("@")[0] ?? "me").slice(0, 12)}</span>
            <span className="acct-sub">tenant ✓</span>
          </div>
          <a href="/api/auth/signout" title="로그아웃">↩</a>
        </div>
      </nav>

      <div className="main">
        {view === "watch" ? (
          <Watchlists embedded onChanged={loadHandles} />
        ) : view === "notes" ? (
          <NotebookView onShare={(n) => setShareArt(n as unknown as Artifact)} />
        ) : view === "dashboard" && features.dashboard ? (
          <BoardCanvas onEvidence={setViewer} />
        ) : view === "bot" && features.alerts ? (
          <BotHome onOpenDashboard={features.dashboard ? () => setView("dashboard") : undefined} />
        ) : (
          <>
            <header className="top">
              <div className="desk-id">
                <Mascot />
                <FreshnessDot f="fresh" />
                <span className="explore-title">탐구<span className="explore-sub"> — 출처와 함께 분석해요</span></span>
              </div>
            </header>

            <main className="chat" ref={scrollRef}>
              {messages.length === 0 && (
                <div className="empty">
                  {/* ASK-6: 물어보기 엔트리 — 종목 탭 → 온디맨드 분석거리 3개 · 뉴스 질문 피드(10분 캐시).
                      모든 탭은 컴포저를 채운다 (auto-send 금지); 접속 시 LLM 0회. */}
                  <CockpitEntry
                    onPick={(q) => { setInput(q); inputRef.current?.focus(); }}
                    onQuestions={setTodayQs}
                    onEvidence={setViewer}
                  />
                </div>
              )}

              {loadError && (
                <div className="load-error" role="alert">
                  대화를 불러오지 못했어요.
                  <button className="chip" onClick={() => openConversation(loadError)}>다시 시도</button>
                </div>
              )}
              {messages.map((m, i) => (
                <div key={i} className={`msg ${m.role} ${m.role === "assistant" && panelIdx === i ? "focused" : ""}`}>
                  {m.role === "assistant" && (m.thinking?.length || 0) > 0 && (
                    busy && i === messages.length - 1
                      ? <ThinkingLive steps={m.thinking!} />
                      : <details className="thinking-log">
                          <summary>🧠 분석 과정 · {m.thinking!.length}단계</summary>
                          {m.thinking!.map((t, j) => <div key={j} className="tl-step done"><span className="tl-ic">✓</span>{t.text}</div>)}
                        </details>
                  )}
                  {m.role === "assistant" && (m.subagents?.length || 0) > 0 && (
                    <SubAgentCards subs={m.subagents!} />
                  )}
                  {m.role === "assistant" ? (
                    // Click the answer to pin its evidence in the right context panel.
                    <div
                      className="answer-focusable"
                      role="button"
                      tabIndex={0}
                      aria-pressed={panelIdx === i}
                      onClick={() => setFocusIdx(i)}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFocusIdx(i); } }}
                    >
                      <div className="bubble">
                        {m.content
                          ? <AnswerArticle content={m.content} artifacts={m.artifacts}
                              ledger={(m.audit?.ledger ?? []) as LedgerRow[]}
                              streaming={busy && i === messages.length - 1}
                              mdComponents={makeMdComponents(panelIdx === i ? hoverCite : null, setHoverCite, citeClickFor(i),
                                { rows: (m.audit?.ledger ?? []) as LedgerRow[], citations: m.citations ?? [],
                                  onEvidence: setViewer, onPinLedger: pinLedger })}
                              onEvidence={setViewer} onPin={pinArtifact} onShare={(a) => setShareArt(a)} />
                          : (busy && !(m.thinking?.length) ? "…" : "")}
                      </div>
                      {(() => {
                        const nArt = m.artifacts?.length || 0;
                        const nUsed = evidenceOf(m).length;
                        const nTool = uniqueTools(m.tools).length;
                        if (!(nArt || nUsed || nTool)) return null;
                        return (
                          <div className="ctx-hint">
                            {nArt > 0 && <span className="ch-stat">📊 차트·표 {nArt}</span>}
                            {nUsed > 0 && <span className="ch-stat">🔗 근거 {nUsed}</span>}
                            {nTool > 0 && <span className="ch-stat">🔧 도구 {nTool}</span>}
                            <span className="ch-go">{panelIdx === i ? "근거 패널에 표시 중" : "근거 패널에서 보기 →"}</span>
                          </div>
                        );
                      })()}
                    </div>
                  ) : (
                    <div className="bubble">{m.content}</div>
                  )}
                  {m.role === "assistant" && m.clarify && (
                    <ClarifyChips clarify={m.clarify} disabled={busy}
                      onSubmit={(labels) => send(`${m.clarify!.origin} — ${labels.join(", ")}`)} />
                  )}
                  {m.role === "assistant" && m.refused && (
                    <GuardrailLabel>매수·매도, 목표가, 전망은 답하지 않아요 — 신뢰를 위해 항상 지키는 원칙이에요.</GuardrailLabel>
                  )}
                  {m.role === "assistant" && (m.suggestions?.length || 0) > 0 && (
                    <div className="followups">
                      <div className="fu-label">이어서 더 파고들기</div>
                      <div className="fu-list">
                        {m.suggestions!.map((q, j) => (
                          <button key={j} type="button" className="fu-chip" disabled={busy} onClick={() => send(q)}>
                            {q} <span className="fu-arrow">→</span>
                          </button>
                        ))}
                        {m.standing_offer && (
                          <button type="button" className={`fu-chip standing ${standingDone.has(i) ? "on" : ""}`}
                            disabled={busy || standingDone.has(i)}
                            title="이 데이터가 업데이트되면 다음에 왔을 때 알려드려요"
                            onClick={() => void subscribeStanding(i, m)}>
                            {standingDone.has(i) ? "✓ 지켜보는 중 — 갱신되면 데스크에 알림" : "🔔 이 질문 계속 지켜보기"}
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </main>

            <footer className="composer">
              {mention.length > 0 && (
                <div className="mention">
                  {mention.map((h, i) => (
                    <div key={h} className={`mention-item ${i === 0 ? "on" : ""}`}
                      onMouseDown={(e) => { e.preventDefault(); pickHandle(h); }}>
                      <span className="h">@{h}</span>
                      <span className="c">관심 그룹</span>
                    </div>
                  ))}
                </div>
              )}
              <form className={messages.length === 0 ? "hero" : undefined}
                onSubmit={(e) => { e.preventDefault(); if (mention.length) { pickHandle(mention[0]); return; } send(input); }}>
                <input ref={inputRef} className="input" value={input} onChange={(e) => onInput(e.target.value)}
                  onBlur={() => setTimeout(() => setMention([]), 120)}
                  placeholder={messages.length === 0 && todayQs.length
                        ? `오늘: “${todayQs[phIdx % todayQs.length]}”`
                        : "무엇이든 물어보세요 — @그룹으로 관심종목을 부를 수 있어요"} disabled={busy} />
                <Button disabled={busy || !input.trim()}>보내기</Button>
              </form>
              {(input.match(/@([^\s@]+)/g) ?? []).length > 0 && (
                <div className="composer-meta">
                  {(input.match(/@([^\s@]+)/g) ?? []).slice(0, 3).map((h) => (
                    <Chip key={h} tone="accent">{h}</Chip>
                  ))}
                </div>
              )}
              <div className="disclaimer">투자 자문이 아니며, 가격 예측을 제공하지 않습니다.</div>
            </footer>
          </>
        )}
      </div>

      {view === "explore" && messages.length > 0 && (
        <ContextPanel
          hoverCite={hoverCite}
          setHoverCite={setHoverCite}
          flashCite={flashCite}
          msg={panelMsg}
          streaming={panelStreaming}
          onEvidence={setViewer}
          onPinArtifact={pinArtifact}
          onShareArtifact={(a) => setShareArt(a)}
          onPinCitation={pinCitation}
          onResizeStart={startCtxResize}
        />
      )}

      {builder.open && (
        <AgentBuilder
          base={builder.base}
          categories={categories}
          onClose={() => setBuilder({ open: false, base: null })}
          onSaved={onSaved}
        />
      )}

      {viewer && <SourceViewer c={viewer} onClose={() => setViewer(null)}
        onQuote={(q) => { setViewer(null); setShareArt(q); }} />}
      {nbPin && (
        <NotebookPicker pin={nbPin} onClose={() => setNbPin(null)} />
      )}
    </div>
    </FeaturesProvider>
  );
}
