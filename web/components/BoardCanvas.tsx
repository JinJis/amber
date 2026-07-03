"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkCjkEmphasis } from "../lib/markdown";
import GridLayout, { WidthProvider, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { ArtifactCard, type Artifact } from "./ArtifactCard";
import { SourceCard, type Citation } from "./SourceCard";
import AlertSheet, { type AlertDraft } from "./AlertSheet";
import WidgetGallery, { type AddedWidget } from "./WidgetGallery";
import { Button, FreshnessDot } from "./ui";
import { ChannelStatus, cadenceLabel, isPeriodic, triggerFromMeta } from "@/lib/alerts";
import { widgetKind } from "@/lib/widgets";
import { useFeatures } from "@/lib/features-context";

// react-grid-layout drives the placement: a true column grid with collision resolution,
// auto-packing (no gaps), a magnetic drop placeholder, smooth snap animation, and resize reflow.
const RGL = WidthProvider(GridLayout);

type Board = { id: string; name: string };
type Item = { id: string; spec: any; x: number | null; y: number | null; w: number | null; h: number | null };
type Template = { id: string; name: string; description?: string; market?: string | null; widgets: any[] };

const COLS = 12;
const ROW_H = 36;        // px per grid row
// default + min sizes per widget kind, in GRID UNITS (cols × rows).
const GDEF = {
  artifact: { w: 4, h: 7, minW: 3, minH: 4 },
  source: { w: 4, h: 6, minW: 3, minH: 4 },
  text: { w: 3, h: 4, minW: 2, minH: 2 },
};
// stored coords are grid units once a board is touched; legacy px (w>COLS/h>40) → re-flow.
const isGridCoord = (it: Item) =>
  it.x != null && it.y != null && it.w != null && it.h != null && it.w <= COLS && it.h <= 40;

// Build the react-grid-layout from items: keep placed (grid-unit) widgets, auto-flow the rest into
// the next free cells so nothing overlaps; RGL then compacts upward to remove gaps.
function toLayout(items: Item[]): Layout[] {
  const baseY = items.filter(isGridCoord).reduce((m, p) => Math.max(m, (p.y ?? 0) + (p.h ?? 0)), 0);
  let cx = 0, cy = 0, rowH = 0;
  return items.map((it) => {
    const d = GDEF[widgetKind(it.spec) as keyof typeof GDEF];
    if (isGridCoord(it)) {
      return { i: it.id, x: it.x!, y: it.y!, w: it.w!, h: it.h!, minW: d.minW, minH: d.minH };
    }
    if (cx + d.w > COLS) { cx = 0; cy += rowH; rowH = 0; }
    const node = { i: it.id, x: cx, y: baseY + cy, w: d.w, h: d.h, minW: d.minW, minH: d.minH };
    cx += d.w; rowH = Math.max(rowH, d.h);
    return node;
  });
}

function TextBlock({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <textarea className="bc-text" autoFocus defaultValue={value}
        placeholder="마크다운으로 메모… ( ## 제목 · **굵게** · - 목록 · [링크](url) )"
        onMouseDown={(e) => e.stopPropagation()}
        onBlur={(e) => { setEditing(false); onSave(e.target.value); }} />
    );
  }
  return (
    <div className="bc-md md" onClick={() => setEditing(true)} title="클릭해 편집">
      {value ? <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkEmphasis]}>{value}</ReactMarkdown> : <span className="bc-ph">클릭해 메모 작성 (마크다운 지원)</span>}
    </div>
  );
}

function InlineEdit({ value, placeholder, onSave, className }: {
  value: string; placeholder: string; onSave: (v: string) => void; className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);
  useEffect(() => { setV(value); }, [value]);
  if (editing) {
    return (
      <input className={`bc-edit ${className || ""}`} autoFocus value={v}
        onChange={(e) => setV(e.target.value)}
        onMouseDown={(e) => e.stopPropagation()} onClick={(e) => e.stopPropagation()}
        onBlur={() => { setEditing(false); if (v !== value) onSave(v); }}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") { setV(value); setEditing(false); } }} />
    );
  }
  return (
    <span className={`bc-editable ${className || ""}`} title="클릭해 수정"
      onClick={(e) => { e.stopPropagation(); setEditing(true); }}>
      {value || <span className="bc-ph">{placeholder}</span>}
    </span>
  );
}

// The dashboard (홈): a board's WIDGETS on a react-grid-layout grid — drag to repack, resize to
// reflow, magnetic snap. Every widget carries source · as_of · freshness + a 🔔 per-widget alert.
// Empty boards offer the template gallery (F2); ＋위젯 opens the all-sources gallery (F4).
export default function BoardCanvas({ onEvidence }: { onEvidence?: (c: Citation) => void }) {
  const { alerts: alertsEnabled } = useFeatures();   // 알림봇 off → hide alert bells on the board
  const [boards, setBoards] = useState<Board[]>([]);
  const [active, setActive] = useState<string>("");
  const [items, setItems] = useState<Item[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);
  const [gallery, setGallery] = useState(false);
  const [applying, setApplying] = useState(false);
  const [dragging, setDragging] = useState(false);  // suppress text selection while drag/resizing
  const [alertDraft, setAlertDraft] = useState<AlertDraft | null>(null);
  const itemsRef = useRef<Item[]>([]);
  itemsRef.current = items;
  const populatedRef = useRef<Set<string>>(new Set());

  const loadBoards = useCallback(async () => {
    const r = await fetch("/api/boards");
    if (!r.ok) return;
    const bs = ((await r.json()).boards ?? []) as Board[];
    setBoards(bs);
    setActive((cur) => (cur && bs.some((b) => b.id === cur) ? cur : bs[0]?.id ?? ""));
  }, []);
  const loadItems = useCallback(async (bid: string) => {
    if (!bid) return;
    const r = await fetch(`/api/board?board_id=${encodeURIComponent(bid)}`);
    if (r.ok) {
      const pinned = ((await r.json()).pinned ?? []) as Item[];
      const seen = new Set<string>();
      setItems(pinned.filter((p) => (seen.has(p.id) ? false : seen.add(p.id))));  // de-dup by id
      setLastRefresh(new Date().toLocaleTimeString());
    }
  }, []);
  const loadChannels = useCallback(async () => {
    try { const r = await fetch("/api/channels"); if (r.ok) setChannels((await r.json()).channels ?? []); } catch {}
  }, []);

  useEffect(() => { loadBoards(); loadChannels(); }, [loadBoards, loadChannels]);
  useEffect(() => { if (active) loadItems(active); }, [active, loadItems]);
  useEffect(() => {
    if (items.length === 0 && templates.length === 0) {
      fetch("/api/templates").then((r) => (r.ok ? r.json() : { templates: [] })).then((d) => setTemplates(d.templates ?? [])).catch(() => {});
    }
  }, [items.length, templates.length]);

  // Persist the grid after a drag/resize: RGL gives the full layout (incl. items it repacked);
  // write back every widget whose grid coords changed (grid units).
  // Start of a drag/resize: flag it (CSS kills text selection) + clear any selection the
  // mousedown already began, so other widgets' text doesn't get highlighted while dragging.
  function startGesture() {
    setDragging(true);
    try { window.getSelection()?.removeAllRanges(); } catch {}
  }
  function persistLayout(layout: Layout[]) {
    setDragging(false);
    const cur = itemsRef.current;
    setItems(cur.map((it) => {
      const l = layout.find((n) => n.i === it.id);
      return l ? { ...it, x: l.x, y: l.y, w: l.w, h: l.h } : it;
    }));
    for (const l of layout) {
      const it = cur.find((i) => i.id === l.i);
      if (it && (it.x !== l.x || it.y !== l.y || it.w !== l.w || it.h !== l.h)) {
        void fetch(`/api/board/${l.i}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ x: l.x, y: l.y, w: l.w, h: l.h }) });
      }
    }
  }
  async function removeItem(id: string) {
    setItems((p) => p.filter((i) => i.id !== id));
    await fetch(`/api/board/${id}`, { method: "DELETE" });
  }
  const refreshItem = useCallback(async (id: string) => {
    const r = await fetch(`/api/board/${id}/refresh`, { method: "POST" });
    if (r.ok) { const fresh = await r.json(); setItems((p) => p.map((i) => (i.id === id ? { ...i, spec: fresh.spec } : i))); }
  }, []);
  async function refreshAll() {
    setLastRefresh(new Date().toLocaleTimeString());
    await Promise.allSettled(itemsRef.current.filter((i) => i.spec?.tool).map((i) => refreshItem(i.id)));
  }
  useEffect(() => {
    const dataless = items.filter((it) => it.spec?.tool && !populatedRef.current.has(it.id)
      && !(it.spec?.series?.length || it.spec?.candles?.length || it.spec?.table?.length || it.spec?.sections?.length));
    if (!dataless.length) return;
    dataless.forEach((it) => populatedRef.current.add(it.id));
    void Promise.allSettled(dataless.map((it) => refreshItem(it.id)));
  }, [items, refreshItem]);

  async function saveSpec(id: string, patch: Record<string, any>) {
    let merged: any = null;
    setItems((p) => p.map((i) => { if (i.id !== id) return i; merged = { ...i.spec, ...patch }; return { ...i, spec: merged }; }));
    if (merged) await fetch(`/api/board/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ spec: merged }) });
  }
  async function addText() {
    const r = await fetch("/api/board", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: { kind: "text", text: "" }, board_ids: [active] }) });  // RGL auto-places it
    if (r.ok) loadItems(active);
  }
  async function newBoard() {
    const name = prompt("새 대시보드 이름");
    if (!name?.trim()) return;
    const r = await fetch("/api/boards", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
    if (r.ok) { const b = await r.json(); await loadBoards(); setActive(b.id); }
  }
  async function renameBoard() {
    const cur = boards.find((b) => b.id === active);
    const name = prompt("대시보드 이름 변경", cur?.name ?? "");
    if (!name?.trim()) return;
    await fetch(`/api/boards/${active}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: name.trim() }) });
    loadBoards();
  }
  async function deleteBoard() {
    if (!confirm("이 대시보드와 위젯을 모두 삭제할까요?")) return;
    await fetch(`/api/boards/${active}`, { method: "DELETE" });
    setActive(""); await loadBoards();
  }
  async function applyTemplate(tid: string) {
    if (applying) return;
    setApplying(true);
    try {
      let bid = active;
      if (!bid) { await newBoard(); bid = active; }
      const r = await fetch("/api/board/from-template", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_id: tid, board_id: bid || undefined }) });
      if (r.ok) { const d = await r.json(); setActive(d.board_id); await loadItems(d.board_id); await refreshAll(); }
    } finally { setApplying(false); }
  }
  // Root alert (step 5): a single board DIGEST that periodically summarizes the board's periodic
  // widgets (one-shot widgets are excluded server-side when the digest renders).
  function openBoardAlert() {
    setAlertDraft({
      scope: "board", board_id: active, trigger_type: "digest",
      name: `${activeBoard?.name ?? "대시보드"} · 주기성 위젯 요약`,
      source_spec: { deeplink: `/?board=${active}` },
    });
  }
  function openWidgetAlert(it: Item) {
    const target = it.spec?.args?.ticker || it.spec?.ticker || it.spec?.title;
    setAlertDraft({
      scope: "widget", board_id: active, pin_id: it.id, name: it.spec?.title,
      trigger_type: triggerFromMeta(it.spec), params: { target },
      source_spec: { tool: it.spec?.tool, args: it.spec?.args, source: it.spec?.source, deeplink: `/?board=${active}&widget=${it.id}` },
    });
  }
  function onWidgetAdded(w: AddedWidget) {
    setGallery(false);
    loadItems(active);
    if (w.withAlert && w.pinId) {
      setAlertDraft({
        scope: "widget", board_id: active, pin_id: w.pinId, name: w.spec?.title,
        trigger_type: triggerFromMeta(w.spec), params: { target: w.spec?.args?.ticker || w.spec?.title },
        source_spec: { tool: w.spec?.tool, args: w.spec?.args, source: w.spec?.source, deeplink: `/?board=${active}&widget=${w.pinId}` },
      });
    }
  }

  const activeBoard = boards.find((b) => b.id === active);

  return (
    <div className="board">
      <div className="dash-tabs">
        {boards.map((b) => (
          <button key={b.id} className={`board-tab ${b.id === active ? "on" : ""}`} onClick={() => setActive(b.id)}>{b.name}</button>
        ))}
        <button className="board-tab add" onClick={newBoard} title="새 대시보드">＋ 대시보드</button>
      </div>

      <div className="dash-bar">
        <button className="dash-chip" onClick={() => void refreshAll()} title="지금 전체 갱신">↻ 갱신</button>
        <span className="grow" />
        {active && (
          <>
            {alertsEnabled && (
              <button className="dash-bell" onClick={openBoardAlert} title="이 보드의 주기성 위젯을 한 번에 요약 — 정기 알림">🔔 주기성 위젯 요약</button>
            )}
            <Button variant="ghost" size="sm" onClick={() => alert("공유 링크는 곧 제공됩니다.")}>↗ 공유</Button>
            <Button size="sm" onClick={() => setGallery(true)}>＋ 위젯</Button>
          </>
        )}
      </div>

      {active && (
        <div className="dash-meta">
          <span className="fdot fresh" /> 실시간 · 마지막 갱신 {lastRefresh ?? "—"} · {items.length}개 위젯
          <span className="grow" />
          <button className="dash-link" onClick={addText}>＋ 메모</button>
          <button className="dash-link" onClick={renameBoard}>이름변경</button>
          <button className="dash-link" onClick={deleteBoard}>삭제</button>
        </div>
      )}

      {items.length === 0 ? (
        <div className="dash-empty">
          <div className="dash-hero">
            <h2>나만의 실시간 대시보드를 시작하세요</h2>
            <p>템플릿으로 바로 채우거나, <b>탐색</b>에서 자연어로 찾아 위젯을 추가하세요.</p>
          </div>
          <div className="tpl-grid">
            {templates.map((t) => (
              <button key={t.id} type="button" className="tpl-card" disabled={applying} onClick={() => applyTemplate(t.id)}>
                <div className="tpl-prev"><span /><span /><span /><span /></div>
                <div className="tpl-name">{t.name}</div>
                <div className="tpl-desc">{t.description}</div>
                <div className="tpl-cta">{applying ? "추가 중…" : "이 템플릿으로 시작 →"}</div>
              </button>
            ))}
            <button type="button" className="tpl-card blank" onClick={() => setGallery(true)}>
              <div className="tpl-blank-ic">＋</div>
              <div className="tpl-name">빈 캔버스로</div>
              <div className="tpl-desc">위젯을 직접 추가</div>
            </button>
          </div>
        </div>
      ) : (
        <div className="board-canvas">
          <RGL className={`dash-grid${dragging ? " dragging" : ""}`} layout={toLayout(items)} cols={COLS} rowHeight={ROW_H}
            margin={[12, 12]} containerPadding={[4, 4]} draggableHandle=".bc-drag"
            isBounded={false} compactType="vertical" resizeHandles={["se"]}
            onDragStart={startGesture} onResizeStart={startGesture}
            onDragStop={persistLayout} onResizeStop={persistLayout}>
            {items.map((it) => {
              const kind = widgetKind(it.spec);
              const src = it.spec?.source as string | undefined;
              const asOf = it.spec?.as_of as string | undefined;
              // a widget is alertable iff its datasource recurs (cadence != one_shot). Text memos
              // are never datasource-backed → no periodicity, no bell.
              const periodic = kind !== "text" && isPeriodic(it.spec);
              const cad = it.spec?.cadence as string | undefined;
              return (
                <div key={it.id} className="bc-item">
                  <div className="bc-card">
                    <div className="bc-drag">
                      <span className="bc-grip">⠿</span>
                      <InlineEdit className="bc-title" value={it.spec?.title || ""}
                        placeholder={kind === "text" ? "메모 제목" : "제목"} onSave={(v) => saveSpec(it.id, { title: v })} />
                      {kind === "artifact" && <FreshnessDot f={it.spec?.freshness ?? undefined} />}
                      {kind !== "text" && cad && (
                        <span className={`bc-cadence ${periodic ? "periodic" : "oneshot"}`}
                          title={periodic ? "주기성 데이터 — 알림봇 설정 가능" : "단발성 데이터 — 값으로 표시 (알림 없음)"}>
                          {periodic ? `↻ ${cadenceLabel(cad)}` : "단발성"}
                        </span>
                      )}
                      <span className="grow" />
                      {periodic && alertsEnabled && (
                        <button className="bc-btn" title="이 위젯에 알림 — 주기성 데이터" onClick={() => openWidgetAlert(it)}>🔔</button>
                      )}
                      {kind === "artifact" && it.spec?.tool && (
                        <button className="bc-btn" title="새로고침" onClick={() => refreshItem(it.id)}>↻</button>
                      )}
                      <button className="bc-btn" title="삭제" onClick={() => removeItem(it.id)}>✕</button>
                    </div>
                    <div className="bc-body">
                      {kind === "artifact" && <ArtifactCard a={it.spec as Artifact} onEvidence={onEvidence} hideTitle bare />}
                      {kind === "source" && <SourceCard c={it.spec as Citation} onExpand={onEvidence} hideTitle />}
                      {kind === "text" && <TextBlock value={it.spec?.text ?? ""} onSave={(v) => saveSpec(it.id, { text: v })} />}
                    </div>
                    {kind !== "text" && (() => {
                      // DEBUG: the widget's data pipeline — where the data came from, which tool,
                      // and the exact args/query that ↻ re-runs to fetch the latest. Periodic
                      // widgets MUST have a refreshable tool; flag it loudly if they don't.
                      const tool = it.spec?.tool as string | undefined;
                      const args = it.spec?.args as Record<string, unknown> | undefined;
                      const argStr = args && Object.keys(args).length ? JSON.stringify(args) : null;
                      return (
                        <details className="bc-debug mono">
                          <summary>🔧 {src || "출처?"}{tool ? ` · ${tool}` : " · 툴 없음"}{asOf ? ` · ${asOf}` : ""}</summary>
                          <div className="bc-debug-body">
                            <div><span className="k">출처</span>{src || "—"}</div>
                            <div><span className="k">툴</span>{tool || "(refresh 불가 — 연결된 툴 없음)"}</div>
                            <div><span className="k">검색·파라미터</span>{argStr || "—"}</div>
                            <div><span className="k">주기성</span>{cad ? `${cadenceLabel(cad)} (${cad})` : "—"}{periodic ? " · 알림 가능" : " · 단발성"}</div>
                            {asOf && <div><span className="k">as_of</span>{asOf}</div>}
                            {periodic && !tool && (
                              <div className="bc-debug-warn">⚠ 주기성으로 표시되지만 refresh할 툴이 없습니다 — 갱신/알림이 데이터를 못 가져옵니다.</div>
                            )}
                            <div className="bc-debug-note">
                              {tool ? "↻ 새로고침은 위 툴·파라미터를 그대로 다시 실행해 최신 데이터를 가져옵니다." : ""}
                            </div>
                          </div>
                        </details>
                      );
                    })()}
                  </div>
                </div>
              );
            })}
          </RGL>
        </div>
      )}

      {gallery && active && (
        <WidgetGallery boardId={active} boardName={activeBoard?.name} onClose={() => setGallery(false)} onAdded={onWidgetAdded} />
      )}
      {alertDraft && alertsEnabled && (
        <AlertSheet initial={alertDraft} channels={channels} boardName={activeBoard?.name}
          widgetName={alertDraft.scope === "widget" ? alertDraft.name : undefined}
          onClose={() => setAlertDraft(null)} onChannelsChanged={loadChannels}
          onCreated={() => setAlertDraft(null)} />
      )}
    </div>
  );
}
