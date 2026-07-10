"use client";

// SH-3 — public, read-only render of a shared snapshot. No auth, no pin/alert affordances;
// the provenance footer + (history) label travel with the artifact, and the CTA closes the
// growth loop (share → page → sign-up).

import { useState } from "react";
import { ArtifactCard } from "./ArtifactCard";
import type { Artifact, Citation, Msg } from "@/lib/types";
import { Mascot } from "./ui";
import { AnswerArticle, makeMdComponents } from "./chat/answer";
import { TrustStrip } from "./EvidencePanel";
import { trustSummary, type LedgerRow } from "../lib/evidence";
import { SourceCard } from "./SourceCard";

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
            {share.kind === "answer" ? (
              <AnswerShareView payload={share.payload} />
            ) : share.kind === "note" ? (
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

// SH-ANSWER — the public render of a WHOLE shared answer: the research note body (inline figures
// + [n] refs + LG-4 number highlights) + the 판정 strip + every cited source as a read-only card.
// Provenance and evidence travel WITH the answer; the reader verifies. No user identity is ever
// present in the payload (it's a pure content snapshot), so nothing about the author can leak.
type AnswerPayload = {
  content?: string; artifacts?: Artifact[]; citations?: Citation[];
  audit?: { checked?: number; supported?: number; unsupported?: string[]; ledger?: LedgerRow[] } | null;
};

function AnswerShareView({ payload }: { payload: Record<string, unknown> }) {
  const p = payload as AnswerPayload;
  const content = p.content ?? "";
  const artifacts = p.artifacts ?? [];
  const citations = p.citations ?? [];
  const ledger = (p.audit?.ledger ?? []) as LedgerRow[];
  const [hoverCite, setHoverCite] = useState<number | null>(null);

  // read-only: a [n]/number click opens the (default-folded) source panel, scrolls to and
  // flashes that card — no in-app viewer (needs a login); the card links out to the original.
  const goCite = (n: number) => {
    const panel = document.getElementById("share-sources") as HTMLDetailsElement | null;
    if (panel && !panel.open) panel.open = true;
    const el = document.getElementById(`cite-${n}`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); }
  };
  const openSource = (c: Citation) => { if (c.url) window.open(c.url, "_blank", "noreferrer"); };

  const trust = trustSummary({ role: "assistant", content, artifacts, citations,
    audit: p.audit ?? null } as Msg);

  // the cited sources, in [n] order (the ones the answer actually leaned on)
  const cited = citations
    .filter((c) => c.used || c.index != null)
    .sort((a, b) => (a.index ?? 999) - (b.index ?? 999));

  return (
    <div className={`share-answer ${cited.length > 0 ? "with-src" : ""}`}>
      <div className="share-answer-main">
        {!trust.conceptual && <TrustStrip s={trust} />}
        {/* reuse the exact chat answer wrappers so article typography + inline figures + [n]/number
            highlights render identically to the app (read-only — evidence opens the source page). */}
        <div className="msg assistant"><div className="bubble">
          <AnswerArticle content={content} artifacts={artifacts} ledger={ledger}
            mdComponents={makeMdComponents(hoverCite, setHoverCite, goCite,
              { rows: ledger, citations, onEvidence: openSource })}
            onEvidence={openSource} />
        </div></div>
      </div>
      {cited.length > 0 && (
        // 근거 패널: 데스크톱 = 우측 사이드 패널, 모바일 = 본문 아래 — 어느 쪽이든 기본 접힘.
        // [n] 클릭이 펼치고 해당 카드로 스크롤한다 (goCite가 open을 강제).
        <details className="share-sources" id="share-sources">
          <summary className="share-sources-h">
            <span className="mono">🔗 근거 · 출처 {cited.length}곳</span>
            <span className="ss-hint mono">눌러서 펼치기</span>
          </summary>
          <div className="share-src-list">
            {cited.map((c, i) => (
              <div key={i} id={c.index != null ? `cite-${c.index}` : undefined} className="share-src-slot">
                <SourceCard c={c} />
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
