"use client";

// ASK-5 물어보기 엔트리 (v2 — 현대적 브리핑 UI). 접속 시 LLM 0회: 모든 콘텐츠는 5분 주기
// ask-feed refresher가 미리 만들어 둔 캐시. 한 화면에서 "이 서비스가 뭘 하는지"가 읽히도록:
//   · 히어로 — 제품 약속 한 줄 (출처로 답하는 리서치 데스크 · 전망 안 함)
//   · 시장 펄스 — 지수/환율 한 줄, 탭하면 그 지수 질문으로
//   · 내 종목 질문거리 — 티커 필터 칩 + 최신 기록에서 뽑은 딥 질문 카드(왜 지금 + 출처)
//   · Hot Trend — 거시/미시/시장 지금 벌어지는 일
// 전역 규칙: 모든 탭은 컴포저를 채운다(자동 전송 없음). 미생성 티커는 "준비 중" 스켈레톤.

import { useEffect, useMemo, useState } from "react";

type PulseItem = { label: string; ticker: string; price?: number | null; change_percent?: number | null; as_of?: string | null };
type WatchTick = { ticker: string; market: string; name?: string | null; price?: number | null; change_percent?: number | null };
type AskCard = { kind: string; question: string; hook: string; ticker?: string | null; market?: string | null;
                 citations?: { source?: string; as_of?: string }[] };
type TickerPool = { market: string; ticker: string; name: string; cards: AskCard[]; generated_at?: string | null };

// per-kind glyph + short label — restrained, one monochrome mark per card (not an emoji zoo).
const KIND: Record<string, { g: string; t: string }> = {
  filing_deep:      { g: "▤", t: "공시" },
  price_context:    { g: "◔", t: "가격" },
  news_probe:       { g: "◈", t: "뉴스" },
  history_echo:     { g: "◷", t: "과거" },
  fundamental_shift:{ g: "▦", t: "재무" },
  macro:            { g: "◍", t: "거시" },
  micro:            { g: "▣", t: "미시" },
  market:           { g: "◆", t: "시장" },
};

// ENT-4: capability chips per company — the product's real verbs, market-aware.
export function capabilityChips(name: string, market: string): { label: string; q: string }[] {
  const chips = [
    { label: "실적·재무", q: `${name} 최근 실적·재무 알려줘` },
    { label: "주가 차트", q: `${name} 최근 주가 흐름 차트로 보여줘` },
    { label: "밸류에이션", q: `${name} 밸류에이션 지표(PER·PBR·시가총액) 알려줘` },
    { label: "최근 공시", q: `${name} 최근 공시 뭐 있어?` },
    { label: "뉴스", q: `${name} 최근 뉴스 브리핑해줘` },
    { label: "과거 낙폭", q: `${name} 지금 낙폭이 과거와 비교하면 어때?` },
  ];
  chips.push(market === "KR"
    ? { label: "수급(외인·기관)", q: `${name} 외국인·기관 수급 어때?` }
    : { label: "거장 보유(13F)", q: `${name} 들고 있는 투자 거장 있어?` });
  return chips;
}

const fmtPct = (v?: number | null) =>
  v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(v <= -100 || v >= 100 ? 0 : 2)}%`;
const dir = (v?: number | null) => (v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat");

function QCard({ c, name, onPick }: { c: AskCard; name?: string; onPick: (q: string) => void }) {
  const k = KIND[c.kind] ?? { g: "·", t: "" };
  return (
    <button type="button" className="qc" onClick={() => onPick(c.question)}>
      <div className="qc-top">
        <span className="qc-glyph" aria-hidden>{k.g}</span>
        {name ? <span className="qc-tkr">{name}</span> : null}
        {k.t ? <span className="qc-kind">{k.t}</span> : null}
      </div>
      <div className="qc-q">{c.question}</div>
      <div className="qc-foot">
        <span className="qc-hook">{c.hook}</span>
        {c.citations?.[0]?.source ? <span className="qc-src mono">{c.citations[0].source}</span> : null}
      </div>
    </button>
  );
}

export default function CockpitEntry({ onPick, onQuestions }: {
  onPick: (q: string) => void;                       // fill the composer, focus — never send
  onQuestions?: (qs: string[]) => void;              // today's questions → rotating placeholder
}) {
  const [pulse, setPulse] = useState<PulseItem[]>([]);
  const [pulseAsOf, setPulseAsOf] = useState<string | null>(null);
  const [ticks, setTicks] = useState<WatchTick[]>([]);
  const [pools, setPools] = useState<TickerPool[]>([]);
  const [pending, setPending] = useState<{ market: string; ticker: string; name: string }[]>([]);
  const [hot, setHot] = useState<AskCard[]>([]);
  const [sel, setSel] = useState<string | null>(null);          // selected ticker filter (null = all)

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await fetch("/api/market/pulse");
        if (r.ok && !dead) { const d = await r.json(); setPulse(d.items ?? []); setPulseAsOf(d.as_of ?? null); }
      } catch { /* the strip is optional */ }
      try {
        const r = await fetch("/api/market/watch");
        if (r.ok && !dead) setTicks(((await r.json()).tickers ?? []));
      } catch { /* optional */ }
      try {
        const r = await fetch("/api/ask-feed");
        if (r.ok && !dead) {
          const d = await r.json();
          const ps: TickerPool[] = d.tickers ?? [];
          setPools(ps); setPending(d.pending ?? []); setHot(d.hot_trend ?? []);
          const qs = ps.flatMap((p) => p.cards.map((c) => c.question)).slice(0, 6);
          if (qs.length) onQuestions?.(qs);
        }
      } catch { /* optional — composer always usable */ }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const moveOf = (t: string) => ticks.find((x) => x.ticker === t)?.change_percent;
  const selPool = sel ? pools.find((p) => p.ticker === sel) : null;

  // the card grid: one ticker selected → its full set; otherwise a briefing of each ticker's top 2.
  const gridCards = useMemo(() => {
    if (selPool) return selPool.cards.map((c) => ({ c, name: selPool.name }));
    return pools.flatMap((p) => p.cards.slice(0, 2).map((c) => ({ c, name: p.name })));
  }, [selPool, pools]);

  const hasMine = pools.length > 0 || pending.length > 0;

  return (
    <div className="ask" data-testid="cockpit">
      {/* 히어로 — 무슨 서비스인지 한 줄에 */}
      <div className="ask-hero">
        <div className="ask-eyebrow mono">RESEARCH DESK</div>
        <h1 className="ask-title">오늘, 무엇을 물어볼까요?</h1>
        <p className="ask-sub">
          관심 종목의 <b>최신 공시·가격·뉴스</b>에서 추린 질문이에요. 탭하면 입력창에 담기고,
          모든 답에는 <b>출처[n]</b>가 붙습니다 — <span className="ask-noforecast">전망은 하지 않아요</span>.
        </p>
      </div>

      {/* 시장 펄스 — 한 줄 */}
      {pulse.length > 0 && (
        <div className="ask-pulse" title={pulseAsOf ? `as of ${pulseAsOf}` : undefined}>
          {pulse.map((p) => (
            <button key={p.ticker} type="button" className="pulse-pill"
              onClick={() => onPick(`오늘 ${p.label} 왜 ${((p.change_percent ?? 0) >= 0 ? "올랐" : "내렸")}는지 설명해줘`)}>
              <span className="pulse-lbl">{p.label}</span>
              <span className="pulse-px mono">{p.price?.toLocaleString(undefined, { maximumFractionDigits: 1 })}</span>
              <span className={`pulse-chg mono ${dir(p.change_percent)}`}>{fmtPct(p.change_percent)}</span>
            </button>
          ))}
        </div>
      )}

      {/* 내 종목 질문거리 */}
      {hasMine ? (
        <section className="ask-sec" data-testid="ck-mine">
          <div className="ask-sec-h"><span className="ask-sec-t">내 종목 질문거리</span></div>

          {/* 티커 필터 칩 (전체 + 각 종목, 등락 배지) */}
          <div className="tkr-row">
            <button type="button" className={`tkr ${sel === null ? "on" : ""}`} onClick={() => setSel(null)}>전체</button>
            {pools.map((p) => (
              <button key={p.ticker} type="button" className={`tkr ${sel === p.ticker ? "on" : ""}`}
                data-testid={`tkr-${p.ticker}`}
                onClick={() => setSel((v) => (v === p.ticker ? null : p.ticker))}>
                {p.name}
                {moveOf(p.ticker) != null && <span className={`tkr-chg mono ${dir(moveOf(p.ticker))}`}>{fmtPct(moveOf(p.ticker))}</span>}
              </button>
            ))}
            {pending.map((p) => (
              <span key={p.ticker} className="tkr pend" data-testid="ck-pending" title="다음 갱신에 질문거리가 채워집니다">
                {p.name}<span className="tkr-dot" /> 준비 중
              </span>
            ))}
          </div>

          {/* 선택된 종목의 능력 칩 */}
          {selPool && (
            <div className="cap-row" data-testid="ck-caps">
              {capabilityChips(selPool.name, selPool.market).map((c) => (
                <button key={c.label} type="button" className="cap" onClick={() => onPick(c.q)}>{c.label}</button>
              ))}
            </div>
          )}

          {/* 질문 카드 그리드 */}
          <div className="qc-grid">
            {gridCards.map(({ c, name }, i) => (
              <QCard key={i} c={c} name={sel ? undefined : name} onPick={onPick} />
            ))}
          </div>
        </section>
      ) : (
        <section className="ask-sec ask-nudge" data-testid="ck-nudge">
          <div className="nudge-card">
            <div className="nudge-t">관심종목을 등록하면</div>
            <div className="nudge-b">매일 이 자리에 오늘 물어볼 거리를 미리 준비해둡니다.</div>
          </div>
        </section>
      )}

      {/* Hot Trend */}
      {hot.length > 0 && (
        <section className="ask-sec" data-testid="ck-hot">
          <div className="ask-sec-h"><span className="ask-sec-t">Hot Trend</span><span className="ask-sec-sub">지금 시장에서</span></div>
          <div className="qc-grid">
            {hot.map((c, i) => <QCard key={i} c={c} onPick={onPick} />)}
          </div>
        </section>
      )}
    </div>
  );
}
