"use client";

// 분석거리 카드 (ASK-6/9) — 엔트리 화면·온보딩 프리뷰가 함께 쓰는 최소 단위.
// 카드 본문 탭 = 컴포저 채움(실행용 query 우선, 없으면 표시용 question), 출처 칩 탭 = 근거 뷰어.

import type { Citation } from "@/lib/types";
import { TickerLogo } from "./TickerLogo";

export type AskCard = { kind: string; question: string; query?: string | null; hook: string;
                        ticker?: string | null; market?: string | null; citations?: Citation[] };

// per-kind emoji + short label — one warm mark per card.
export const KIND: Record<string, { i: string; t: string }> = {
  filing_deep:      { i: "📄", t: "공시" },
  price_context:    { i: "📈", t: "가격" },
  news_probe:       { i: "📰", t: "뉴스" },
  history_echo:     { i: "🕰️", t: "과거" },
  fundamental_shift:{ i: "📊", t: "재무" },
  valuation:        { i: "💰", t: "밸류에이션" },
  ownership:        { i: "👥", t: "수급·보유" },
  earnings:         { i: "📅", t: "실적" },
  macro:            { i: "🌍", t: "거시" },
  micro:            { i: "🏭", t: "산업" },
  market:           { i: "📉", t: "시장" },
};

export function QCard({ c, name, onPick, onEvidence }: {
  c: AskCard; name?: string; onPick: (q: string) => void; onEvidence?: (cit: Citation) => void;
}) {
  const k = KIND[c.kind] ?? { i: "•", t: "" };
  const cit = c.citations?.[0];
  return (
    <div className="qc">
      <button type="button" className="qc-main" onClick={() => {
        // RC-1: 탭 신호(fire-and-forget) — 실패해도 UX 무영향
        try { fetch("/api/ask-feed/tap", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind: c.kind, ticker: c.ticker ?? null }) }).catch(() => {}); } catch {}
        onPick(c.query || c.question);
      }}>
        <div className="qc-top">
          {c.ticker ? <TickerLogo market={c.market} ticker={c.ticker} name={name || c.ticker} size={20} />
                    : <span className="qc-emoji" aria-hidden>{k.i}</span>}
          {name ? <span className="qc-tkr">{name}</span> : null}
          {k.t ? <span className="qc-kind">{k.t}</span> : null}
        </div>
        <div className="qc-q">{c.question}</div>
        <div className="qc-hook">{c.hook}</div>
      </button>
      {cit?.source ? (
        <button type="button" className="qc-src" title="근거 보기"
          onClick={(e) => { e.stopPropagation(); onEvidence?.(cit); }}>
          <span className="qc-src-dot" aria-hidden />
          <span className="qc-src-name">{cit.source}</span>
          <span className="qc-src-cta mono">근거 보기 →</span>
        </button>
      ) : null}
    </div>
  );
}
