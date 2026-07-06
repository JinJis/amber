"use client";

// NB-3/NB-4 — the 노트 surface: notebook list (left) + the vertical block document (right).
// Pin blocks render in their native shapes (artifact card / source card / ledger row) as
// IMMUTABLE snapshots with an editable 왜-담았는지 memo; text blocks are the user's own
// markdown. ↑↓ reorders. 공유 = the existing SH pipeline (kind=note) — the public page
// separates 작성자 메모 from sourced evidence by layout (honesty by design). No alert UI.

import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArtifactCard } from "./ArtifactCard";
import { SourceCard } from "./SourceCard";
import { remarkCjkEmphasis } from "../lib/markdown";
import type { Artifact, Citation } from "../lib/types";

type Nb = { id: string; title: string; blocks: number; updated_at?: string | null };
type Block = { id: string; kind: string; position: number; note?: string | null;
  payload: Record<string, unknown>; created_at?: string | null };
type Doc = Nb & { block_list: Block[] };

function LedgerPinRow({ p }: { p: Record<string, unknown> }) {
  return (
    <div className="nb-ledger mono">
      <b>{String(p.raw ?? "")}</b>
      {p.citation_idx != null ? <span> · [{String(p.citation_idx)}]</span> : null}
      {p.source ? <span className="nb-lg-src"> {String(p.source)}</span> : null}
      {p.as_of ? <span className="nb-lg-src"> · as of {String(p.as_of)}</span> : null}
    </div>
  );
}

function BlockView({ b, onNote, onText, onMove, onDelete }: {
  b: Block;
  onNote: (note: string) => void;
  onText: (md: string) => void;
  onMove: (dir: -1 | 1) => void;
  onDelete: () => void;
}) {
  const [editingNote, setEditingNote] = useState(false);
  const [noteDraft, setNoteDraft] = useState(b.note ?? "");
  const [editingText, setEditingText] = useState(false);
  const [textDraft, setTextDraft] = useState(String(b.payload.md ?? ""));

  return (
    <div className={`nb-block ${b.kind}`} data-testid={`nb-block-${b.id}`}>
      <div className="nb-block-bar">
        <span className="nb-kind mono">{
          b.kind === "text" ? "✍ 메모" : b.kind === "pin_ledger" ? "🔢 수치" :
          b.kind === "pin_citation" ? "📄 출처" : "📊 차트·표"}</span>
        <span className="nb-tools">
          <button type="button" onClick={() => onMove(-1)} title="위로">↑</button>
          <button type="button" onClick={() => onMove(1)} title="아래로">↓</button>
          <button type="button" onClick={onDelete} title="제거">✕</button>
        </span>
      </div>

      {b.kind === "text" ? (
        editingText ? (
          <div className="nb-text-edit">
            <textarea value={textDraft} rows={5} onChange={(e) => setTextDraft(e.target.value)} />
            <div className="nb-edit-actions">
              <button className="chip" onClick={() => { onText(textDraft); setEditingText(false); }}>저장</button>
              <button className="chip" onClick={() => { setTextDraft(String(b.payload.md ?? "")); setEditingText(false); }}>취소</button>
            </div>
          </div>
        ) : (
          <div className="nb-text md" role="button" tabIndex={0} title="눌러서 수정할 수 있어요"
            onClick={() => setEditingText(true)}
            onKeyDown={(e) => { if (e.key === "Enter") setEditingText(true); }}>
            <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkEmphasis]}>{String(b.payload.md ?? "")}</ReactMarkdown>
          </div>
        )
      ) : (
        <>
          {b.kind === "pin_artifact" && <ArtifactCard a={b.payload as unknown as Artifact} bare />}
          {b.kind === "pin_citation" && <SourceCard c={b.payload as unknown as Citation} />}
          {b.kind === "pin_ledger" && <LedgerPinRow p={b.payload} />}
          {/* the WHY memo — the one habit this surface is designed around */}
          {editingNote ? (
            <div className="nb-note-edit">
              <input value={noteDraft} maxLength={200} placeholder="왜 담았나요?"
                onChange={(e) => setNoteDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") { onNote(noteDraft); setEditingNote(false); } }} />
              <button className="chip" onClick={() => { onNote(noteDraft); setEditingNote(false); }}>저장</button>
            </div>
          ) : (
            <button type="button" className={`nb-note ${b.note ? "" : "empty"}`}
              onClick={() => setEditingNote(true)}>
              {b.note ? <>메모: {b.note}</> : "＋ 왜 담았는지 한 줄"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

export default function NotebookView({ onShare }: {
  // NB-4: hand the note payload to the existing ShareSheet pipeline (kind=note).
  onShare?: (payload: { kind: "note"; title: string; blocks: Block[] }) => void;
}) {
  const [books, setBooks] = useState<Nb[]>([]);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [loading, setLoading] = useState(true);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/notebooks");
      const list: Nb[] = r.ok ? (await r.json()).notebooks ?? [] : [];
      setBooks(list);
      if (list.length && !doc) void open(list[0].id);
    } catch { setBooks([]); } finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function open(id: string) {
    try {
      const r = await fetch(`/api/notebooks/${encodeURIComponent(id)}`);
      if (r.ok) setDoc(await r.json());
    } catch { /* keep the current doc */ }
  }

  useEffect(() => { void loadList(); }, [loadList]);

  async function createBook() {
    const r = await fetch("/api/notebooks", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "새 리서치 노트" }),
    });
    if (r.ok) { const nb = await r.json(); await loadList(); await open(nb.id); }
  }

  async function patchBlock(bid: string, body: Record<string, unknown>) {
    if (!doc) return;
    await fetch(`/api/notebooks/${encodeURIComponent(doc.id)}/blocks/${encodeURIComponent(bid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    await open(doc.id);
  }

  async function removeBlock(bid: string) {
    if (!doc) return;
    await fetch(`/api/notebooks/${encodeURIComponent(doc.id)}/blocks/${encodeURIComponent(bid)}`, { method: "DELETE" });
    await open(doc.id);
  }

  async function move(b: Block, dir: -1 | 1) {
    if (!doc) return;
    const list = doc.block_list;
    const i = list.findIndex((x) => x.id === b.id);
    const j = i + dir;
    if (j < 0 || j >= list.length) return;
    // swap sparse positions (fall back to index*10 when equal)
    const pa = list[i].position, pb = list[j].position;
    await patchBlock(list[i].id, { position: pb === pa ? pb + dir : pb });
    await patchBlock(list[j].id, { position: pa });
  }

  async function addText() {
    if (!doc) return;
    await fetch(`/api/notebooks/${encodeURIComponent(doc.id)}/blocks`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "text", payload: { md: "메모를 적어보세요…" } }),
    });
    await open(doc.id);
  }

  return (
    <div className="nbview" data-testid="notebook-view">
      <aside className="nb-list">
        <div className="nb-list-h">
          노트북
          <button type="button" className="chip" onClick={() => void createBook()}>＋ 새 노트북</button>
        </div>
        {loading && <div className="nb-empty">불러오는 중…</div>}
        {!loading && books.length === 0 && (
          <div className="nb-empty">
            아직 노트북이 없어요.<br />채팅의 근거 패널에서 📌 를 눌러 담아보세요.
          </div>
        )}
        {books.map((b) => (
          <button key={b.id} type="button" className={`nb-item ${doc?.id === b.id ? "on" : ""}`}
            onClick={() => void open(b.id)}>
            {b.title} <span className="nbp-count mono">{b.blocks}</span>
          </button>
        ))}
      </aside>

      <section className="nb-doc">
        {doc ? (
          <>
            <div className="nb-doc-h">
              <input className="nb-title-input" defaultValue={doc.title} key={doc.id}
                onBlur={async (e) => {
                  const t = e.target.value.trim();
                  if (t && t !== doc.title) {
                    await fetch(`/api/notebooks/${encodeURIComponent(doc.id)}`, {
                      method: "PATCH", headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ title: t }),
                    });
                    await loadList();
                  }
                }} />
              <span style={{ flex: 1 }} />
              <button type="button" className="chip" onClick={() => void addText()}>＋ 메모</button>
              {onShare && doc.block_list.length > 0 && (
                <button type="button" className="chip"
                  onClick={() => onShare({ kind: "note", title: doc.title, blocks: doc.block_list })}>
                  ↗ 공유
                </button>
              )}
            </div>
            {doc.block_list.length === 0 && (
              <div className="nb-empty">비어 있어요 — 근거 패널의 📌 로 담거나 ＋ 메모로 시작하세요.</div>
            )}
            {doc.block_list.map((b) => (
              <BlockView key={b.id} b={b}
                onNote={(note) => void patchBlock(b.id, { note })}
                onText={(md) => void patchBlock(b.id, { md })}
                onMove={(dir) => void move(b, dir)}
                onDelete={() => void removeBlock(b.id)} />
            ))}
          </>
        ) : (
          !loading && <div className="nb-empty">왼쪽에서 노트북을 고르거나 새로 만들어보세요.</div>
        )}
      </section>
    </div>
  );
}
