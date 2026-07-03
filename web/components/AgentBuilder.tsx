"use client";

import { useMemo, useState } from "react";
import { Button, GuardrailLabel, Modal } from "./ui";

export type Agent = {
  id: string;
  name: string;
  description?: string | null;
  model: string;
  system_prompt?: string | null;
  data_sources: string[];
  is_template: boolean;
  editable: boolean;
};
// A tool is an individually selectable capability; `name` is the fully-qualified id
// (`sec_edgar__guru_trades`) stored in the agent's data_sources.
export type Tool = {
  name: string;
  label?: string;
  description?: string | null;
  source?: string | null;
  connector_name?: string;
};
// Categories are the intuitive, user-facing groups (금융시장 현황 / 거시경제 분석 …) —
// NOT the upstream API. Users pick individual tools within a category.
export type Category = { id: string; label: string; description?: string | null; tools: Tool[] };

// Builder modal: create a new agent, or edit/clone an existing one. Templates
// are read-only, so editing one starts a clone (new agent seeded from it).
export default function AgentBuilder({
  base,
  categories,
  onClose,
  onSaved,
}: {
  base: Agent | null; // agent to edit/clone, or null for a blank new agent
  categories: Category[];
  onClose: () => void;
  onSaved: (a: Agent, deletedId?: string) => void;
}) {
  const cloning = !!base && !base.editable; // a template -> save as a new agent
  const allToolNames = useMemo(
    () => categories.flatMap((c) => c.tools.map((t) => t.name)),
    [categories],
  );
  const [name, setName] = useState(base ? (cloning ? `${base.name} (복사본)` : base.name) : "");
  const [description, setDescription] = useState(base?.description ?? "");
  // gemini-only (invariant #7) — no picker; the agent always runs on Gemini.
  const [model] = useState(base?.model ?? "gemini");
  const [systemPrompt, setSystemPrompt] = useState(base?.system_prompt ?? "");
  // Empty stored data_sources means "every tool" — reflect that as all-selected so the user
  // can then narrow it. A non-empty list is shown exactly as saved.
  const [sources, setSources] = useState<string[]>(
    base?.data_sources?.length ? base.data_sources : allToolNames,
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const sel = useMemo(() => new Set(sources), [sources]);
  const editing = !!base && base.editable; // existing own agent -> PATCH

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleTool(toolName: string) {
    setSources((s) => (s.includes(toolName) ? s.filter((x) => x !== toolName) : [...s, toolName]));
  }

  function toggleCategory(cat: Category) {
    const names = cat.tools.map((t) => t.name);
    const allOn = names.every((n) => sel.has(n));
    setSources((s) =>
      allOn ? s.filter((x) => !names.includes(x)) : Array.from(new Set([...s, ...names])),
    );
  }

  const totalSelected = sources.filter((s) => allToolNames.includes(s)).length;

  async function save() {
    if (!name.trim()) {
      setErr("이름을 입력하세요.");
      return;
    }
    setBusy(true);
    setErr("");
    // Only persist known tool ids (drop any legacy connector/category ids the user didn't touch).
    const data_sources = sources.filter((s) => allToolNames.includes(s));
    const body = { name, description, model, system_prompt: systemPrompt, data_sources };
    try {
      const res = editing
        ? await fetch(`/api/agents/${base!.id}`, { method: "PATCH", body: JSON.stringify(body) })
        : await fetch("/api/agents", { method: "POST", body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await res.text());
      onSaved(await res.json());
    } catch (e: any) {
      setErr("저장에 실패했어요. 다시 시도해 주세요.");
      setBusy(false);
    }
  }

  async function remove() {
    if (!editing) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/agents/${base!.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error();
      onSaved(base!, base!.id);
    } catch {
      setErr("삭제에 실패했어요.");
      setBusy(false);
    }
  }

  return (
    <Modal
      title={editing ? "에이전트 편집" : cloning ? "에이전트 복제" : "새 에이전트"}
      onClose={onClose}
      footer={<>
        {editing && <Button variant="danger" onClick={remove} disabled={busy}>삭제</Button>}
        <span className="grow" />
        <Button variant="ghost" onClick={onClose} disabled={busy}>취소</Button>
        <Button onClick={save} disabled={busy}>{busy ? "저장 중…" : "저장"}</Button>
      </>}
    >
        <label className="fld">
          <span>이름</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 공시 분석가" />
        </label>

        <label className="fld">
          <span>설명 (선택)</span>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="이 에이전트가 무엇을 하나요?" />
        </label>

        <label className="fld">
          <span>시스템 프롬프트 (선택)</span>
          <textarea className="input ta" value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="에이전트의 역할·말투·규칙을 적어주세요." rows={3} />
        </label>

        <div className="fld">
          <span>
            툴 선택 <span className="hint-inline">— 카테고리 안에서 이 분석가가 쓸 툴을 골라요 ({totalSelected}/{allToolNames.length})</span>
          </span>
          <div className="sources-list">
            {categories.map((cat) => {
              const names = cat.tools.map((t) => t.name);
              const on = names.filter((n) => sel.has(n)).length;
              const open = expanded.has(cat.id);
              const state = on === 0 ? "off" : on === names.length ? "on" : "partial";
              return (
                <div key={cat.id} className={`src-row ${state}`}>
                  <div className="src-head">
                    <label className="src-pick" title={cat.description ?? ""}>
                      <input
                        type="checkbox"
                        checked={state === "on"}
                        ref={(el) => { if (el) el.indeterminate = state === "partial"; }}
                        onChange={() => toggleCategory(cat)}
                      />
                      {cat.label}
                    </label>
                    <button type="button" className="src-expand" aria-expanded={open}
                      onClick={() => toggleExpand(cat.id)}>
                      {open ? "▾" : "▸"} {on}/{names.length}
                    </button>
                  </div>
                  {open && (
                    <ul className="tool-list">
                      {cat.tools.map((t) => (
                        <li key={t.name}>
                          <label className="tool-pick" title={t.description ?? ""}>
                            <input type="checkbox" checked={sel.has(t.name)} onChange={() => toggleTool(t.name)} />
                            <span className="tool-label">{t.label || t.name}</span>
                            {t.source ? <span className="tool-src">{t.source}</span> : null}
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
            {categories.length === 0 && <div className="muted-note">툴 목록을 불러오지 못했습니다.</div>}
          </div>
        </div>

        <GuardrailLabel>매수/매도·목표가·전망은 자동 거절됩니다 (끌 수 없음)</GuardrailLabel>

        {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
    </Modal>
  );
}
