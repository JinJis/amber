"use client";

// NB-2 — 📌 담기 시트: pick a notebook (recent list or create inline), write the one-line
// "왜 담았는지" note, and drop the SNAPSHOT in. The note field is the design's heart — it's
// what separates a research notebook from a scrapbook; skippable but always asked.

import { useEffect, useState } from "react";

export type PinPayload = {
  kind: "pin_artifact" | "pin_citation" | "pin_ledger";
  payload: Record<string, unknown>;
  title?: string;              // shown in the sheet header ("무엇을 담는지")
};

type Nb = { id: string; title: string; blocks: number };

export function NotebookPicker({ pin, onClose, onSaved }: {
  pin: PinPayload; onClose: () => void; onSaved?: (notebookId: string) => void;
}) {
  const [books, setBooks] = useState<Nb[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/notebooks");
        if (r.ok) {
          const list: Nb[] = (await r.json()).notebooks ?? [];
          setBooks(list);
          if (list.length) setSel(list[0].id);
        }
      } catch { /* the create-new path still works */ }
    })();
  }, []);

  async function save() {
    if (busy) return;
    setBusy(true);
    setError(false);
    try {
      let target = sel;
      if (!target) {
        const title = newTitle.trim() || "리서치 노트";
        const r = await fetch("/api/notebooks", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        });
        if (!r.ok) throw new Error("create failed");
        target = (await r.json()).id;
      }
      const r2 = await fetch(`/api/notebooks/${encodeURIComponent(target!)}/blocks`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: pin.kind, payload: pin.payload, note: note.trim() || null }),
      });
      if (!r2.ok) throw new Error("block failed");
      onSaved?.(target!);
      onClose();
    } catch { setError(true); setBusy(false); }
  }

  return (
    <div className="sv-backdrop" onClick={onClose}>
      <div className="nbp" onClick={(e) => e.stopPropagation()} data-testid="notebook-picker">
        <div className="nbp-h">
          <b>📌 노트에 담기</b>
          <button className="sv-x" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {pin.title ? <div className="nbp-what mono">{pin.title}</div> : null}

        <div className="nbp-books">
          {books.map((b) => (
            <button key={b.id} type="button" className={`nbp-book ${sel === b.id ? "on" : ""}`}
              onClick={() => setSel(b.id)}>
              {b.title} <span className="nbp-count mono">{b.blocks}</span>
            </button>
          ))}
          <button type="button" className={`nbp-book new ${sel === null ? "on" : ""}`}
            onClick={() => setSel(null)}>＋ 새 노트북</button>
        </div>
        {sel === null && (
          <input className="nbp-title" value={newTitle} placeholder="노트북 이름 (예: 반도체 리서치)"
            onChange={(e) => setNewTitle(e.target.value)} />
        )}

        <input className="nbp-note" value={note} maxLength={200}
          placeholder="왜 담나요? 한 줄 메모 (선택)"
          onChange={(e) => setNote(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); }} />

        {error && <p className="share-blocked">담지 못했습니다 — 잠시 후 다시 시도해주세요.</p>}
        <div className="nbp-actions">
          <button className="chip" onClick={() => void save()} disabled={busy}>
            {busy ? "담는 중…" : "담기"}
          </button>
        </div>
      </div>
    </div>
  );
}
