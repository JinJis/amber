"use client";

// ENT (관제탑 엔트리) — the empty 탐색 state. One focal point: the composer (the footer
// composer is visually promoted by the parent; this hero supplies everything around it):
//   ① 시장 오프닝 스트립 — live indices/FX/VIX, every figure tap-to-ask (never auto-send)
//   ② 오늘의 제안 — top 3 desk-feed cards as a quiet list (hook = why, question = what)
//   ③ 내 종목 스트립 — watchlist tickers with day moves; tap → capability question chips
//   ④ 오늘의 데스크 — the old card grid, demoted to a fold
// Global rule: every tap FILLS the composer; the user always presses Enter themselves.

import { useEffect, useState } from "react";
import DeskHome from "./DeskHome";
import type { Artifact } from "@/lib/types";

type PulseItem = { label: string; ticker: string; price?: number | null; change_percent?: number | null; as_of?: string | null };
type WatchTick = { ticker: string; market: string; name?: string | null; price?: number | null; change_percent?: number | null };
type FeedCard = { kind: string; question: string; hook: string; citations?: { source?: string; as_of?: string }[] };

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
  v == null ? "" : `${v > 0 ? "▲" : v < 0 ? "▼" : ""}${Math.abs(v).toFixed(v >= 100 ? 0 : 1)}%`;

export default function CockpitEntry({ onPick, onQuestions, onChanged, onShareBriefing }: {
  onPick: (q: string) => void;                       // fill the composer, focus — never send
  onQuestions?: (qs: string[]) => void;              // today's questions → rotating placeholder
  onChanged?: () => void;
  onShareBriefing?: (a: Artifact) => void;
}) {
  const [pulse, setPulse] = useState<PulseItem[]>([]);
  const [pulseAsOf, setPulseAsOf] = useState<string | null>(null);
  const [ticks, setTicks] = useState<WatchTick[]>([]);
  const [cards, setCards] = useState<FeedCard[]>([]);
  const [picked, setPicked] = useState<WatchTick | null>(null);
  const [deskOpen, setDeskOpen] = useState(false);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await fetch("/api/market/pulse");
        if (r.ok && !dead) {
          const d = await r.json();
          setPulse(d.items ?? []);
          setPulseAsOf(d.as_of ?? null);
        }
      } catch { /* the strip is optional — never blocks the composer */ }
      try {
        const r = await fetch("/api/market/watch");
        if (r.ok && !dead) setTicks(((await r.json()).tickers ?? []));
      } catch { /* optional */ }
      try {
        const r = await fetch("/api/desk-feed");
        if (r.ok && !dead) {
          const feed = await r.json();
          const data = (feed.cards ?? []).filter(
            (c: FeedCard) => c.kind !== "watchlist_nudge" && c.kind !== "continue_thread" && c.question);
          setCards(data.slice(0, 3));
          onQuestions?.(data.map((c: FeedCard) => c.question).slice(0, 6));
        }
      } catch { /* optional */ }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="cockpit" data-testid="cockpit">
      {/* ① 시장 오프닝 스트립 */}
      {pulse.length > 0 && (
        <div className="ck-strip mono" title={pulseAsOf ? `as of ${pulseAsOf}` : undefined}>
          {pulse.map((p) => (
            <button key={p.ticker} type="button" className="ck-tickeritem"
              onClick={() => onPick(`오늘 ${p.label} 왜 ${((p.change_percent ?? 0) >= 0 ? "올랐" : "내렸")}는지 설명해줘`)}
              title="탭하면 질문이 입력창에 담깁니다">
              <span className="ck-lbl">{p.label}</span>
              <span className="ck-px">{p.price?.toLocaleString(undefined, { maximumFractionDigits: 1 })}</span>
              <span className={`ck-chg ${(p.change_percent ?? 0) >= 0 ? "up" : "down"}`}>{fmtPct(p.change_percent)}</span>
            </button>
          ))}
        </div>
      )}

      <h2 className="ck-h">오늘의 시장, 무엇이 궁금하세요?</h2>

      {/* ② 오늘의 제안 3 — hook(왜) + question(무엇) */}
      {cards.length > 0 && (
        <div className="ck-suggest" data-testid="ck-suggest">
          <div className="ck-sec-h mono">오늘 물어볼 만한 것</div>
          {cards.map((c, i) => (
            <button key={i} type="button" className="ck-sg" onClick={() => onPick(c.question)}>
              <span className="ck-sg-hook">{c.hook}</span>
              <span className="ck-sg-q">“{c.question}” →</span>
              {c.citations?.[0]?.source ? <span className="ck-sg-src mono">{c.citations[0].source}</span> : null}
            </button>
          ))}
        </div>
      )}

      {/* ③ 내 종목 스트립 → 능력 칩 */}
      {ticks.length > 0 && (
        <div className="ck-watch">
          <div className="ck-sec-h mono">내 종목</div>
          <div className="ck-ticks">
            {ticks.map((t) => (
              <button key={t.ticker} type="button"
                className={`ck-tick ${picked?.ticker === t.ticker ? "on" : ""}`}
                onClick={() => setPicked((p) => (p?.ticker === t.ticker ? null : t))}>
                {t.name || t.ticker}
                {t.change_percent != null && (
                  <span className={`ck-chg ${(t.change_percent ?? 0) >= 0 ? "up" : "down"} mono`}> {fmtPct(t.change_percent)}</span>
                )}
              </button>
            ))}
          </div>
          {picked && (
            <div className="ck-caps" data-testid="ck-caps">
              {capabilityChips(picked.name || picked.ticker, picked.market).map((c) => (
                <button key={c.label} type="button" className="ck-cap" onClick={() => onPick(c.q)}>{c.label}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ④ 데스크 접이 (콜드 유저는 펼쳐서 넛지가 보이게) */}
      <div className="ck-desk">
        <button type="button" className="ck-desk-toggle" onClick={() => setDeskOpen((v) => !v)} aria-expanded={deskOpen}>
          {deskOpen ? "▾" : "▸"} 오늘의 데스크
        </button>
        {(deskOpen || ticks.length === 0) && (
          <DeskHome onPick={onPick} onChanged={onChanged} onShareBriefing={onShareBriefing} />
        )}
      </div>
    </div>
  );
}
