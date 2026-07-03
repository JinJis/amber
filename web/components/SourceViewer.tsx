"use client";

// Click-to-expand full source viewer. A preview in the Live Context panel expands into this
// full-screen modal. For a filing-backed citation (공시 본문 or 재무제표 figure) it renders the
// ORIGINAL disclosure in-app (FilingViewer: real iXBRL/DART HTML, the cited element highlighted,
// free scroll/zoom). News/data without a filing keep the native-form preview. Honest by
// construction: we render the real document or the extracted passage we hold, never a fabrication.

import { useState } from "react";
import { Citation, sourceShape, hostOf, SrcTable } from "./SourceCard";
import { FilingViewer, viewerSrc } from "./FilingViewer";
import { DeckViewer, deckSrc } from "./DeckViewer";
import { FreshnessDot, FRESH_LABEL } from "./ui";

const TABS: { key: "filing" | "web" | "data"; label: string }[] = [
  { key: "filing", label: "📄 공시" },
  { key: "web", label: "🌐 뉴스" },
  { key: "data", label: "▤ 데이터" },
];

export function SourceViewer({ c, onClose }: { c: Citation; onClose: () => void }) {
  const shape = sourceShape(c);
  const [copied, setCopied] = useState(false);
  const fresh = c.freshness ? FRESH_LABEL[c.freshness] || c.freshness : null;
  // Render the REAL source in-app whenever we can: a filing-backed citation (공시 본문 or 재무제표
  // 수치) OR any citation carrying an external source page (macro series page, news article). The
  // viewer fetches + sanitizes it and highlights the cited value; on 204 it degrades to the link.
  // an 8-K presentation deck (PDF) opens in the pdf.js DeckViewer; everything else (filings,
  // transcripts, macro pages, news) uses the HTML FilingViewer.
  const isDeck = !!deckSrc(c);
  const frameSrc = isDeck ? null : viewerSrc(c);

  async function copyCite() {
    const text = `“${c.snippet ?? ""}” — ${c.source ?? ""}${c.as_of ? ` (${c.as_of})` : ""}${c.url ? ` ${c.url}` : ""}`.trim();
    try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }

  return (
    <div className="sv-backdrop" onClick={onClose}>
      <div className="sv" onClick={(e) => e.stopPropagation()}>
        <div className="sv-head">
          <div className="sv-tabs">
            {TABS.map((t) => <span key={t.key} className={`sv-tab ${t.key === shape ? "on" : ""}`}>{t.label}</span>)}
          </div>
          <span className="sv-name">{c.source || "원문"}</span>
          {c.page ? <span className="sv-meta mono">{c.page}{c.doc_type ? ` · ${c.doc_type}` : ""}</span> : null}
          <button className="sv-x" onClick={onClose} aria-label="닫기">✕</button>
        </div>

        <div className="sv-body">
          <div className="sv-stage">
            {isDeck ? (
              // an 8-K presentation deck — render the real slides (pdf.js) + highlight the cited chunk
              <DeckViewer c={c} />
            ) : frameSrc ? (
              // The REAL source page in-app (filing OR external page), cited value highlighted. The
              // extracted figures we used stay visible in the right-side context aside.
              <FilingViewer c={c} />
            ) : shape === "web" ? (
              <article className="sv-web">
                <div className="sp-chrome">
                  <span className="sp-dots" aria-hidden><i /><i /><i /></span>
                  <span className="sp-url mono">🔒 {hostOf(c.url) || c.source || "web"}</span>
                </div>
                <div className="sv-web-body">
                  <h4 className="sv-headline">{c.source || hostOf(c.url) || "기사"}</h4>
                  {c.as_of ? <div className="sv-wmeta mono">{c.as_of}</div> : null}
                  <p className="sv-skel-l" />
                  <p className="sv-web-text">“<mark>{c.snippet || "인용 구절"}</mark>”</p>
                  <p className="sv-skel-l" style={{ width: "82%" }} />
                </div>
              </article>
            ) : shape === "data" ? (
              <article className="sv-page">
                <div className="sv-page-hd mono">{c.source || "추출 데이터"}{c.ticker ? ` · ${c.ticker}` : ""}</div>
                {c.table ? <SrcTable table={c.table} /> : null}
                {c.snippet ? (
                  <div className="sv-data mono"><span className="sv-pin mono">{c.index ?? "1"}</span>{c.snippet}</div>
                ) : null}
                <p className="sv-data-note mono">{c.as_of ? `as_of ${c.as_of} · ` : ""}출처에서 추출·계산된 값 (셀 = 인용 근거)</p>
              </article>
            ) : (
              <article className="sv-page">
                <div className="sv-page-hd mono">{c.source || "공시 문서"}{c.page ? ` · ${c.page}` : ""}</div>
                {c.doc_type && c.doc_type !== "news" ? <h4 className="sv-page-h">{c.doc_type}</h4> : null}
                <p className="sv-skel-l" /><p className="sv-skel-l" style={{ width: "88%" }} />
                <div className="sv-quote">
                  <span className="sv-pin mono">{c.index ?? "1"}</span>
                  {c.snippet || "인용된 원문 구절을 불러올 수 없습니다."}
                </div>
                <p className="sv-skel-l" style={{ width: "94%" }} /><p className="sv-skel-l" style={{ width: "70%" }} />
              </article>
            )}
          </div>

          <aside className="sv-ctx">
            <div className="sv-ctx-h mono">이 원문을 인용한 곳</div>
            <div className="sv-ctx-card">
              <div className="sv-ctx-snip">{c.snippet ? `“${c.snippet}”` : "이 답변이 인용한 출처입니다."}</div>
              {c.index ? <div className="sv-ctx-n mono">인용 [{c.index}]</div> : null}
            </div>
            <div className="sv-ctx-meta mono">
              <div><FreshnessDot f={c.freshness} /> 신선도 {fresh ?? "—"}</div>
              {c.as_of ? <div>as_of {c.as_of}</div> : null}
              {c.ticker ? <div>{c.ticker}</div> : null}
              {c.page ? <div>{c.page}</div> : null}
            </div>
            <div className="sv-ctx-actions">
              {c.url ? <a className="sv-act primary" href={c.url} target="_blank" rel="noreferrer">원문 보기 ↗</a> : null}
              <button className="sv-act" onClick={copyCite}>{copied ? "복사됨 ✓" : "인용 복사"}</button>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
