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
import { DerivationCard } from "./DerivationCard";
import { DocBadge, FreshnessDot, FRESH_LABEL } from "./ui";

const TABS: { key: "filing" | "web" | "data"; label: string }[] = [
  { key: "filing", label: "📄 공시" },
  { key: "web", label: "🌐 뉴스" },
  { key: "data", label: "▤ 데이터" },
];

export function SourceViewer({ c, onClose, onQuote }: {
  c: Citation; onClose: () => void; onQuote?: (a: import("@/lib/types").Artifact) => void;
}) {
  const shape = sourceShape(c);
  const [copied, setCopied] = useState(false);
  // DRV-3: an input row's [원문↗] swaps the stage to the /evidence viewer for THAT input —
  // the derived figure's source preview can open each ingredient's own filing cell.
  const [evInput, setEvInput] = useState<Citation | null>(null);
  const fresh = c.freshness ? FRESH_LABEL[c.freshness] || c.freshness : null;
  // Render the REAL source in-app whenever we can: a filing-backed citation (공시 본문 or 재무제표
  // 수치) OR any citation carrying an external source page (macro series page, news article). The
  // viewer fetches + sanitizes it and highlights the cited value; on 204 it degrades to the link.
  // an 8-K presentation deck (PDF) opens in the pdf.js DeckViewer; everything else (filings,
  // transcripts, macro pages, news) uses the HTML FilingViewer.
  const isDeck = !!deckSrc(c);
  const frameSrc = isDeck ? null : viewerSrc(c);
  // A DERIVED figure's trust envelope is its math (공식 + 출처 있는 입력 + 단계) — show the
  // DerivationCard as the stage even when a source page exists; each input row's [원문↗] still
  // opens its own filing cell (evInput), so the real document stays one click away.
  const showDerivation = shape === "data" && !!c.computation;

  // SH-4: capture the highlighted passage (or the cited snippet) into a quote card → share pipeline.
  function quoteThis() {
    const sel = typeof window !== "undefined" ? window.getSelection?.()?.toString().trim() : "";
    const passage = (sel && sel.length >= 4 ? sel : c.snippet || "").trim();
    if (!passage || !onQuote) return;
    onQuote({
      kind: "quote", title: `${c.source || "원문"} 인용`, series: [],
      passage, doc_title: [c.source, c.doc_type, c.page].filter(Boolean).join(" · ") || c.source || null,
      source: c.source || null, as_of: c.as_of || null, url: c.url || null,
    });
  }

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
          <DocBadge c={c} />{/* 문서유형 뱃지 (국내 공시·미국 공시·실적콜·모델 계산) */}
          {c.page ? <span className="sv-meta mono">{c.page}</span> : null}
          <button className="sv-x" onClick={onClose} aria-label="닫기">✕</button>
        </div>

        <div className="sv-body">
          <div className="sv-stage">
            {isDeck ? (
              // an 8-K presentation deck — render the real slides (pdf.js) + highlight the cited chunk
              <DeckViewer c={c} />
            ) : showDerivation && evInput ? (
              // an INPUT's own source page (filing cell highlighted) — back returns to the derivation
              <div className="sv-ev-input">
                <button className="sv-act mono" onClick={() => setEvInput(null)}>← 계산 과정으로</button>
                <FilingViewer c={evInput} />
              </div>
            ) : showDerivation ? (
              // DRV-3: the derivation IS the body of a derived figure's preview — formula +
              // sourced inputs + steps take precedence over rendering a source page.
              <article className="sv-page">
                <div className="sv-page-hd mono">{c.source || "추출 데이터"}{c.ticker ? ` · ${c.ticker}` : ""}</div>
                <DerivationCard comp={c.computation!}
                  onEvidence={(url, row) => setEvInput({
                    evidence_image_url: url, source: row.source || c.source,
                    kind: "filing", snippet: `${row.label} = ${row.value}`,
                  })} />
                {c.table ? <SrcTable table={c.table} /> : null}
                <p className="sv-data-note mono">{c.as_of ? `as_of ${c.as_of} · ` : ""}출처에서 가져오거나 계산한 값이에요 (입력마다 원문 셀을 열 수 있어요)</p>
              </article>
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
            ) : shape === "data" && evInput ? (
              // an INPUT's own source page (filing cell highlighted) — back returns to the derivation
              <div className="sv-ev-input">
                <button className="sv-act mono" onClick={() => setEvInput(null)}>← 계산 과정으로</button>
                <FilingViewer c={evInput} />
              </div>
            ) : shape === "data" ? (
              <article className="sv-page">
                <div className="sv-page-hd mono">{c.source || "추출 데이터"}{c.ticker ? ` · ${c.ticker}` : ""}</div>
                {c.computation ? (
                  // DRV-3: the derivation IS the body of a derived figure's preview —
                  // formula + sourced inputs + steps; the snippet/table become secondary.
                  <DerivationCard comp={c.computation}
                    onEvidence={(url, row) => setEvInput({
                      evidence_image_url: url, source: row.source || c.source,
                      kind: "filing", snippet: `${row.label} = ${row.value}`,
                    })} />
                ) : null}
                {c.table ? <SrcTable table={c.table} /> : null}
                {c.snippet ? (
                  <div className="sv-data mono"><span className="sv-pin mono">{c.index ?? "1"}</span>{c.snippet}</div>
                ) : null}
                <p className="sv-data-note mono">{c.as_of ? `as_of ${c.as_of} · ` : ""}출처에서 가져오거나 계산한 값이에요 (표시된 셀이 인용 근거)</p>
              </article>
            ) : (
              <article className="sv-page">
                <div className="sv-page-hd mono">{c.source || "공시 문서"}{c.page ? ` · ${c.page}` : ""}</div>
                {c.doc_type && c.doc_type !== "news" ? <h4 className="sv-page-h">{c.doc_type}</h4> : null}
                <p className="sv-skel-l" /><p className="sv-skel-l" style={{ width: "88%" }} />
                <div className="sv-quote">
                  <span className="sv-pin mono">{c.index ?? "1"}</span>
                  {c.snippet || "인용한 원문 구절을 불러오지 못했어요."}
                </div>
                <p className="sv-skel-l" style={{ width: "94%" }} /><p className="sv-skel-l" style={{ width: "70%" }} />
              </article>
            )}
          </div>

          <aside className="sv-ctx">
            <div className="sv-ctx-h mono">이 원문을 인용한 곳</div>
            <div className="sv-ctx-card">
              <div className="sv-ctx-snip">{c.snippet ? `“${c.snippet}”` : "이 답변이 인용한 출처예요."}</div>
              {c.index ? <div className="sv-ctx-n mono">인용 [{c.index}]</div> : null}
            </div>
            <div className="sv-ctx-meta mono">
              <div><FreshnessDot f={c.freshness} /> {fresh ?? "—"}</div>
              {c.as_of ? <div>as_of {c.as_of}</div> : null}
              {c.ticker ? <div>{c.ticker}</div> : null}
              {c.page ? <div>{c.page}</div> : null}
            </div>
            <div className="sv-ctx-actions">
              {c.url ? <a className="sv-act primary" href={c.url} target="_blank" rel="noreferrer">원문 보기 ↗</a> : null}
              <button className="sv-act" onClick={copyCite}>{copied ? "복사됨 ✓" : "인용 복사"}</button>
              {onQuote && (c.snippet || c.url) ? (
                <button className="sv-act" onClick={quoteThis} title="선택한 문단(없으면 인용 구절)을 카드로 공유">
                  이 문단 카드로 ↗
                </button>
              ) : null}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
