"use client";

// M-FACT (FC-2) — the fact-check verdict card: the receipt. Verdict chip (grayscale glyph,
// never color-coded advice), the claim quoted verbatim, cited findings for/against with
// their [n] anchors, the corrected record when the claim is off, and the method footer.
// TRUST GATE: a scored verdict with zero cited findings refuses to render (the prose answer
// stands; an uncited receipt is worse than none). A future claim ('미래 주장') renders
// without findings — there is nothing to cite about the future, and we never score it.

import { useState } from "react";
import type { Artifact, VerdictData } from "@/lib/types";
import { FreshnessDot } from "./ui";

const GLYPH: Record<string, string> = {
  "사실": "✓",
  "대체로 사실": "△",
  "사실과 다름": "✕",
  "확인 불가": "?",
  "미래 주장(검증 불가)": "⏳",
};

const CONF_LABEL: Record<string, string> = { high: "근거 강함", medium: "근거 보통", low: "근거 약함" };

export function VerdictArtifact({ a, onPin, onShare, onRemove, hideTitle, bare }: {
  a: Artifact; bare?: boolean; hideTitle?: boolean;
  onPin?: (spec: Artifact) => void; onShare?: (spec: Artifact) => void; onRemove?: () => void;
}) {
  const [pinned, setPinned] = useState(false);
  const v = a.verdict as VerdictData | undefined;
  if (!v || !GLYPH[v.verdict]) return null;
  const isFuture = v.verdict === "미래 주장(검증 불가)";
  if (!isFuture && !(v.findings && v.findings.length > 0)) return null; // uncited verdict never renders

  const body = (
    <div className="vd" data-testid="verdict-card">
      <div className="vd-chip mono" data-testid="verdict-chip">
        <span className="vd-glyph">{GLYPH[v.verdict]}</span> {v.verdict}
        {v.confidence ? <span className="vd-conf"> · {CONF_LABEL[v.confidence] ?? v.confidence}</span> : null}
      </div>
      <blockquote className="vd-claim">“{v.claim}”</blockquote>
      {isFuture ? (
        <p className="vd-future">
          미래에 대한 주장은 기록으로 검증할 수 없습니다. 이 서비스는 과거·현재의 기록만 확인하며,
          주장의 실현 가능성은 평가하지 않습니다.
        </p>
      ) : (
        <ul className="vd-findings">
          {v.findings.map((f, i) => (
            <li key={i} className={`vd-finding ${f.supports ? "for" : "against"}`}>
              <span className="vd-fmark mono">{f.supports ? "지지" : "반박"}</span>
              <span className="vd-fpoint">{f.point}</span>
              <span className="vd-fn mono">[{f.citation_idx}]</span>
            </li>
          ))}
        </ul>
      )}
      {v.corrected ? (
        <div className="vd-corrected">
          <span className="vd-corr-h mono">기록상 사실</span> {v.corrected}
        </div>
      ) : null}
      <div className="artifact-foot">
        <span className="vd-method mono">{v.method || a.source || "1차 기록 대조 검증"}</span>
        <span className="artifact-src">{a.as_of ? <span className="mono">as of {a.as_of}</span> : null}</span>
      </div>
    </div>
  );

  if (bare) return <div className="hist-bare">{body}</div>;
  return (
    <div className="artifact">
      <div className="artifact-head">
        {!hideTitle && <span className="artifact-title">{a.title}</span>}
        <FreshnessDot f={a.freshness ?? undefined} />
        <span style={{ flex: 1 }} />
        {onPin && (
          <button type="button" className="artifact-toggle" disabled={pinned}
            onClick={() => { onPin(a); setPinned(true); }}>{pinned ? "✓ 대시보드" : "＋ 대시보드"}</button>
        )}
        {onShare && <button type="button" className="artifact-toggle" onClick={() => onShare(a)}>↗ 공유</button>}
        {onRemove && <button type="button" className="artifact-toggle" onClick={onRemove}>제거</button>}
      </div>
      {body}
    </div>
  );
}
