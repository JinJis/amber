"use client";

// LG (근거 패널 v2) — the panel's unit flips from DOCUMENT to FIGURE. Three trust layers:
//   ① 판정 (TrustStrip)  — the QT-2 number audit as the headline, not fine print
//   ② 차트·표            — artifacts, unchanged
//   ③ 수치 원장 (Ledger)  — every claim numeral in the prose, one row each:
//        값 · 문맥 · 출처 [n] · 신선도 · 원문↗   (파생값 🧮 → Derivation Card body)
//   문서 서랍 / 과정 — the old SourceCards + tool log, demoted to folds (nothing disappears).
// [n] anchors in the prose are LIVE: hover ↔ card highlight, click → scroll+flash the card
// here (the panel is the [n]'s destination — the footnote becomes a remote control).

import { useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { ArtifactCard } from "./ArtifactCard";
import { SourceCard } from "./SourceCard";
import { FreshnessDot } from "./ui";
import {
  citationByIndex, clearNumeralHighlight, contextLabel, evidenceOf, highlightNumeral,
  occurrenceIndex, trustSummary, type LedgerRow, type TrustSummary,
} from "../lib/evidence";
import type { Artifact, Citation, Msg, ToolUse } from "../lib/types";

function uniqueTools(tools?: ToolUse[]): ToolUse[] {
  const seen = new Map<string, ToolUse>();
  for (const t of tools || []) seen.set(t.label || t.name, t);
  return [...seen.values()];
}
export { evidenceOf, uniqueTools };

// ── ① 판정 ──────────────────────────────────────────────────────────────────────
export function TrustStrip({ s }: { s: TrustSummary }) {
  if (s.conceptual) {
    return <div className="ctx-trust quiet" data-testid="trust-strip">개념 설명 — 검증할 수치 없음</div>;
  }
  return (
    <div className={`ctx-trust ${s.allClear ? "" : "warn"}`} data-testid="trust-strip">
      <span className="ct-verdict mono">
        {s.checked > 0
          ? (s.allClear
              ? <><b>✓ 검증</b> 수치 {s.checked}/{s.checked} 원자료 대조</>
              : <><b className="ct-warn">⚠ 확인 필요</b> 수치 {s.checked}개 중 미확인 {s.unsupported}건</>)
          : <><b>✓</b> 출처 기반</>}
      </span>
      <span className="ct-meta mono">
        출처 {s.sources}
        {s.freshness.fresh > 0 && <span className="ct-f"><i className="fdot fresh" />{s.freshness.fresh}</span>}
        {s.freshness.aging > 0 && <span className="ct-f"><i className="fdot aging" />{s.freshness.aging}</span>}
        {s.freshness.stale > 0 && <span className="ct-f"><i className="fdot stale" />{s.freshness.stale}</span>}
      </span>
    </div>
  );
}

// ── ③ 수치 원장 ──────────────────────────────────────────────────────────────────
function LedgerSection({ msg, rows, onEvidence, onPinLedger, hoverCite, setHoverCite, bubbleEl }: {
  msg: Msg; rows: LedgerRow[];
  onEvidence: (c: Citation) => void;
  onPinLedger?: (row: Record<string, unknown>, c: Citation | null) => void;   // NB-2: 📌 노트에 담기
  hoverCite: number | null; setHoverCite: (n: number | null) => void;
  bubbleEl?: () => HTMLElement | null;   // the focused answer's bubble, for prose highlight
}) {
  if (!rows.length) return null;
  return (
    <div className="ctx-section" data-testid="ledger">
      <div className="ctx-label">수치 원장 — 답변의 모든 숫자</div>
      <div className="ledger">
        {rows.map((r, i) => {
          const cit = citationByIndex(msg, r.citation_idx);
          const derived = !!cit?.computation;
          const label = contextLabel(msg.content, r);
          return (
            <div key={i}
              className={`lg-row ${r.supported ? "" : "unsupported"} ${cit && hoverCite === cit.index ? "hot" : ""}`}
              data-testid={`lg-row-${i}`}
              role={cit ? "button" : undefined}
              onMouseEnter={() => {
                if (cit?.index != null) setHoverCite(cit.index);
                highlightNumeral(bubbleEl?.() ?? null, r.raw, occurrenceIndex(rows, i));
              }}
              onMouseLeave={() => { setHoverCite(null); clearNumeralHighlight(); }}
              onClick={cit ? () => onEvidence(cit) : undefined}
              title={r.supported
                ? (derived ? "계산으로 도출된 값 — 클릭하면 도출 과정" : "클릭하면 원문")
                : "이 수치는 이번 턴 도구 반환값과 일치하지 않았습니다"}>
              <span className="lg-val mono">{r.raw}</span>
              <span className="lg-ctx">{label || (r.supported ? "" : "답변 표현")}</span>
              {r.supported ? (
                <span className="lg-src mono">
                  {derived ? "🧮 " : ""}{cit ? <>[{cit.index}] {(cit.source || "").slice(0, 18)}</> : "차트·표 데이터"}
                </span>
              ) : (
                <span className="lg-src lg-warn mono">⚠ 미확인</span>
              )}
              {cit?.freshness ? <FreshnessDot f={cit.freshness} /> : null}
              {r.supported && onPinLedger ? (
                <button type="button" className="lg-pin" data-testid={`lg-pin-${i}`} title="노트에 담기"
                  onClick={(e) => { e.stopPropagation(); onPinLedger(r as unknown as Record<string, unknown>, cit); }}>📌</button>
              ) : null}
              {cit ? <span className="lg-open mono">↗</span> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── the panel ────────────────────────────────────────────────────────────────────
export function ContextPanel(
  { msg, streaming, onEvidence, onPinArtifact, onPinCitation, onPinLedger, onShareArtifact, onResizeStart,
    hoverCite, setHoverCite, flashCite, bubbleEl }:
  {
    msg: Msg | null; streaming: boolean;
    onEvidence: (c: Citation) => void;
    onPinArtifact?: (a: Artifact) => void;
    onShareArtifact?: (a: Artifact) => void;
    onPinCitation?: (c: Citation) => void;
    onPinLedger?: (row: Record<string, unknown>, c: Citation | null) => void;
    onResizeStart: (e: ReactMouseEvent) => void;
    hoverCite: number | null;
    setHoverCite: (n: number | null) => void;
    flashCite: { n: number; ts: number } | null;   // [n] clicked in prose → scroll+flash here
    bubbleEl?: () => HTMLElement | null;
  },
) {
  const arts = msg?.artifacts ?? [];
  const cites = msg?.citations ?? [];
  const used = msg ? evidenceOf(msg) : [];
  const usedKeys = new Set(used.map((c) => `${c.source}|${c.url}`));
  const others = cites.filter((c) => !usedKeys.has(`${c.source}|${c.url}`));
  const tools = uniqueTools(msg?.tools);
  const hasAny = arts.length || cites.length || tools.length;
  const ledger = (msg?.audit?.ledger ?? []) as LedgerRow[];
  const summary = trustSummary(msg);

  // 문서 서랍: collapsed when the ledger carries the story; expanded otherwise (news/conceptual).
  const [docsOpen, setDocsOpen] = useState(false);
  useEffect(() => { setDocsOpen(ledger.length === 0); }, [msg, ledger.length]);

  // [n] click → make sure the drawer is open, then scroll+flash that card.
  const cardRefs = useRef(new Map<number, HTMLDivElement>());
  useEffect(() => {
    if (!flashCite) return;
    setDocsOpen(true);
    const el = cardRefs.current.get(flashCite.n);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      const t = setTimeout(() => el.classList.remove("flash"), 1600);
      return () => clearTimeout(t);
    }
  }, [flashCite]);

  const card = (c: Citation, key: string) => (
    <div key={key}
      ref={(el) => { if (el && c.index != null) cardRefs.current.set(c.index, el); }}
      className={`ctx-card-wrap ${hoverCite != null && c.index === hoverCite ? "hot" : ""}`}
      onMouseEnter={() => c.index != null && setHoverCite(c.index)}
      onMouseLeave={() => setHoverCite(null)}>
      <SourceCard c={c} onExpand={onEvidence} onPin={onPinCitation} />
    </div>
  );

  return (
    <aside className="ctxpane">
      <div className="ctx-resize" onMouseDown={onResizeStart} title="드래그해서 패널 너비 조절" aria-hidden />
      <div className="ctxpane-head">
        <span className="ctx-title">근거 패널</span>
        {streaming && <span className="ctx-live"><span className="tl-spin" />수집 중</span>}
      </div>
      <span className="live-label">원자료와 출처만 보여줘요 — 예측·매매 의견은 제공하지 않습니다.</span>
      {!hasAny ? (
        <div className="ctx-empty">
          {streaming
            ? "답변을 작성하며 차트·표·출처를 모으고 있어요…"
            : "답변을 누르면 그 답에 쓰인 차트·표·출처가 여기에 모여요."}
        </div>
      ) : (
        <>
          {msg && <TrustStrip s={summary} />}
          {arts.length > 0 && (
            <div className="ctx-section">
              <div className="ctx-label">차트·표 {arts.length}</div>
              <div className="artifacts">
                {arts.map((a, j) => <ArtifactCard key={`a${j}`} a={a} onPin={onPinArtifact} onShare={onShareArtifact} onEvidence={onEvidence} />)}
              </div>
            </div>
          )}
          {msg && ledger.length > 0 && (
            <LedgerSection msg={msg} rows={ledger} onEvidence={onEvidence} onPinLedger={onPinLedger}
              hoverCite={hoverCite} setHoverCite={setHoverCite} bubbleEl={bubbleEl} />
          )}
          {used.length > 0 && (
            <details className="ctx-section ctx-more" open={docsOpen}
              onToggle={(e) => setDocsOpen((e.target as HTMLDetailsElement).open)}>
              <summary className="ctx-label">출처 문서 {used.length}</summary>
              <div className="ctx-cards">{used.map((c, j) => card(c, `u${j}`))}</div>
            </details>
          )}
          {others.length > 0 && (
            <details className="ctx-section ctx-more">
              <summary className="ctx-label">참고한 모든 출처 {cites.length} · 답변 외 {others.length}</summary>
              <div className="ctx-cards">{others.map((c, j) => card(c, `o${j}`))}</div>
            </details>
          )}
          {tools.length > 0 && (
            <details className="ctx-section ctx-more">
              <summary className="ctx-label">과정 — 도구 {tools.length}개</summary>
              {tools.map((t, j) => <div key={`t${j}`} className="tool">🔧 {t.label || t.name}</div>)}
            </details>
          )}
        </>
      )}
    </aside>
  );
}
