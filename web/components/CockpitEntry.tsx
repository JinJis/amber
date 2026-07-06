"use client";

// ASK-5 탐구 엔트리 (v3 — 전문적 + 친근). 접속 시 LLM 0회: 모든 콘텐츠는 5분 주기 ask-feed
// refresher가 미리 만든 캐시. 한 화면에서 서비스 정체성이 읽히도록:
//   · 히어로 — "오늘, 무엇을 분석할까요?" + 신뢰 한 줄(출처[n] · 전망 안 함)
//   · 티커 테이프 — 증권거래소처럼 오른쪽→왼쪽으로 무한히 흐르는 지수/환율 (탭 → 질문)
//   · 2단 레이아웃: [내 관심종목 파고들기] ⟷ [Hot Trend]
//       - 관심그룹 필터, 최신 기록 기반 분석 카드(이모지 아이콘 · 왜 지금 · 눌러보는 출처)
// 전역 규칙: 카드 탭 = 컴포저 채움(자동 전송 없음). 출처 탭 = 근거 뷰어. 미생성 = "준비 중".

import { useEffect, useMemo, useState } from "react";
import type { Citation } from "@/lib/types";

type PulseItem = { label: string; ticker: string; price?: number | null; change_percent?: number | null; as_of?: string | null };
type AskCard = { kind: string; question: string; hook: string; ticker?: string | null; market?: string | null;
                 citations?: Citation[] };
type TickerPool = { market: string; ticker: string; name: string; groups: string[]; cards: AskCard[] };
type Pending = { market: string; ticker: string; name: string; groups: string[] };

// per-kind emoji + short label — one warm mark per card.
const KIND: Record<string, { i: string; t: string }> = {
  filing_deep:      { i: "📄", t: "공시" },
  price_context:    { i: "📈", t: "가격" },
  news_probe:       { i: "📰", t: "뉴스" },
  history_echo:     { i: "🕰️", t: "과거" },
  fundamental_shift:{ i: "📊", t: "재무" },
  macro:            { i: "🌍", t: "거시" },
  micro:            { i: "🏭", t: "산업" },
  market:           { i: "📉", t: "시장" },
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

// One analysis card: the body fills the composer; the source chip opens the evidence viewer.
function QCard({ c, name, onPick, onEvidence }: {
  c: AskCard; name?: string; onPick: (q: string) => void; onEvidence?: (cit: Citation) => void;
}) {
  const k = KIND[c.kind] ?? { i: "•", t: "" };
  const cit = c.citations?.[0];
  return (
    <div className="qc">
      <button type="button" className="qc-main" onClick={() => onPick(c.question)}>
        <div className="qc-top">
          <span className="qc-emoji" aria-hidden>{k.i}</span>
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

export default function CockpitEntry({ onPick, onQuestions, onEvidence }: {
  onPick: (q: string) => void;                       // fill the composer, focus — never send
  onQuestions?: (qs: string[]) => void;              // today's questions → rotating placeholder
  onEvidence?: (cit: Citation) => void;              // open the source/evidence viewer
}) {
  const [pulse, setPulse] = useState<PulseItem[]>([]);
  const [pools, setPools] = useState<TickerPool[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [hot, setHot] = useState<AskCard[]>([]);
  const [group, setGroup] = useState<string | null>(null);      // selected 관심그룹 filter (null = all)

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await fetch("/api/market/pulse");
        if (r.ok && !dead) setPulse(((await r.json()).items ?? []));
      } catch { /* the tape is optional */ }
      try {
        const r = await fetch("/api/ask-feed");
        if (r.ok && !dead) {
          const d = await r.json();
          const ps: TickerPool[] = d.tickers ?? [];
          setPools(ps); setPending(d.pending ?? []); setHot(d.hot_trend ?? []); setGroups(d.groups ?? []);
          const qs = ps.flatMap((p) => p.cards.map((c) => c.question)).slice(0, 6);
          if (qs.length) onQuestions?.(qs);
        }
      } catch { /* optional — composer always usable */ }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const inGroup = (g: string[]) => group === null || g.includes(group);
  const visPools = useMemo(() => pools.filter((p) => inGroup(p.groups)), [pools, group]);
  const visPending = useMemo(() => pending.filter((p) => inGroup(p.groups)), [pending, group]);
  // each visible ticker contributes its top 3 cards, self-labeled with the company name.
  const mineCards = useMemo(
    () => visPools.flatMap((p) => p.cards.slice(0, 3).map((c) => ({ c, name: p.name }))),
    [visPools]);

  const hasMine = pools.length > 0 || pending.length > 0;
  // duplicate the tape items so the marquee loops seamlessly.
  const tape = pulse.length ? [...pulse, ...pulse] : [];

  return (
    <div className="ask" data-testid="cockpit">
      {/* 히어로 */}
      <div className="ask-hero">
        <div className="ask-eyebrow mono">RESEARCH DESK</div>
        <h1 className="ask-title">오늘, 무엇을 분석할까요?</h1>
        <p className="ask-trust">모든 답에는 <b>출처[n]</b>가 붙고, <span className="ask-noforecast">전망은 하지 않아요</span>.</p>
      </div>

      {/* 티커 테이프 — 증권거래소처럼 무한 흐름 */}
      {tape.length > 0 && (
        <div className="tape" data-testid="ck-tape" aria-label="시장 시세">
          <div className="tape-track">
            {tape.map((p, i) => (
              <button key={i} type="button" className="tape-item" tabIndex={i < pulse.length ? 0 : -1}
                aria-hidden={i >= pulse.length}
                onClick={() => onPick(`오늘 ${p.label}가 ${((p.change_percent ?? 0) >= 0 ? "올랐" : "내렸")}는데, 왜 그런지 같이 알아볼까요?`)}>
                <span className="tape-lbl">{p.label}</span>
                <span className="tape-px mono">{p.price?.toLocaleString(undefined, { maximumFractionDigits: 1 })}</span>
                <span className={`tape-chg mono ${dir(p.change_percent)}`}>
                  {p.change_percent != null && (p.change_percent > 0 ? "▲" : p.change_percent < 0 ? "▼" : "·")}{fmtPct(p.change_percent).replace("+", "")}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* 2단: 내 관심종목 파고들기 · Hot Trend */}
      <div className="ask-cols">
        {/* 왼쪽 — 내 관심종목 파고들기 */}
        <section className="ask-col" data-testid="ck-mine">
          <div className="ask-col-h">
            <span className="ask-col-t">🔎 내 관심종목 파고들기</span>
          </div>
          <p className="ask-col-desc">관심 종목의 <b>최신 공시·가격·뉴스</b>에서 추린 분석거리예요. 탭하면 입력창에 담겨요.</p>

          {hasMine ? (
            <>
              {groups.length > 1 && (
                <div className="grp-row">
                  <button type="button" className={`grp ${group === null ? "on" : ""}`} onClick={() => setGroup(null)}>전체</button>
                  {groups.map((g) => (
                    <button key={g} type="button" className={`grp ${group === g ? "on" : ""}`}
                      data-testid={`grp-${g}`} onClick={() => setGroup((v) => (v === g ? null : g))}>{g}</button>
                  ))}
                </div>
              )}
              <div className="qc-list">
                {mineCards.map(({ c, name }, i) => (
                  <QCard key={i} c={c} name={name} onPick={onPick} onEvidence={onEvidence} />
                ))}
              </div>
              {visPending.length > 0 && (
                <div className="pend-row" data-testid="ck-pending">
                  {visPending.map((p) => (
                    <span key={p.ticker} className="pend" title="다음 갱신에 분석거리가 채워집니다">
                      <span className="pend-dot" />{p.name} · 준비 중
                    </span>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="nudge-card" data-testid="ck-nudge">
              <div className="nudge-t">관심종목을 등록하면</div>
              <div className="nudge-b">매일 이 자리에 오늘 파고들 분석거리를 미리 준비해둡니다.</div>
            </div>
          )}
        </section>

        {/* 오른쪽 — Hot Trend */}
        {hot.length > 0 && (
          <section className="ask-col" data-testid="ck-hot">
            <div className="ask-col-h">
              <span className="ask-col-t">🔥 Hot Trend</span>
            </div>
            <p className="ask-col-desc">지금 시장 전반에서 벌어지는 일 — <b>거시·산업·시장</b>을 한눈에.</p>
            <div className="qc-list">
              {hot.map((c, i) => <QCard key={i} c={c} onPick={onPick} onEvidence={onEvidence} />)}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
