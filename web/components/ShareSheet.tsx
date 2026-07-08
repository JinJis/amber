"use client";

// SH-2 — the share sheet: snapshot the artifact/answer (audit-gated server-side), get the public
// link, and offer ONE-TAP handoff to every SNS (X · Threads · Telegram · KakaoTalk=copy). Sharing
// is LINK-ONLY — no aspect presets, no image save/copy. A preview image is still generated SILENTLY
// from the real content and uploaded as the OG card, so the link unfurls with the actual answer.

import { useEffect, useState } from "react";
import type { Artifact, Citation } from "@/lib/types";
import { renderOgCard, ogCardForAnswer, ogCardForArtifact } from "@/lib/shareCard";

type Urls = { page: string; x: string; threads: string; telegram: string; kakao: string };

// SH-ANSWER: a whole chat answer to share — pure content, no user identity travels.
export type AnswerShare = {
  title: string; content: string;
  artifacts?: Artifact[]; citations?: Citation[];
  audit?: Record<string, unknown> | null;
};

function shortLink(page: string): string {
  try { const u = new URL(page); return `${u.host}${u.pathname}`.replace(/^www\./, ""); }
  catch { return page.replace(/^https?:\/\//, ""); }
}

export function ShareSheet({ a, answer, audit, onClose }: {
  a?: Artifact; answer?: AnswerShare; audit?: Record<string, unknown> | null; onClose: () => void;
}) {
  const [state, setState] = useState<"working" | "ready" | "blocked" | "error">("working");
  const [urls, setUrls] = useState<Urls | null>(null);
  const [detail, setDetail] = useState("");
  const [copied, setCopied] = useState(false);
  const token = urls ? decodeURIComponent(urls.page.split("/s/")[1] || "") : "";

  useEffect(() => {
    (async () => {
      try {
        const body = answer
          // SH-ANSWER: the whole answer — content + inline figures + citations + audit. No user
          // identity is ever included (no email / conversation id) — the payload is pure research.
          ? { kind: "answer", title: answer.title || "ValueGraph 리서치",
              payload: { content: answer.content, artifacts: answer.artifacts ?? [],
                         citations: answer.citations ?? [], audit: answer.audit ?? null },
              audit: answer.audit ?? null }
          : a!.kind === "note"
              ? { kind: "note", title: a!.title || "리서치 노트",
                  payload: { blocks: (a as unknown as { blocks: unknown[] }).blocks },
                  audit: null }  // pins carry provenance; user text is ATTRIBUTED, not audited
              : a!.kind === "quote"
              ? { kind: "quote", title: a!.title || "원문 인용",
                  payload: { passage: a!.passage, source: a!.source, doc_title: a!.doc_title, url: a!.url, as_of: a!.as_of },
                  audit: null }  // a verbatim quote has no computed numbers to audit
              : { kind: "artifact", title: a!.title || "ValueGraph 자료", payload: a, audit: audit ?? null };
        const r = await fetch("/api/shares", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (r.status === 422) { setDetail((await r.json()).error ?? ""); setState("blocked"); return; }
        if (!r.ok) { setState("error"); return; }
        setUrls((await r.json()).share_urls);
        setState("ready");
      } catch { setState("error"); }
    })();
  }, [a, answer, audit]);

  // Silent OG image: render the real content into a 16:9 card (best for link unfurls) and upload
  // it as the share's og:image. No UI — the user shares a link; the preview shows the answer.
  useEffect(() => {
    (async () => {
      if (state !== "ready" || !urls || !token) return;
      try {
        const card = answer ? ogCardForAnswer(answer) : ogCardForArtifact(a!);
        const blob = await renderOgCard(card, shortLink(urls.page));
        const dataUrl: string = await new Promise((res) => {
          const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsDataURL(blob);
        });
        await fetch(`/api/shares/${encodeURIComponent(token)}/image`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data_url: dataUrl }),
        });
      } catch { /* best-effort — the link works without the preview image */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, urls]);

  async function copy() {
    if (!urls) return;
    try { await navigator.clipboard.writeText(urls.page); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }

  return (
    <div className="sv-backdrop" onClick={onClose}>
      <div className="share-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="share-sheet-h">
          <b>공유</b>
          <button className="sv-x" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {state === "working" && <p className="muted">공유 링크 만드는 중…</p>}
        {state === "blocked" && (
          <p className="share-blocked">이 자료에는 원본 데이터와 대조되지 않은 수치가 있어 공유할 수 없어요.
            {detail ? <span className="mono"> {detail}</span> : null}</p>
        )}
        {state === "error" && <p className="share-blocked">공유 링크를 만들지 못했어요 — 잠시 후 다시 시도해주세요.</p>}
        {state === "ready" && urls && (
          <>
            <p className="share-note mono">이 링크를 열면 답변 전체를 출처·기준일과 함께 볼 수 있어요 · 90일 후 만료 · 언제든 해제 가능</p>
            <div className="share-linkrow">
              <input className="share-link mono" readOnly value={urls.page} onFocus={(e) => e.currentTarget.select()} />
              <button className="chip" onClick={copy}>{copied ? "복사됨 ✓" : "링크 복사"}</button>
            </div>
            <div className="share-sns">
              <a className="chip" href={urls.x} target="_blank" rel="noreferrer">X (트위터)</a>
              <a className="chip" href={urls.threads} target="_blank" rel="noreferrer">Threads</a>
              <a className="chip" href={urls.telegram} target="_blank" rel="noreferrer">텔레그램</a>
              <button className="chip" onClick={copy} title="카카오톡: 링크를 붙여넣어 공유">카카오톡 (링크 복사)</button>
            </div>
            <p className="share-note mono muted">링크를 붙여넣으면 미리보기에 답변 내용이 함께 표시돼요.</p>
          </>
        )}
      </div>
    </div>
  );
}
