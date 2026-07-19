"use client";

// 온보딩 v3 (ONB-3) — 서비스를 말로 설명하지 않고 "직접 보여주는" 6스텝.
//   ① intro    — 정체성: 등록한 종목을 진짜 데이터(SEC EDGAR·8-K·어닝콜·재무제표·DART)로
//                딥 리서치, 뉴스와 교차검증해 지어낸 숫자를 거른다 (할루시네이션 방지)
//   ② evidence — 근거 패널 프리뷰: 하이라이트 수치 + 판정 스트립 + 실제 SourceCard 3종
//   ③ cards    — 종목 탭 → 큐레이션된 분석거리 카드 프리뷰 (실제 QCard)
//   ④ chain    — 꼬리물기 리서치: 답 뒤에 이어지는 후속 질문 칩 프리뷰
//   ⑤ watch    — 관심종목 등록 (필수, 최소 3종목 — min-3 게이트)
//   ⑥ land     — 착륙
// 프리뷰는 진짜 컴포넌트 + 가짜 데이터 + "예시 화면" 배지 (무날조 원칙). 스킵 없음:
// ask-feed 첫 화면이 관심종목에서 나오므로, 등록이 곧 온보딩이다.

import { useMemo, useState, useEffect } from "react";
import { Logo } from "./Logo";
import { PRESETS } from "@/lib/presets";
import { FIX_CITATIONS, FIX_FOLLOWUPS, FIX_QCARDS, FIX_TRUST } from "@/lib/onboardingFixtures";
import type { AskCard } from "@/components/QCard";
import type { Citation } from "@/lib/types";

// ONB-LIVE: 라이브 쇼케이스(핫 KR 종목 · 일 1회 갱신). 로드 실패/빈 응답 → 픽스처 폴백(정직 배지).
type Showcase = { question?: string; name?: string; cards?: AskCard[];
  evidence?: Citation[]; followups?: string[] };
import { Button } from "./ui";
import { QCard } from "./QCard";
import { SourceCard } from "./SourceCard";
import { TrustStrip } from "./EvidencePanel";

type StepKey = "intro" | "evidence" | "cards" | "chain" | "watch" | "land";
type Pick = { market: string; ticker: string; name?: string };

const MIN_TICKERS = 3;
const STEPS: StepKey[] = ["intro", "evidence", "cards", "chain", "watch", "land"];

const keyOf = (p: Pick) => `${p.market}:${p.ticker}`;

// 프리뷰 래퍼 — 진짜 컴포넌트를 만질 수 없는 "예시 화면"으로 감싼다.
function Preview({ children, testid, badge }: { children: React.ReactNode; testid: string; badge?: string }) {
  return (
    <div className="onb-preview" data-testid={testid} aria-hidden>
      <span className="onb-demo-badge mono">{badge ?? "예시 화면"}</span>
      {children}
    </div>
  );
}

export default function Onboarding({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [live, setLive] = useState<Showcase | null>(null);
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/onboarding-showcase");
        if (r.ok) { const j = await r.json(); if (j?.cards?.length) setLive(j); }
      } catch { /* 픽스처 폴백 */ }
    })();
  }, []);
  const liveBadge = live ? `실시간 데이터 · ${live.name ?? "삼성전자"}` : undefined;
  const liveTrust = live ? {
    checked: 0, unsupported: 0, sources: (live.evidence ?? []).length,
    freshness: { fresh: (live.evidence ?? []).filter((c) => c.freshness === "fresh").length,
                 aging: (live.evidence ?? []).filter((c) => c.freshness === "aging").length,
                 stale: (live.evidence ?? []).filter((c) => c.freshness === "stale").length },
    allClear: true, conceptual: false,
  } : null;

  const key = STEPS[step];
  const last = step === STEPS.length - 1;

  const [market, setMarket] = useState<"KR" | "US" | "both">("both");
  const [picks, setPicks] = useState<Map<string, Pick>>(new Map());
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Pick[]>([]);
  const [busy, setBusy] = useState(false);

  const filt = (m: string) => market === "both" || m === market;
  const visPresets = useMemo(
    () => PRESETS.map((p) => ({ ...p, items: p.items.filter((i) => filt(i.market)) }))
      .filter((p) => p.items.length > 0),
    [market]);  // eslint-disable-line react-hooks/exhaustive-deps

  const togglePick = (it: Pick) => setPicks((prev) => {
    const n = new Map(prev);
    n.has(keyOf(it)) ? n.delete(keyOf(it)) : n.set(keyOf(it), it);
    return n;
  });
  const togglePreset = (items: Pick[]) => setPicks((prev) => {
    const n = new Map(prev);
    const allIn = items.every((i) => n.has(keyOf(i)));
    for (const i of items) allIn ? n.delete(keyOf(i)) : n.set(keyOf(i), i);
    return n;
  });

  async function search(q: string) {
    setQuery(q);
    if (q.trim().length < 2) { setResults([]); return; }
    try {
      const r = await fetch(`/api/company/search?q=${encodeURIComponent(q.trim())}`);
      if (r.ok) {
        const d = await r.json();
        setResults(((d.results ?? d.companies ?? []) as Pick[]).filter((x) => x.ticker).slice(0, 6));
      }
    } catch { /* search is best-effort; presets always work */ }
  }

  async function finish() {
    setBusy(true);
    try {
      // one 내 관심 group with every picked ticker — the @handle chat + ask-feed both read.
      const r = await fetch("/api/watchlists", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "내 관심" }),
      });
      if (r.ok) {
        const wl = await r.json();
        for (const it of picks.values()) {
          await fetch(`/api/watchlists/${wl.id}/items`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify(it),
          }).catch(() => {});
        }
      }
      await fetch("/api/onboarded", { method: "POST" }).catch(() => {});
    } finally { onDone(); }
  }

  return (
    <div className="onb-backdrop">
      <div className="onb">
        <div className="onb-top">
          <div className="onb-brand"><Logo size={20} /></div>
          <div className="onb-progress">{STEPS.map((s, i) => <span key={s} className={`onb-dot ${i <= step ? "on" : ""}`} />)}</div>
          <span />
        </div>

        {key === "intro" && (
          <div className="onb-step" data-testid="onb-intro">
            <div className="onb-k">1 / {STEPS.length} · 어떤 서비스인가요</div>
            <h2>궁금한 종목, 진짜 데이터로 파고드는 리서치 데스크예요</h2>
            <div className="onb-promises">
              <div className="onb-promise">
                <span className="onb-pi">🔎</span>
                <div><b>등록한 종목을 깊이 리서치해요</b>
                  <p>관심종목을 등록해두면, 최신 데이터를 계속 살피면서 오늘 파볼 만한 리서치 포인트를 먼저 추천해드려요.</p></div>
              </div>
              <div className="onb-promise">
                <span className="onb-pi">[n]</span>
                <div><b>모든 답은 실제 기록에서 출발해요</b>
                  <p>SEC EDGAR·8-K·어닝콜·재무제표·DART 공시 같은 <b>원자료</b>를 직접 보여드리고, 뉴스 등 여러 데이터와
                    <b> 교차검증</b>해서 AI가 지어낸 숫자를 걸러내요.</p></div>
              </div>
              <div className="onb-promise">
                <span className="onb-pi">⏳</span>
                <div><b>전망이나 매수 조언은 하지 않아요</b>
                  <p>과거 기록과 현재 데이터까지만 다뤄요. 예측·목표가가 없는 게 이 데스크를 믿을 수 있는 이유예요.</p></div>
              </div>
            </div>
          </div>
        )}

        {key === "evidence" && (
          <div className="onb-step" data-testid="onb-evidence">
            <div className="onb-k">2 / {STEPS.length} · 근거</div>
            <h2>답변 옆엔 늘 이런 근거가 붙어요</h2>
            <Preview testid="onb-preview-evidence" badge={liveBadge}>
              <p className="onb-fake-answer">
                {live?.cards?.[0]?.hook
                  ? <>{live.cards[0].hook} <button type="button" className="cite-ref mono">[1]</button></>
                  : <>삼성전자 1분기 매출은 <span className="num-hl">79.1조</span>
                      <button type="button" className="cite-ref mono">[1]</button>로 전년 대비{" "}
                      <span className="num-hl">12%</span> 늘었어요.</>}
              </p>
              <TrustStrip s={(liveTrust ?? FIX_TRUST) as typeof FIX_TRUST} />
              <div className="onb-srcs">
                {(live?.evidence?.length ? live.evidence : FIX_CITATIONS).map((c, i) => <SourceCard key={i} c={c} />)}
              </div>
            </Preview>
            <p className="onb-caption">노란 숫자에 마우스를 올리면 원자료와 바로 대조돼요. 공시 원문·뉴스·데이터 표까지
              어디서 온 숫자인지 끝까지 확인할 수 있어요.</p>
          </div>
        )}

        {key === "cards" && (
          <div className="onb-step" data-testid="onb-cards">
            <div className="onb-k">3 / {STEPS.length} · 리서치 포인트</div>
            <h2>종목을 누르면, 오늘 볼만한 분석거리를 추려드려요</h2>
            <Preview testid="onb-preview-cards" badge={liveBadge}>
              <div className="tk-row">
                <span className="tk-chip on"><span className="tk-name">삼성전자</span><span className="tk-mkt mono">KR</span></span>
              </div>
              <div className="qc-list">
                {(live?.cards?.length ? live.cards.slice(0, 3) : FIX_QCARDS).map((c, i) => (
                  <QCard key={i} c={c} name={live?.name ?? "삼성전자"} onPick={() => {}} />
                ))}
              </div>
            </Preview>
            <p className="onb-caption">공시·가격·뉴스·밸류에이션·수급을 훑어서 지금 가장 눌러볼 만한 것만 골라요.
              카드를 누르면 그대로 질문이 시작돼요.</p>
          </div>
        )}

        {key === "chain" && (
          <div className="onb-step" data-testid="onb-chain">
            <div className="onb-k">4 / {STEPS.length} · 꼬리물기</div>
            <h2>답이 끝나면, 다음 질문이 이어져요</h2>
            <Preview testid="onb-preview-chain" badge={liveBadge}>
              {live?.question && <p className="onb-user-q">🙋 “{live.question}”</p>}
              <p className="onb-fake-answer onb-fade">
                {live?.cards?.[1]?.hook ?? live?.cards?.[0]?.hook
                  ?? "…매출 성장의 대부분은 반도체 부문에서 나왔고, 영업이익률은 두 분기 연속 개선됐어요"}{" "}
                <button type="button" className="cite-ref mono">[2]</button>
              </p>
              <div className="fu-label">이어서 더 파고들기</div>
              <div className="fu-list">
                {(live?.followups?.length ? live.followups : FIX_FOLLOWUPS).map((q, i) => (
                  <button key={i} type="button" className="fu-chip">{q} <span className="fu-arrow">→</span></button>
                ))}
              </div>
            </Preview>
            <p className="onb-caption">한 번의 질문이 리서치 흐름이 되도록, 답변을 읽고 나면 다음으로 파볼 갈래를
              제안해드려요 — 꼬리에 꼬리를 무는 리서치.</p>
          </div>
        )}

        {key === "watch" && (
          <div className="onb-step" data-testid="onb-watch">
            <div className="onb-k">5 / {STEPS.length} · 관심종목 <b>(필수)</b></div>
            <h2>함께 지켜볼 종목을 골라주세요</h2>
            <p className="onb-note">여기서 고른 종목이 <b>데스크의 기본 유니버스</b>가 돼요 — 첫 화면의 분석거리,
              뉴스 훑기, 갱신 알림이 전부 이 종목들 중심으로 돌아가요. 최소 {MIN_TICKERS}종목이 필요해요.</p>
            <p className="onb-note onb-at-tip">💡 고른 종목은 그룹으로 저장되고, 채팅에서 <b className="mono">@그룹이름</b>으로
              한 번에 불러요 — 예: <span className="mono">“@반도체 실적 비교해줘”</span></p>
            <div className="onb-row">
              {([["KR", "🇰🇷 한국"], ["US", "🇺🇸 미국"], ["both", "둘 다"]] as const).map(([v, l]) => (
                <button key={v} className={`onb-pick ${market === v ? "on" : ""}`} onClick={() => setMarket(v)}>{l}</button>
              ))}
            </div>
            <div className="onb-grid">
              {visPresets.map((p) => (
                <button key={p.id} className={`onb-card ${p.items.every((i) => picks.has(keyOf(i))) ? "on" : ""}`}
                  onClick={() => togglePreset(p.items)}>
                  <span className="onb-card-n">{p.name}</span>
                  <span className="onb-card-c">{p.items.length}종목</span>
                </button>
              ))}
            </div>
            <input className="input onb-search" value={query} placeholder="종목 이름으로 찾기 — 예: 삼성전자, NVDA"
              onChange={(e) => search(e.target.value)} />
            {results.length > 0 && (
              <div className="onb-results">
                {results.map((r) => (
                  <button key={keyOf(r)} className={`onb-pick ${picks.has(keyOf(r)) ? "on" : ""}`} onClick={() => togglePick(r)}>
                    {r.name || r.ticker} <span className="mono">{r.ticker}</span>
                  </button>
                ))}
              </div>
            )}
            {picks.size > 0 && (
              <div className="onb-picked" data-testid="onb-picked">
                {[...picks.values()].map((p) => (
                  <button key={keyOf(p)} className="onb-chip" onClick={() => togglePick(p)} title="빼기">
                    {p.name || p.ticker} ✕
                  </button>
                ))}
              </div>
            )}
            <p className="onb-note onb-count" data-testid="onb-count">
              {picks.size < MIN_TICKERS ? `${picks.size}/${MIN_TICKERS} 선택 — ${MIN_TICKERS - picks.size}개만 더 골라주세요` : `${picks.size}종목 선택 완료 ✓`}
            </p>
          </div>
        )}

        {key === "land" && (
          <div className="onb-step" data-testid="onb-land">
            <div className="onb-k">6 / {STEPS.length} · 시작</div>
            <h2>데스크가 준비를 시작해요</h2>
            <div className="onb-landing">
              <div className="onb-land-row"><b>관심종목</b> {[...picks.values()].map((p) => p.name || p.ticker).join(" · ")}</div>
              <div className="onb-land-bot">✎ 첫 화면에 이 종목들의 리서치 포인트가 곧 채워져요 · 모든 답에는 출처가 붙어요</div>
            </div>
            <p className="onb-note">리서치 포인트는 몇 분 안에 준비돼요 — 그동안 무엇이든 바로 물어보세요.</p>
          </div>
        )}

        <div className="onb-foot">
          {step > 0 ? <Button variant="ghost" onClick={() => setStep((s) => s - 1)} disabled={busy}>이전</Button> : <span />}
          {!last
            ? <Button onClick={() => setStep((s) => s + 1)} disabled={busy || (key === "watch" && picks.size < MIN_TICKERS)}>다음 →</Button>
            : <Button onClick={finish} disabled={busy}>{busy ? "준비하고 있어요…" : "시작하기 →"}</Button>}
        </div>
      </div>
    </div>
  );
}
