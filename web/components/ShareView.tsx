"use client";

// SH-3 — public, read-only render of a shared snapshot. No auth, no pin/alert affordances;
// the provenance footer + (history) label travel with the artifact, and the CTA closes the
// growth loop (share → page → sign-up).

import { ArtifactCard } from "./ArtifactCard";
import type { Artifact } from "@/lib/types";
import { Mascot } from "./ui";

type Share = {
  token: string; kind: string; title: string; payload: Record<string, unknown>;
  created_at?: string | null;
};

export function ShareView({ status, share }: { status: number; share: Share | null }) {
  return (
    <div className="share-page">
      <header className="share-head">
        <span className="share-brand"><Mascot /> ValueGraph</span>
        <a className="share-cta" href="/">직접 확인해보기 →</a>
      </header>
      <main className="share-main">
        {share ? (
          <>
            <h1 className="share-title">{share.title}</h1>
            {share.kind === "note" ? (
              // NB-4: the public research note — sourced pins keep their provenance shape;
              // the USER'S OWN text is explicitly labeled (attribution, not audit).
              <div className="share-note-doc">
                {((share.payload as { blocks?: { kind?: string; note?: string | null;
                    payload?: Record<string, unknown> }[] }).blocks ?? []).map((b, i) => (
                  b.kind === "text" ? (
                    <div key={i} className="share-nb-text">
                      <span className="share-nb-lbl mono">✍ 작성자 메모</span>
                      <p>{String(b.payload?.md ?? "")}</p>
                    </div>
                  ) : b.kind === "pin_artifact" ? (
                    <div key={i} className="share-nb-pin">
                      <ArtifactCard a={b.payload as unknown as Artifact} bare />
                      {b.note ? <p className="share-nb-why mono">메모: {b.note}</p> : null}
                    </div>
                  ) : (
                    <div key={i} className="share-nb-pin">
                      <div className="share-nb-ev mono">
                        {String(b.payload?.raw ?? b.payload?.snippet ?? b.payload?.title ?? "근거")}
                        {b.payload?.source ? <span className="muted"> · {String(b.payload.source)}</span> : null}
                        {b.payload?.as_of ? <span className="muted"> · as of {String(b.payload.as_of)}</span> : null}
                      </div>
                      {b.note ? <p className="share-nb-why mono">메모: {b.note}</p> : null}
                    </div>
                  )
                ))}
              </div>
            ) : share.kind === "quote" ? (
              <blockquote className="share-quote">
                <p>“{String((share.payload as { passage?: string }).passage ?? "")}”</p>
                <footer className="mono">{String((share.payload as { source?: string }).source ?? "")}</footer>
              </blockquote>
            ) : (
              <div className="artifacts">
                <ArtifactCard a={share.payload as unknown as Artifact} hideTitle={false} />
              </div>
            )}
            <p className="share-note mono">
              이 자료는 공유 시점의 스냅샷입니다 · 게시 {share.created_at?.slice(0, 10) ?? ""} ·
              모든 수치는 출처·기준일과 함께 기록되었습니다
            </p>
          </>
        ) : (
          <div className="share-tomb">
            <h1>{status === 410 ? "게시자가 이 공유를 해제했거나 만료되었습니다" : "존재하지 않는 공유입니다"}</h1>
            <p>ValueGraph에서 출처가 달린 최신 자료를 직접 확인해보세요.</p>
            <a className="share-cta big" href="/">ValueGraph 열기 →</a>
          </div>
        )}
      </main>
    </div>
  );
}
