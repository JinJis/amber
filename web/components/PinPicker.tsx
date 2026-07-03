"use client";

import { useEffect, useState } from "react";
import { Button, Modal } from "./ui";
import { widgetKind, widgetKindLabel } from "@/lib/widgets";

export type Board = { id: string; name: string };

// On pin (any asset — chart, source, text), choose which dashboard(s) to add it to (multi-select),
// or create a new dashboard inline. Adds to every selected dashboard at once. (탐색 → ＋대시보드)
export default function PinPicker({
  spec,
  onClose,
  onPinned,
}: {
  spec: any;
  onClose: () => void;
  onPinned: (boardIds: string[]) => void;
}) {
  const [boards, setBoards] = useState<Board[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      const r = await fetch("/api/boards");
      if (r.ok) {
        const bs = ((await r.json()).boards ?? []) as Board[];
        setBoards(bs);
        if (bs[0]) setSel(new Set([bs[0].id])); // preselect the first dashboard
      }
      setLoaded(true);
    })();
  }, []);

  function toggle(id: string) {
    setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }

  async function createBoard() {
    if (!newName.trim()) return;
    const r = await fetch("/api/boards", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: newName.trim() }) });
    if (r.ok) {
      const b = (await r.json()) as Board;
      setBoards((p) => [...p, b]);
      setSel((s) => new Set([...s, b.id]));
      setNewName("");
    }
  }

  async function pin() {
    if (!sel.size) return;
    setBusy(true);
    try {
      await fetch("/api/board", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec, board_ids: [...sel] }),
      });
      onPinned([...sel]);
      onClose();
    } catch { setBusy(false); }
  }

  const label = widgetKindLabel(widgetKind(spec));

  return (
    <Modal title={`📌 ${label} → ＋ 대시보드에 추가`} onClose={onClose}
      footer={<>
        <span className="grow" />
        <Button variant="ghost" onClick={onClose} disabled={busy}>취소</Button>
        <Button onClick={pin} disabled={busy || sel.size === 0}>{busy ? "추가 중…" : `추가 (${sel.size})`}</Button>
      </>}>
      <div className="fld">
        <span>어느 대시보드에 추가할까요? <span className="hint-inline">— 여러 개 선택 가능</span></span>
        <div className="pinpick-list">
          {boards.map((b) => (
            <label key={b.id} className={`pinpick-row ${sel.has(b.id) ? "on" : ""}`}>
              <input type="checkbox" checked={sel.has(b.id)} onChange={() => toggle(b.id)} />
              {b.name}
            </label>
          ))}
          {!loaded && <div className="muted-note">대시보드를 불러오는 중…</div>}
          {loaded && boards.length === 0 && (
            <div className="muted-note">대시보드가 아직 없어요. 아래에서 새로 만들어 시작하세요.</div>
          )}
        </div>
      </div>
      <div className="fld">
        <span>새 대시보드 만들기 <span className="hint-inline">— 아직 없으면 바로 생성</span></span>
        <div className="pinpick-new">
          <input className="input" value={newName} placeholder="대시보드 이름 (예: 반도체 리서치)"
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); createBoard(); } }} />
          <Button variant="ghost" onClick={createBoard} disabled={!newName.trim()}>＋ 추가</Button>
        </div>
      </div>
    </Modal>
  );
}
