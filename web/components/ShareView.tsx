"use client";

// SH-3 — public, read-only render of a shared snapshot. No auth, no pin/alert affordances;
// the provenance footer + (history) label travel with the artifact, and the CTA closes the
// growth loop (share → page → sign-up).

import { useEffect, useState } from "react";
import { ArtifactCard } from "./ArtifactCard";
import type { Artifact, Citation, Msg } from "@/lib/types";
import { Logo } from "./Logo";
import { AnswerArticle, makeMdComponents } from "./chat/answer";
import { TrustStrip } from "./EvidencePanel";
import { trustSummary, type LedgerRow } from "../lib/evidence";
import { SourceCard } from "./SourceCard";

type Share = {
  token: string; kind: string; title: string; payload: Record<string, unknown>;
  created_at?: string | null; views?: number;
  referral_code?: string | null;  // GUEST-4: 공유자 추천 코드 — CTA·칩 ?ref= 귀속
};

// GUEST-4: 공유 페이지에서 앱으로 가는 모든 링크에 ?ref=공유자코드를 붙인다 — 이 링크로
// 유입된 가입이 공유자에게 귀속된다(REF-1). 코드가 없으면 링크는 그대로.
function withRef(href: string, ref?: string | null): string {
  if (!ref) return href;
  return href + (href.includes("?") ? "&" : "?") + "ref=" + encodeURIComponent(ref);
}

export function ShareView({ status, share }: { status: number; share: Share | null }) {
  // V-4: 실측 뷰 비콘 — 렌더가 캐시돼도(페이지 revalidate) 뷰는 클라이언트에서 센다.
  useEffect(() => {
    if (!share?.token) return;
    try { navigator.sendBeacon?.(`/api/shares/${encodeURIComponent(share.token)}/view`); } catch {}
  }, [share?.token]);
  return (
    <div className="share-page">
      <header className="share-head">
        <span className="share-brand"><Logo size={20} /></span>
        <a className="share-cta" href={withRef("/", share?.referral_code)}>직접 확인해보기 →</a>
      </header>
      <main className="share-main">
        {share ? (
          <>
            <h1 className="share-title">{share.title}</h1>
            {share.kind === "answer" ? (
              <AnswerShareView payload={share.payload} title={share.title} views={share.views ?? 0} refCode={share.referral_code} />
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
            <p>finnote에서 출처가 달린 최신 자료를 직접 확인해보세요.</p>
            <a className="share-cta big" href="/">finnote 열기 →</a>
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
  content?: string; artifacts?: Artifact[]; citations?: Citation[]; suggestions?: string[];
  audit?: { checked?: number; supported?: number; unsupported?: string[]; ledger?: LedgerRow[] } | null;
};

function AnswerShareView({ payload, title, views, refCode }: { payload: Record<string, unknown>; title?: string; views?: number; refCode?: string | null }) {
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

  const newestAsOf = citations.map((c) => c.as_of).filter(Boolean).sort().slice(-1)[0];
  const stale = !!newestAsOf && (Date.now() - new Date(String(newestAsOf)).getTime()) > 7 * 86400e3;

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
      {/* V-5 전환 루프: 이어 묻기 칩(실제 팔로업) + 오래된 스냅샷 갱신 유도 + 실측 소셜 프루프 */}
      <div className="share-loop">
        {(views ?? 0) >= 50 && <span className="share-views mono">👀 {views!.toLocaleString()}명이 봤어요</span>}
        {stale && (
          <a className="share-cta" href={withRef(`/?q=${encodeURIComponent(title ?? "")}`, refCode)}>
            지금 데이터로 다시 보기 →
          </a>
        )}
        {(p.suggestions ?? []).length > 0 && (
          <div className="share-followups">
            <div className="sf-label mono">이 질문에서 이어가기</div>
            {(p.suggestions ?? []).slice(0, 3).map((q, i) => (
              <a key={i} className="fu-chip" href={withRef(`/?q=${encodeURIComponent(q)}`, refCode)}>{q} <span className="fu-arrow">→</span></a>
            ))}
          </div>
        )}
      </div>
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
