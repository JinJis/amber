"use client";

// ASK-6/9 탐구 엔트리 (v7). 접속 시 LLM 0회 — 모든 카드는 백그라운드 캐시, 종목 분석거리는
// 사용자가 종목을 "직접 눌렀을 때"만 온디맨드 생성(소스별 후보 → 다양성 큐레이션).
// v7 레이아웃:
//   ① 내 관심종목 파고들기 — 관심 @그룹 칩 한 줄(가로 스크롤, ＋새그룹은 우측 sticky).
//     그룹 탭 → 아래 패널에 종목 칩 토글, 종목 탭 → 그 자리에서 분석 카드 3~5개.
//   ② 트렌드 보드 — Macro Trends·어닝 레이더·투자거장·수급·히스토리 랩을 3단 정적 칼럼으로.
//     카드 = 순위 행: 서버가 실측 인기(최근 7일 전 유저 탭 수)로 정렬, 🔥 수치 노출(0이면 숨김).
// 전역 규칙: 카드 탭 = 컴포저 채움(자동 전송 없음). 출처 탭 = 근거 뷰어. 미생성 = 정직한 공백.

import { useEffect, useState, type ReactNode } from "react";
import type { Citation } from "@/lib/types";
import { QCard, type AskCard } from "./QCard";
import { TickerLogo } from "./TickerLogo";

type TickerInfo = { market: string; ticker: string; name: string; groups: string[] };
type WatchGroup = { id: string; name: string };
// 마키 섹션 = 시장 전체 공유 캐시(스코프별). studio는 데이터만 내려주고, 프레젠테이션(제목·
// 이모지·카피)은 여기 SECTION_META가 scope로 매핑한다.
type TrendSection = { scope: string; cards: AskCard[]; generated_at?: string | null };
type SectionMeta = { title: string; emoji: string; sub?: string; desc: ReactNode; testId: string };

const SECTION_META: Record<string, SectionMeta> = {
  news_feed: {
    title: "Macro Trends", emoji: "🌍", sub: "5분마다 갱신", testId: "ck-news",
    desc: (<>실시간 뉴스와 <b>금리·물가·고용</b> 같은 거시 지표를 교차해, 지금 파볼 만한 질문만 골라뒀어요. 관심 가는 카드를 눌러 바로 살펴보세요.</>),
  },
  earnings_radar: {
    title: "어닝 레이더", emoji: "📅", sub: "실적 시즌", testId: "ck-sec-earnings_radar",
    desc: (<>곧 실적을 발표하는 <b>미국 대표주</b>들의 발표일과 과거 서프라이즈 패턴을 모았어요. 실적 전에 미리 짚어볼 질문을 눌러보세요.</>),
  },
  guru_flows: {
    title: "투자거장·수급", emoji: "🐘", sub: "13F · 수급", testId: "ck-sec-guru_flows",
    desc: (<>버핏·버리 같은 <b>거장들의 13F 매매</b>와 한국 시장 수급 쏠림을 훑었어요. 돈이 어디로 움직였는지 함께 들여다볼까요.</>),
  },
  history_lab: {
    title: "히스토리 랩", emoji: "🕰️", sub: "과거 기록 · 전망 아님", testId: "ck-sec-history_lab",
    desc: (<>지금의 <b>낙폭·변동성</b>이 과거 어디쯤인지, 비슷한 국면엔 그 뒤 어땠는지 — 예측이 아니라 <b>과거 기록</b>으로 확인해요.</>),
  },
};

// 정적 랭킹 보드 — 마키 폐기(우→좌 흐름은 스캔이 안 됨). 섹션 = 칼럼, 카드 = 순위 행.
// 순서는 서버가 실측 인기(최근 7일 전 유저 탭 수, RC-2)로 정렬해 내려준다 — 동률은 LLM
// 중요도(큐레이션 순서). 🔥 수치는 실측이라 0이면 숨긴다(날조 없음). 행 탭 = 컴포저 채움,
// 출처 탭 = 근거 뷰어. 상위 6개만 — 스캔 가능한 밀도가 마키 20장보다 낫다.
const BOARD_TOP_N = 6;

function TrendColumn({ meta, cards, onPick, onEvidence }: {
  meta: SectionMeta; cards: AskCard[];
  onPick: (q: string) => void; onEvidence?: (cit: Citation) => void;
}) {
  const top = cards.slice(0, BOARD_TOP_N);
  return (
    <section className="tb-col" data-testid={meta.testId}>
      <div className="tb-h">
        <span className="tb-t">{meta.emoji} {meta.title}</span>
        {meta.sub ? <span className="tb-sub mono">{meta.sub}</span> : null}
      </div>
      <ol className="tb-list">
        {top.map((c, i) => {
          const cit = c.citations?.[0];
          return (
            <li key={i} className="tb-row">
              <button type="button" className="tb-main" onClick={() => {
                try { fetch("/api/ask-feed/tap", { method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ kind: c.kind, ticker: c.ticker ?? null, question: c.question }) }).catch(() => {}); } catch {}
                onPick(c.query || c.question);
              }}>
                <span className="tb-rank mono" aria-hidden>{i + 1}</span>
                <span className="tb-body">
                  <span className="tb-q">{c.question}</span>
                  <span className="tb-meta">
                    {(c.taps ?? 0) > 0 && (
                      <span className="tb-taps mono" data-testid="tb-taps"
                        title={`최근 7일 동안 ${c.taps}번 열어본 질문이에요`}>🔥 {c.taps}</span>
                    )}
                    <span className="tb-hook">{c.hook}</span>
                  </span>
                </span>
              </button>
              {cit?.source ? (
                <button type="button" className="tb-src" title="근거 보기"
                  onClick={(e) => { e.stopPropagation(); onEvidence?.(cit); }}>
                  {cit.source}
                </button>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

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

export default function CockpitEntry({ onPick, onQuestions, onEvidence, onManageWatch }: {
  onPick: (q: string) => void;                       // fill the composer, focus — never send
  onQuestions?: (qs: string[]) => void;              // today's questions → rotating placeholder
  onEvidence?: (cit: Citation) => void;              // open the source/evidence viewer
  onManageWatch?: (groupId?: string) => void;        // 관심 페이지로 이동 (해당 그룹 활성 + 종목 검색)
}) {
  const [tickers, setTickers] = useState<TickerInfo[]>([]);
  const [groups, setGroups] = useState<WatchGroup[]>([]);
  const [sections, setSections] = useState<TrendSection[]>([]);      // 마키 섹션들 (Macro Trends + 어닝·거장·히스토리)
  const [openGroup, setOpenGroup] = useState<string | null>(null);   // 펼친 @그룹 (name)
  // 종목 파고들기: 탭한 종목만 온디맨드 생성. sel = 펼친 종목, cardsBy = 세션 캐시.
  const [sel, setSel] = useState<string | null>(null);
  const [cardsBy, setCardsBy] = useState<Record<string, AskCard[]>>({});
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  // ＋ 새 그룹 — 관심 페이지의 인라인 생성 플로우 그대로
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [busyNew, setBusyNew] = useState(false);
  const [newErr, setNewErr] = useState("");

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await fetch("/api/ask-feed");
        if (r.ok && !dead) {
          const d = await r.json();
          setTickers(d.tickers ?? []);
          // 구버전 studio(문자열 그룹) 응답도 그리게 — id 없으면 name을 임시 id로.
          setGroups((d.groups ?? []).map((g: WatchGroup | string) =>
            typeof g === "string" ? { id: g, name: g } : g));
          // 신버전 studio는 sections 배열(스코프별 카드)을 내려준다. 구버전(또는 기존 테스트)은
          // news_feed만 → Macro Trends 단일 섹션으로 폴백. 카드 있는 섹션·아는 스코프만 그린다.
          const secs: TrendSection[] = (d.sections?.length
            ? d.sections
            : (d.news_feed?.length ? [{ scope: "news_feed", cards: d.news_feed }] : []))
            .filter((s: TrendSection) => SECTION_META[s.scope] && (s.cards?.length ?? 0) > 0);
          setSections(secs);
          const macro = secs.find((s) => s.scope === "news_feed");
          // RC-3: 콜드스타트 0 — Macro Trends가 비면(가입 직후) 온보딩 쇼케이스 카드로 즉시 채움.
          if (!macro) {
            try {
              const sr = await fetch("/api/onboarding-showcase");
              if (sr.ok) {
                const sj = await sr.json();
                if (sj?.cards?.length) {
                  setSections((prev) => [{ scope: "news_feed", cards: sj.cards.slice(0, 4) }, ...prev]);
                  onQuestions?.(sj.cards.slice(0, 6).map((c: AskCard) => c.question));
                }
              }
            } catch { /* 빈 상태 유지 — 정직한 갭 */ }
          }
          const qs = (macro?.cards ?? []).map((c: AskCard) => c.question).slice(0, 6);
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

  async function createGroup() {
    const nm = newName.trim();
    if (!nm || busyNew) return;
    setNewErr(""); setBusyNew(true);
    try {
      const r = await fetch("/api/watchlists", { method: "POST", body: JSON.stringify({ name: nm }) });
      // 서버 detail은 영어·격식체라 그대로 노출하지 않고 한국어 해요체로 매핑 (해요체 불변조건).
      if (!r.ok) { setNewErr(r.status === 409 ? "이미 같은 이름의 그룹이 있어요." : "그룹을 만들지 못했어요."); return; }
      const wl = await r.json();
      setCreating(false); setNewName("");
      onManageWatch?.(wl.id);   // 관심 페이지로 — 방금 만든 그룹에 실제 종목을 담도록
    } catch {
      setNewErr("그룹을 만들지 못했어요.");
    } finally { setBusyNew(false); }
  }

  const selTicker = tickers.find((t) => tkKey(t) === sel) ?? null;
  const selCards = sel ? cardsBy[sel] : undefined;

  const tickerCards = (t: TickerInfo) => (
    <div className="tk-cards" data-testid="tk-cards">
      {loadingKey === sel ? (
        <div className="tk-loading" data-testid="tk-loading">
          <span className="tl-spin" aria-hidden />
          {t.name}의 공시·가격·뉴스·밸류에이션·수급을 훑는 중…
        </div>
      ) : selCards && selCards.length > 0 ? (
        <div className="qc-list">
          {selCards.slice(0, 5).map((c, i) => (
            <QCard key={i} c={c} name={t.name} onPick={onPick} onEvidence={onEvidence} />
          ))}
        </div>
      ) : selCards ? (
        <div className="tk-gap" data-testid="tk-gap">
          지금은 {t.name}의 분석거리를 준비하지 못했어요 — 잠시 후 다시 눌러보세요.
          직접 물어보셔도 돼요:
          <span className="tk-gap-chips">
            {capabilityChips(t.name, t.market).slice(0, 3).map((ch) => (
              <button key={ch.label} type="button" className="grp" onClick={() => onPick(ch.q)}>{ch.label}</button>
            ))}
          </span>
        </div>
      ) : null}
    </div>
  );

  return (
    <div className="ask" data-testid="cockpit">
      {/* 히어로 */}
      <div className="ask-hero">
        <h1 className="ask-title">오늘, 무엇을 분석할까요?</h1>
        <p className="ask-trust">모든 답에는 <b>출처[n]</b>가 붙고, <span className="ask-noforecast">전망은 하지 않아요</span>.</p>
      </div>

      <div className="ask-secs">
        {/* ① 내 관심종목 파고들기 — @그룹 아코디언 → 종목 칩 → 온디맨드 분석 카드 */}
        <section className="ask-sec" data-testid="ck-mine">
          <div className="ask-col-h">
            <span className="ask-col-t">🔎 내 관심종목 파고들기</span>
          </div>
          <p className="ask-col-desc">그룹을 열고 궁금한 종목을 누르면 <b>공시·가격·뉴스·밸류에이션·수급·실적</b>을 훑어 오늘 가장 눌러볼 만한 분석거리 3~5개를 추려드려요.</p>

          {groups.length > 0 ? (
            <div className="eg-wrap">
              {/* 1차 depth = @그룹 칩(가로로 나열, 넘치면 줄바꿈). 칩을 누르면 아래 패널에서
                  그 그룹의 종목이 토글되고, 종목을 누르면 그 자리에서 분석 카드. */}
              <div className="eg-chips">
                {groups.map((g) => {
                  const members = tickers.filter((t) => t.groups.includes(g.name));
                  const open = openGroup === g.name;
                  return (
                    <button key={g.id} type="button" className={`eg-chip ${open ? "on" : ""}`}
                      data-testid={`grp-${g.name}`} aria-expanded={open}
                      onClick={() => { setOpenGroup(open ? null : g.name); setSel(null); }}>
                      {members.length > 0 && (
                        <span className="eg-logos" aria-hidden>
                          {members.slice(0, 3).map((t) => (
                            <span key={tkKey(t)} className="eg-logo">
                              <TickerLogo market={t.market} ticker={t.ticker} name={t.name} size={16} />
                            </span>
                          ))}
                        </span>
                      )}
                      <span className="eg-name">@{g.name}</span>
                      <span className="eg-count mono">{members.length}</span>
                      <span className="eg-chev" aria-hidden>{open ? "▾" : "▸"}</span>
                    </button>
                  );
                })}

                {/* 리스트 끝 ＋ 새 그룹 — 관심 페이지의 생성 플로우 그대로(칩 형태). 만들면
                    관심 페이지로 이동해 실제 종목을 담는다(onManageWatch). */}
                {creating ? (
                  <span className="eg-new">
                    <input className="input" autoFocus placeholder="그룹 이름 (예: 반도체바스켓)" value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void createGroup();
                        if (e.key === "Escape") { setCreating(false); setNewName(""); setNewErr(""); }
                      }} />
                    <button type="button" className="grp on" onClick={() => void createGroup()}
                      disabled={busyNew || !newName.trim()}>추가</button>
                    <button type="button" className="grp"
                      onClick={() => { setCreating(false); setNewName(""); setNewErr(""); }}>취소</button>
                  </span>
                ) : (
                  <button type="button" className="wl-addgroup eg-add-chip" data-testid="eg-add"
                    onClick={() => setCreating(true)}>＋ 새 그룹</button>
                )}
              </div>
              {newErr && <div className="eg-new-err">{newErr}</div>}

              {/* 펼친 그룹의 종목 패널 — 칩 줄 아래 전체폭으로 */}
              {(() => {
                const g = openGroup ? groups.find((x) => x.name === openGroup) : null;
                if (!g) return null;
                const members = tickers.filter((t) => t.groups.includes(g.name));
                return (
                  <div className="eg-panel" data-testid={`grp-body-${g.name}`}>
                    {members.length === 0 ? (
                      <div className="tk-gap">
                        아직 담긴 종목이 없어요.
                        <span className="tk-gap-chips">
                          <button type="button" className="grp" onClick={() => onManageWatch?.(g.id)}>＋ 종목 담으러 가기</button>
                        </span>
                      </div>
                    ) : (
                      <>
                        <div className="tk-row" role="listbox" aria-label={`@${g.name} 종목`}>
                          {members.map((t) => {
                            const key = tkKey(t);
                            const on = sel === key;
                            return (
                              <button key={key} type="button" data-testid={`tk-${t.ticker}`}
                                className={`tk-chip ${on ? "on" : ""}`} aria-pressed={on}
                                onClick={() => void pickTicker(t)}>
                                <TickerLogo market={t.market} ticker={t.ticker} name={t.name} size={20} />
                                <span className="tk-name">{t.name}</span>
                                <span className="tk-mkt mono">{t.market}</span>
                                {loadingKey === key ? <span className="tl-spin" aria-hidden /> : (
                                  <span className="tk-chev" aria-hidden>{on ? "▾" : "▸"}</span>
                                )}
                              </button>
                            );
                          })}
                        </div>
                        {selTicker && members.some((t) => tkKey(t) === sel) && tickerCards(selTicker)}
                      </>
                    )}
                  </div>
                );
              })()}
            </div>
          ) : (
            <div className="nudge-card" data-testid="ck-nudge">
              <div className="nudge-t">관심 그룹을 만들면</div>
              <div className="nudge-b">여기서 그룹 속 종목을 눌러 그날의 분석거리를 바로 받아볼 수 있어요.</div>
              <button type="button" className="wl-addgroup nudge-cta" onClick={() => onManageWatch?.()}>＋ 관심 그룹 만들기</button>
            </div>
          )}
        </section>

        {/* ② 트렌드 보드 — Macro Trends·어닝 레이더·투자거장·수급·히스토리 랩을 관심종목
            아래 3단 칼럼으로. 정적 + 실측 인기 랭킹(서버 정렬). 아는 스코프만 그린다. */}
        {sections.length > 0 && (
          <div className="tb-grid" data-testid="ck-board">
            {sections.map((s) => (
              <TrendColumn key={s.scope} meta={SECTION_META[s.scope]} cards={s.cards}
                onPick={onPick} onEvidence={onEvidence} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
