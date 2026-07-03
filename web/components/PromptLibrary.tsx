"use client";

import { useEffect, useState } from "react";
import { Button, Modal } from "./ui";
import { useAsyncOp } from "@/lib/hooks";

export type Prompt = {
  id: string;
  title: string;
  description?: string | null;
  body: string;
  category?: string | null;
  community: boolean;
  source_id?: string | null;
  editable: boolean;
};

// Prompt library modal: a personal collection + a seeded community catalog.
// "사용" drops the prompt body into the composer; "가져오기" clones a community
// prompt into the personal library.
export default function PromptLibrary({
  onClose,
  onUse,
}: {
  onClose: () => void;
  onUse: (body: string) => void;
}) {
  const [tab, setTab] = useState<"mine" | "community">("mine");
  const [mine, setMine] = useState<Prompt[]>([]);
  const [community, setCommunity] = useState<Prompt[]>([]);
  const [creating, setCreating] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const { busy, run } = useAsyncOp();  // FE-04: busy/finally lifecycle for the mutations

  async function loadMine() {
    const r = await fetch("/api/prompts");
    if (r.ok) setMine((await r.json()).prompts ?? []);
  }
  useEffect(() => {
    loadMine();
    (async () => {
      const r = await fetch("/api/prompts/community");
      if (r.ok) setCommunity((await r.json()).prompts ?? []);
    })();
  }, []);

  async function create() {
    if (!title.trim() || !body.trim()) return;
    await run(async () => {
      const r = await fetch("/api/prompts", { method: "POST", body: JSON.stringify({ title, body }) });
      if (r.ok) {
        setTitle("");
        setBody("");
        setCreating(false);
        await loadMine();
      }
    });
  }

  async function remove(id: string) {
    await run(async () => {
      await fetch(`/api/prompts/${id}`, { method: "DELETE" });
      await loadMine();
    });
  }

  async function importPrompt(id: string) {
    await run(async () => {
      await fetch(`/api/prompts/${id}/import`, { method: "POST" });
      await loadMine();
      setTab("mine");
    });
  }

  const list = tab === "mine" ? mine : community;

  return (
    <Modal title="프롬프트 라이브러리" onClose={onClose}>
        <div className="tabs">
          <button className={`tab ${tab === "mine" ? "on" : ""}`} onClick={() => setTab("mine")}>내 프롬프트</button>
          <button className={`tab ${tab === "community" ? "on" : ""}`} onClick={() => setTab("community")}>커뮤니티</button>
        </div>

        {tab === "mine" && (
          <div className="newprompt">
            {creating ? (
              <div className="fld">
                <input className="input" placeholder="제목" value={title} onChange={(e) => setTitle(e.target.value)} />
                <textarea className="input ta" rows={3} placeholder="프롬프트 내용 ( {TICKER} 같은 자리표시자 사용 가능 )"
                  value={body} onChange={(e) => setBody(e.target.value)} />
                <div className="modal-foot">
                  <div className="grow" />
                  <Button variant="ghost" size="sm" onClick={() => setCreating(false)} disabled={busy}>취소</Button>
                  <Button size="sm" onClick={create} disabled={busy || !title.trim() || !body.trim()}>저장</Button>
                </div>
              </div>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setCreating(true)}>＋ 새 프롬프트</Button>
            )}
          </div>
        )}

        <div className="prompt-list">
          {list.length === 0 && (
            <div className="muted-note">{tab === "mine" ? "아직 저장한 프롬프트가 없어요. 커뮤니티에서 가져오거나 새로 만들어 보세요." : "커뮤니티 프롬프트를 불러오지 못했습니다."}</div>
          )}
          {list.map((p) => (
            <div key={p.id} className="prompt-card">
              <div className="pc-head">
                <b>{p.title}</b>
                {p.category && <span className="cat">{p.category}</span>}
              </div>
              {p.description && <div className="pc-desc">{p.description}</div>}
              <div className="pc-body">{p.body}</div>
              <div className="pc-actions">
                <Button size="sm" onClick={() => onUse(p.body)}>사용</Button>
                {tab === "community" && <Button variant="ghost" size="sm" onClick={() => importPrompt(p.id)} disabled={busy}>가져오기</Button>}
                {tab === "mine" && <Button variant="ghost" size="sm" onClick={() => remove(p.id)} disabled={busy}>삭제</Button>}
              </div>
            </div>
          ))}
        </div>
    </Modal>
  );
}
