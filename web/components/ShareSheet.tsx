"use client";

// SH-2 — the share sheet: snapshot the artifact (audit-gated server-side), get the public link,
// and offer ONE-TAP handoff to every SNS (X · Threads · Telegram · KakaoTalk=copy) — the
// intent URLs come ready-made from the server. 링크에는 출처·기준일이 함께 갑니다.

import { useEffect, useState } from "react";
import type { Artifact } from "@/lib/types";

type Urls = { page: string; x: string; threads: string; telegram: string; kakao: string };

export function ShareSheet({ a, audit, onClose }: {
  a: Artifact; audit?: Record<string, unknown> | null; onClose: () => void;
}) {
  const [state, setState] = useState<"working" | "ready" | "blocked" | "error">("working");
  const [urls, setUrls] = useState<Urls | null>(null);
  const [detail, setDetail] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/shares", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: "artifact", title: a.title || "ValueGraph 자료",
                                 payload: a, audit: audit ?? null }),
        });
        if (r.status === 422) { setDetail((await r.json()).error ?? ""); setState("blocked"); return; }
        if (!r.ok) { setState("error"); return; }
        setUrls((await r.json()).share_urls);
        setState("ready");
      } catch { setState("error"); }
    })();
  }, [a, audit]);

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
          <p className="share-blocked">이 자료에는 원본 데이터와 대조되지 않은 수치가 있어 공유할 수 없습니다.
            {detail ? <span className="mono"> {detail}</span> : null}</p>
        )}
        {state === "error" && <p className="share-blocked">공유 링크를 만들지 못했습니다 — 잠시 후 다시 시도해주세요.</p>}
        {state === "ready" && urls && (
          <>
            <p className="share-note mono">스냅샷 링크 — 출처·기준일이 함께 갑니다 · 90일 후 만료 · 언제든 해제 가능</p>
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
          </>
        )}
      </div>
    </div>
  );
}
