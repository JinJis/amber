"use client";

// ASK-6/9 탐구 엔트리 (v4). 접속 시 LLM 0회 — 뉴스 질문 피드는 10분 주기 백그라운드 캐시,
// 종목 분석거리는 사용자가 종목을 "직접 눌렀을 때"만 온디맨드 생성: 소스별 후보 → 다양성
// 큐레이션으로 3~5개(가격·공시·뉴스 나열이 아니라 밸류·수급·어닝·과거가 섞이게, ASK-9).
//   · 히어로 — "오늘, 무엇을 분석할까요?" + 신뢰 한 줄(출처[n] · 전망 안 함)
//   · 2단 레이아웃: [내 관심종목 파고들기 — 종목 칩 탭 → 분석 카드 3~5개] ⟷ [지금 뉴스에서]
// 전역 규칙: 카드 탭 = 컴포저 채움(자동 전송 없음). 출처 탭 = 근거 뷰어. 미생성 = 정직한 공백.

import { useEffect, useMemo, useState } from "react";
import type { Citation } from "@/lib/types";
import { QCard, type AskCard } from "./QCard";

type TickerInfo = { market: string; ticker: string; name: string; groups: string[] };

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

const tkKey = (t: { market: string; ticker: string }) => `${t.market}:${t.ticker}`;

export default function CockpitEntry({ onPick, onQuestions, onEvidence }: {
  onPick: (q: string) => void;                       // fill the composer, focus — never send
  onQuestions?: (qs: string[]) => void;              // today's questions → rotating placeholder
  onEvidence?: (cit: Citation) => void;              // open the source/evidence viewer
}) {
  const [tickers, setTickers] = useState<TickerInfo[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [news, setNews] = useState<AskCard[]>([]);
  const [group, setGroup] = useState<string | null>(null);      // selected 관심그룹 filter (null = all)
  // 종목 파고들기: 탭한 종목만 온디맨드 생성. sel = 펼친 종목, cardsBy = 세션 캐시.
  const [sel, setSel] = useState<string | null>(null);
  const [cardsBy, setCardsBy] = useState<Record<string, AskCard[]>>({});
  const [loadingKey, setLoadingKey] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await fetch("/api/ask-feed");
        if (r.ok && !dead) {
          const d = await r.json();
          setTickers(d.tickers ?? []); setGroups(d.groups ?? []); setNews(d.news_feed ?? []);
          const qs = (d.news_feed ?? []).map((c: AskCard) => c.question).slice(0, 6);
          if (qs.length) onQuestions?.(qs);
        }
      } catch { /* optional — composer always usable */ }
    })();
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function pickTicker(t: TickerInfo) {
    const key = tkKey(t);
    if (sel === key) { setSel(null); return; }        // tap again → fold
    setSel(key);
    if (cardsBy[key] || loadingKey === key) return;   // session cache / already fetching
    setLoadingKey(key);
    try {
      const p = new URLSearchParams({ market: t.market, ticker: t.ticker, name: t.name });
      const r = await fetch(`/api/ask-feed/ticker?${p.toString()}`);
      const d = r.ok ? await r.json() : { cards: [] };
      setCardsBy((prev) => ({ ...prev, [key]: (d.cards ?? []) as AskCard[] }));
    } catch {
      setCardsBy((prev) => ({ ...prev, [key]: [] }));  // honest gap — never fabricated
    } finally {
      setLoadingKey((k) => (k === key ? null : k));
    }
  }

  const inGroup = (g: string[]) => group === null || g.includes(group);
  const visTickers = useMemo(() => tickers.filter((t) => inGroup(t.groups)), [tickers, group]);
  const selTicker = useMemo(() => tickers.find((t) => tkKey(t) === sel) ?? null, [tickers, sel]);
  const selVisible = selTicker != null && visTickers.some((t) => tkKey(t) === sel);
  const selCards = sel ? cardsBy[sel] : undefined;

  return (
    <div className="ask" data-testid="cockpit">
      {/* 히어로 */}
      <div className="ask-hero">
        <div className="ask-eyebrow mono">RESEARCH DESK</div>
        <h1 className="ask-title">오늘, 무엇을 분석할까요?</h1>
        <p className="ask-trust">모든 답에는 <b>출처[n]</b>가 붙고, <span className="ask-noforecast">전망은 하지 않아요</span>.</p>
      </div>

      {/* 2단: 내 관심종목 파고들기 · Macro Trends */}
      <div className="ask-cols">
        {/* 왼쪽 — 내 관심종목 파고들기 (종목을 누르면 그 자리에서 3~5개 큐레이션) */}
        <section className="ask-col" data-testid="ck-mine">
          <div className="ask-col-h">
            <span className="ask-col-t">🔎 내 관심종목 파고들기</span>
          </div>
          <p className="ask-col-desc">궁금한 종목을 누르면 <b>공시·가격·뉴스·밸류에이션·수급·실적</b>을 훑어 오늘 가장 눌러볼 만한 분석거리 3~5개를 추려드려요.</p>

          {tickers.length > 0 ? (
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
              <div className="tk-row" role="listbox" aria-label="관심종목">
                {visTickers.map((t) => {
                  const key = tkKey(t);
                  const on = sel === key;
                  return (
                    <button key={key} type="button" data-testid={`tk-${t.ticker}`}
                      className={`tk-chip ${on ? "on" : ""}`} aria-pressed={on}
                      onClick={() => void pickTicker(t)}>
                      <span className="tk-name">{t.name}</span>
                      <span className="tk-mkt mono">{t.market}</span>
                      {loadingKey === key ? <span className="tl-spin" aria-hidden /> : (
                        <span className="tk-chev" aria-hidden>{on ? "▾" : "▸"}</span>
                      )}
                    </button>
                  );
                })}
              </div>

              {selTicker && selVisible && (
                <div className="tk-cards" data-testid="tk-cards">
                  {loadingKey === sel ? (
                    <div className="tk-loading" data-testid="tk-loading">
                      <span className="tl-spin" aria-hidden />
                      {selTicker.name}의 공시·가격·뉴스·밸류에이션·수급을 훑는 중…
                    </div>
                  ) : selCards && selCards.length > 0 ? (
                    <div className="qc-list">
                      {selCards.slice(0, 5).map((c, i) => (
                        <QCard key={i} c={c} name={selTicker.name} onPick={onPick} onEvidence={onEvidence} />
                      ))}
                    </div>
                  ) : selCards ? (
                    <div className="tk-gap" data-testid="tk-gap">
                      지금은 {selTicker.name}의 분석거리를 준비하지 못했어요 — 잠시 후 다시 눌러보세요.
                      직접 물어보셔도 돼요:
                      <span className="tk-gap-chips">
                        {capabilityChips(selTicker.name, selTicker.market).slice(0, 3).map((ch) => (
                          <button key={ch.label} type="button" className="grp" onClick={() => onPick(ch.q)}>{ch.label}</button>
                        ))}
                      </span>
                    </div>
                  ) : null}
                </div>
              )}
            </>
          ) : (
            <div className="nudge-card" data-testid="ck-nudge">
              <div className="nudge-t">관심종목을 등록하면</div>
              <div className="nudge-b">여기서 종목을 눌러 그날의 분석거리를 바로 받아볼 수 있어요.</div>
            </div>
          )}
        </section>

        {/* 오른쪽 — Macro Trends (뉴스+거시지표, 5분 주기 백그라운드 갱신 + read-through) */}
        {news.length > 0 && (
          <section className="ask-col" data-testid="ck-news">
            <div className="ask-col-h">
              <span className="ask-col-t">🌍 Macro Trends</span>
              <span className="ask-col-sub mono">5분마다 갱신</span>
            </div>
            <p className="ask-col-desc">실시간 뉴스와 <b>금리·물가·고용</b> 같은 거시 지표에서 지금 눌러볼 만한 질문만 골라뒀어요.</p>
            <div className="qc-list">
              {news.map((c, i) => <QCard key={i} c={c} onPick={onPick} onEvidence={onEvidence} />)}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
