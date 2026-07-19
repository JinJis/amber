"use client";

// Live Context source previews — wireframe "화면 상세" Live panel. Instead of a title
// list, each cited source renders in its NATIVE form with the cited passage highlighted:
//   filing → a mini document page (page badge + 하이라이터로 칠한 인용 줄)
//   web/news → browser chrome (URL bar) + headline + highlighted phrase
//   data/metric → an extracted-data card with the computation
// Clicking a preview opens the full SourceViewer. "정말 거기 그렇게 써 있다"를 눈으로 확인 = 신뢰.
// We only ever show the extracted snippet + a link to the real document (no full-text
// redistribution), and surrounding text is drawn as skeleton lines.

import { useState } from "react";
import { CadenceTag, DocBadge, FreshnessDot, FRESH_LABEL, TrustLegend } from "./ui";
import { TickerLogo } from "./TickerLogo";
// Citation lives in lib/types.ts (FE-01); imported for local use + re-exported for back-compat
// (importers use `import { Citation } from "./SourceCard"`).
import type { Citation } from "../lib/types";

export { FreshnessDot, TrustLegend };
export type { Citation } from "../lib/types";

// PH-THINK: a small confidence chip (how well this source supports the question).
const CONF: Record<string, { label: string; cls: string }> = {
  high: { label: "신뢰 높음", cls: "high" },
  medium: { label: "신뢰 보통", cls: "med" },
  low: { label: "신뢰 낮음", cls: "low" },
};
export function ConfBadge({ c }: { c: Citation }) {
  const k = (c.confidence || "").toLowerCase();
  const m = CONF[k];
  if (!m) return null;
  return <span className={`sp-conf ${m.cls}`} title={c.confidence_why || "이 근거가 질문에 얼마나 잘 맞는지예요"}>{m.label}</span>;
}


// A compact extracted-data table for the preview — header row + data rows, the
// cited (latest) row highlighted. Shows the *real* figures the answer used.
export function SrcTable({ table }: { table: string[][] }) {
  if (!table?.length) return null;
  const [head, ...rows] = table;
  return (
    <table className="sp-table mono">
      <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
      <tbody>
        {rows.map((r, ri) => (
          <tr key={ri} className={ri === 0 ? "cited" : ""}>{r.map((cell, ci) => <td key={ci}>{cell}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

// filing · web · data — the three native preview shapes.
export function sourceShape(c: Citation): "filing" | "web" | "data" {
  if (c.kind === "filing") return "filing";
  if (c.kind === "news") return "web";
  if (c.kind === "metric" || c.kind === "data") return "data";
  if (c.url && /^https?:\/\//.test(c.url)) return "web";
  return "data";
}

export function hostOf(url?: string): string {
  if (!url) return "";
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return url.replace(/^https?:\/\//, "").split("/")[0]; }
}

const OPEN_LABEL: Record<string, string> = { filing: "원문 ↗", web: "기사 ↗", data: "표 ↗" };

export function SourceCard({ c, onExpand, onPin, hideTitle }: { c: Citation; onExpand?: (c: Citation) => void; onPin?: (c: Citation) => void; hideTitle?: boolean }) {
  const [pinned, setPinned] = useState(false);
  const shape = sourceShape(c);
  const fresh = c.freshness ? FRESH_LABEL[c.freshness] || c.freshness : null;
  // A filing-backed citation (공시 본문 or 재무제표 수치) opens the REAL document in-app on click;
  // any citation carrying an external source page (macro series page, news article) opens that page
  // in-app too (fetched + sanitized + the cited value highlighted). Either way → an "원문" badge.
  const hasFiling = !!c.evidence_image_url && shape !== "web";
  const hasSourcePage = !hasFiling && !!c.url && /^https?:\/\//i.test(c.url);
  const evBadge = hasFiling
    ? <span className="sp-ev-badge mono" title="누르면 원문 전체를 여기서 바로 볼 수 있어요">📄 원문</span>
    : hasSourcePage
    ? <span className="sp-ev-badge mono" title="누르면 원문 사이트를 여기서 바로 볼 수 있어요">🌐 원문</span>
    : null;
  const open = c.url ? (
    <a className="sp-open" href={c.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
      {OPEN_LABEL[shape]}
    </a>
  ) : null;
  const foot = (
    <div className="sp-foot mono">
      <FreshnessDot f={c.freshness} />
      <span>{shape === "web" ? "맥락 정보" : c.as_of ? `as_of ${c.as_of}` : (fresh ?? "출처")}</span>
      <CadenceTag c={c.cadence} />
      <ConfBadge c={c} />
      {open}
      {onPin && (
        <button type="button" className="sp-add" disabled={pinned} title="담기"
          onClick={(e) => { e.stopPropagation(); onPin(c); setPinned(true); }}>{pinned ? "✓ 담김" : "＋ 담기"}</button>
      )}
    </div>
  );

  return (
    <div className={`srcprev ${shape}`} role={onExpand ? "button" : undefined}
      onClick={onExpand ? () => onExpand(c) : undefined} title={onExpand ? "누르면 원문 전체를 볼 수 있어요" : undefined}>
      {shape === "filing" && (
        <>
          <div className="sp-head">
            {c.index ? <span className="sp-n mono">[{c.index}]</span> : null}
            {c.ticker ? <TickerLogo ticker={c.ticker} size={18} /> : <span className="sp-ic" aria-hidden>📄</span>}
            {!hideTitle && <span className="sp-title">{c.source || "공시 문서"}</span>}
            <DocBadge c={c} />{/* 문서유형: 국내 공시=오션·미국 공시=자주·실적콜=초록·모델=회색 */}
            {c.page ? <span className="sp-page mono">{c.page}</span> : null}
            {evBadge}
          </div>
          <div className="sp-doc">
            <span className="sp-skel" style={{ width: "82%" }} />
            {c.snippet ? <div className="sp-quote">{c.snippet}</div> : <span className="sp-skel" style={{ width: "95%" }} />}
            <span className="sp-skel" style={{ width: "94%" }} />
            <span className="sp-skel" style={{ width: "60%" }} />
          </div>
          {foot}
        </>
      )}

      {shape === "web" && (
        <>
          <div className="sp-chrome">
            {c.index ? <span className="sp-n mono">[{c.index}]</span> : null}
            <span className="sp-dots" aria-hidden><i /><i /><i /></span>
            <span className="sp-url mono">🔒 {hostOf(c.url) || c.source || "web"}…</span>
          </div>
          <div className="sp-web">
            <div className="sp-headline">{c.source || hostOf(c.url) || "기사"}</div>
            {c.as_of ? <div className="sp-wmeta mono">{c.as_of}</div> : null}
            {c.snippet ? <div className="sp-wtext">“<mark>{c.snippet}</mark>”</div> : null}
          </div>
          {foot}
        </>
      )}

      {shape === "data" && (
        <>
          <div className="sp-head">
            {c.index ? <span className="sp-n mono">[{c.index}]</span> : null}
            {c.ticker ? <TickerLogo ticker={c.ticker} size={18} /> : <span className="sp-ic" aria-hidden>▤</span>}
            {!hideTitle && <span className="sp-title">{c.source || "추출 데이터"}</span>}
            <DocBadge c={c} />{/* 파생 값이면 '모델 계산'(회색) 뱃지 */}
            {c.ticker ? <span className="sp-page mono">{c.ticker}</span> : null}
            {evBadge}
          </div>
          {c.table ? <SrcTable table={c.table} /> : null}
          {c.snippet ? <div className="sp-data mono">{c.snippet}</div>
            : (!c.table ? <div className="sp-data mono">계산에 사용된 값</div> : null)}
          {foot}
        </>
      )}
    </div>
  );
}
