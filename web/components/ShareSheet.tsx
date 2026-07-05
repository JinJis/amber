"use client";

// SH-2 — the share sheet: snapshot the artifact (audit-gated server-side), get the public link,
// and offer ONE-TAP handoff to every SNS (X · Threads · Telegram · KakaoTalk=copy) — the
// intent URLs come ready-made from the server. 링크에는 출처·기준일이 함께 갑니다.

import { useEffect, useRef, useState } from "react";
import type { Artifact } from "@/lib/types";
import { PRESETS, type PresetKey, renderShareCard } from "@/lib/shareCard";

type Urls = { page: string; x: string; threads: string; telegram: string; kakao: string };

function shortLink(page: string): string {
  try { const u = new URL(page); return `${u.host}${u.pathname}`.replace(/^www\./, ""); }
  catch { return page.replace(/^https?:\/\//, ""); }
}

export function ShareSheet({ a, audit, onClose }: {
  a: Artifact; audit?: Record<string, unknown> | null; onClose: () => void;
}) {
  const [state, setState] = useState<"working" | "ready" | "blocked" | "error">("working");
  const [urls, setUrls] = useState<Urls | null>(null);
  const [detail, setDetail] = useState("");
  const [copied, setCopied] = useState(false);
  const [preset, setPreset] = useState<PresetKey>("1:1");
  const [imgUrl, setImgUrl] = useState<string | null>(null);   // object URL for the preview
  const [imgBusy, setImgBusy] = useState(false);
  const [imgCopied, setImgCopied] = useState(false);
  const token = urls ? decodeURIComponent(urls.page.split("/s/")[1] || "") : "";

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

  // SH-2b: render the card image whenever the share is ready or the preset changes.
  useEffect(() => {
    let revoked: string | null = null;
    (async () => {
      if (state !== "ready" || !urls) return;
      setImgBusy(true);
      try {
        const blob = await renderShareCard(a, preset, shortLink(urls.page));
        const url = URL.createObjectURL(blob);
        revoked = url;
        setImgUrl(url);
        // upload the 1:1 as the OG preview image (best-effort; the link works regardless)
        if (preset === "1:1" && token) {
          const dataUrl: string = await new Promise((res) => {
            const fr = new FileReader(); fr.onload = () => res(String(fr.result)); fr.readAsDataURL(blob);
          });
          fetch(`/api/shares/${encodeURIComponent(token)}/image`, {
            method: "PUT", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data_url: dataUrl }),
          }).catch(() => {});
        }
      } catch { setImgUrl(null); } finally { setImgBusy(false); }
    })();
    return () => { if (revoked) URL.revokeObjectURL(revoked); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, preset, urls]);

  async function saveImage() {
    if (!imgUrl) return;
    const link = document.createElement("a");
    link.href = imgUrl;
    link.download = `${(a.title || "valuegraph").replace(/\s+/g, "_").slice(0, 40)}_${preset.replace(":", "x")}.png`;
    link.click();
  }

  async function copyImage() {
    if (!imgUrl) return;
    try {
      const blob = await (await fetch(imgUrl)).blob();
      // ClipboardItem may be unavailable — fall back to download
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const CI = (window as any).ClipboardItem;
      if (CI && navigator.clipboard && "write" in navigator.clipboard) {
        await navigator.clipboard.write([new CI({ "image/png": blob })]);
        setImgCopied(true); setTimeout(() => setImgCopied(false), 1500);
      } else { saveImage(); }
    } catch { saveImage(); }
  }

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
            <div className="share-card-block">
              <div className="share-presets">
                {(Object.keys(PRESETS) as PresetKey[]).map((k) => (
                  <button key={k} type="button" className={`chip ${preset === k ? "on" : ""}`}
                    onClick={() => setPreset(k)} title={PRESETS[k].label}>{k}</button>
                ))}
              </div>
              <div className={`share-card-preview p-${preset.replace(":", "-")}`}>
                {imgUrl ? <img src={imgUrl} alt="공유 카드 미리보기" />
                  : <div className="share-card-skel mono">{imgBusy ? "카드 만드는 중…" : "미리보기"}</div>}
              </div>
              <div className="share-card-actions">
                <button className="chip" onClick={saveImage} disabled={!imgUrl}>이미지 저장</button>
                <button className="chip" onClick={copyImage} disabled={!imgUrl}>{imgCopied ? "복사됨 ✓" : "이미지 복사"}</button>
              </div>
              <p className="share-note mono">출처·기준일이 이미지에 새겨집니다 — 캡션 없이도 근거가 함께 갑니다</p>
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
