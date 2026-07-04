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
            {share.kind === "quote" ? (
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
