"use client";

// 답변 본문 렌더링 클러스터 — Chat.tsx에서 분리(FE-2). [n] 링크화, {{figure:N}} 인라인 그림,
// LG-4 수치 하이라이트 + hover 팝업, 마크다운 컴포넌트 매핑. Chat.tsx가 이 심볼들을 재익스포트해
// 테스트(InlineFigures/EvidencePanel)의 `../components/Chat` 임포트가 그대로 유지된다.

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { remarkCjkEmphasis, remarkKeyMark } from "../../lib/markdown";
import { annotateNumerals, type LedgerRow } from "../../lib/evidence";
import { ArtifactCard } from "../ArtifactCard";
import type { Artifact, Citation } from "../../lib/types";

// LG-3: bare [n] markers become links (#cite-n) — but never a [n] that is already a
// markdown link. The renderer turns them into live refs: hover ↔ panel-card highlight,
// click → scroll+flash that card in the 근거 패널 (the footnote becomes a remote control).
export function linkifyCitations(md: string): string {
  // ANCHOR-NORM 벨트: 서버 정규화 이전에 저장된 묶음 마커([1,2]·[3-5])도 개별 [n]으로 펼친다.
  const expanded = md.replace(/\[(\d{1,3}(?:\s*[,·\-–]\s*\d{1,3})+)\]/g, (_m, g: string) =>
    g.split(/[,·]/).flatMap((part) => {
      const r = part.trim().match(/^(\d{1,3})\s*[-–]\s*(\d{1,3})$/);
      if (r) {
        const a = Number(r[1]), b = Number(r[2]);
        if (b - a > 0 && b - a <= 10) return Array.from({ length: b - a + 1 }, (_x, i) => a + i);
        return [a, b];
      }
      return /^\d{1,3}$/.test(part.trim()) ? [Number(part.trim())] : [];
    }).map((n) => `[${n}]`).join(""));
  return expanded.replace(/\[(\d{1,3})\](?!\()/g, "[[$1]](#cite-$1)");
}

// ARTICLE: the answer body is a research note with figures INLINE — the synthesis model
// places {{figure:N}} markers (1-based into the turn's artifacts) where each chart/table
// belongs in the prose. Split the markdown into text/figure segments. While streaming, a
// half-arrived marker at the tail ("{{figu…") is hidden so it never flashes as raw text.
export function splitFigures(md: string, streaming?: boolean): { text?: string; fig?: number }[] {
  let src = md;
  if (streaming) {
    src = src.replace(/\{\{[^}]*$/, "");
    // hide an in-progress ==핵심== run (odd number of `==` → the last one is still open) so the
    // raw marker never flashes before its closing `==` arrives — mirrors the {{figure hide above.
    if (((src.match(/==/g) || []).length) % 2 === 1) src = src.slice(0, src.lastIndexOf("=="));
  }
  const parts = src.split(/\{\{figure:(\d{1,2})\}\}/g);
  const out: { text?: string; fig?: number }[] = [];
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 1) out.push({ fig: Number(parts[i]) });
    else if (parts[i]) out.push({ text: parts[i] });
  }
  return out;
}

// The article body: markdown segments interleaved with the REAL artifact cards (blog
// figures, center-aligned). Audited numerals get wrapped first (annotateNumerals → the
// yellow LG-4 highlights). Unknown/duplicate figure numbers are dropped silently — old
// conversations without persisted artifacts degrade to plain prose, never raw markers.
export function AnswerArticle({ content, artifacts, ledger, streaming, mdComponents, onEvidence, onPin, onShare }: {
  content: string; artifacts?: Artifact[]; ledger?: LedgerRow[]; streaming?: boolean;
  mdComponents: ReturnType<typeof makeMdComponents>;
  onEvidence?: (c: Citation) => void;
  onPin?: (a: Artifact) => void;
  onShare?: (a: Artifact) => void;
}) {
  const segs = splitFigures(annotateNumerals(content, streaming ? undefined : ledger), streaming);
  const seen = new Set<number>();
  return (
    <div className="md article">
      {segs.map((s, k) => {
        if (s.text != null) {
          return (
            <ReactMarkdown key={k} remarkPlugins={[remarkGfm, remarkCjkEmphasis, remarkKeyMark]} components={mdComponents}>
              {linkifyCitations(s.text)}
            </ReactMarkdown>
          );
        }
        const a = s.fig != null && !seen.has(s.fig) ? artifacts?.[s.fig - 1] : undefined;
        if (!a || s.fig == null) return null;
        seen.add(s.fig);
        return (
          <figure key={k} className="inline-figure" data-testid={`fig-${s.fig}`}
            onClick={(e) => e.stopPropagation()}>
            <ArtifactCard a={a} onPin={onPin} onShare={onShare} onEvidence={onEvidence} />
          </figure>
        );
      })}
    </div>
  );
}

// LG-4: 본문 속 수치 원장 — the audited numeral, highlighted in the prose. Hover → a small
// popup with the 원자료 대조 result (출처 [n]·as_of·파생 🧮·📌담기); click → the source/derivation.
function NumHighlight({ row, cit, children, setHoverCite, onEvidence }: {
  row: LedgerRow; cit: Citation | null; children: React.ReactNode;
  setHoverCite: (n: number | null) => void;
  onEvidence?: (c: Citation) => void;
}) {
  const [pinned, setPinned] = useState(false);
  const derived = !!cit?.computation;
  return (
    <span className={`num-hl ${row.supported ? "figure-num" : "warn"}`} data-testid="num-hl"
      role={cit ? "button" : undefined} tabIndex={cit ? 0 : undefined}
      onMouseEnter={() => cit?.index != null && setHoverCite(cit.index)}
      onMouseLeave={() => setHoverCite(null)}
      onClick={cit ? (e) => { e.stopPropagation(); onEvidence?.(cit); } : undefined}>
      {children}
      <span className="num-tip" role="tooltip" onClick={(e) => e.stopPropagation()}>
        {row.supported ? (
          <>
            <span className="nt-line nt-ok">✓ 원자료 대조 확인{derived ? " · 🧮 계산으로 도출" : ""}</span>
            <span className="nt-line">
              {cit ? <>[{cit.index}] {cit.source}{cit.as_of ? ` · ${cit.as_of}` : ""}</> : "차트·표 데이터와 일치"}
            </span>
            {cit && <span className="nt-hint">{derived ? "누르면 계산 과정을 볼 수 있어요" : "누르면 원문을 볼 수 있어요"}</span>}
          </>
        ) : (
          <span className="nt-line nt-warn">⚠ 이번 답변의 자료에서는 확인하지 못한 숫자예요</span>
        )}
      </span>
    </span>
  );
}

export type NumCtx = {
  rows: LedgerRow[]; citations: Citation[];
  onEvidence?: (c: Citation) => void;
};

export function makeMdComponents(
  hoverCite: number | null,
  setHoverCite: (n: number | null) => void,
  onCiteClick: (n: number) => void,
  num?: NumCtx,
) {
  return {
    a: (props: any) => {
      // 핵심(하이라이트 3종) — remarkKeyMark가 만든 #key 링크를 연한 앰버 블록으로. 비상호작용
      // (읽을 순서만 표시), 수치(오션 칩)·근거(앰버 각주)와 색·모양이 모두 구분된다.
      if (String(props.href || "") === "#key") return <mark className="hl-block">{props.children}</mark>;
      const m = String(props.href || "").match(/^#cite-(\d+)$/);
      if (m) {
        const n = Number(m[1]);
        return (
          <button type="button" className={`cite-ref mono ${hoverCite === n ? "hot" : ""}`}
            onMouseEnter={() => setHoverCite(n)} onMouseLeave={() => setHoverCite(null)}
            onClick={(e) => { e.stopPropagation(); onCiteClick(n); }}
            title="근거 패널에서 이 출처를 볼 수 있어요">[{n}]</button>
        );
      }
      const nm = String(props.href || "").match(/^#num-(\d+)$/);
      if (nm && num) {
        const row = num.rows[Number(nm[1])];
        if (!row) return <span>{props.children}</span>;
        const cit = row.citation_idx != null
          ? num.citations.find((c) => c.index === row.citation_idx) ?? null : null;
        return (
          <NumHighlight row={row} cit={cit} setHoverCite={setHoverCite}
            onEvidence={num.onEvidence}>{props.children}</NumHighlight>
        );
      }
      return <a {...props} target="_blank" rel="noreferrer" />;
    },
  };
}
