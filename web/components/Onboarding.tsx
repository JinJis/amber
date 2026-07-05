"use client";

// 온보딩 v2 (ASK-6) — three steps that explain the product and guarantee a non-empty first
// screen: ① 소개(제품 약속 3문장) → ② 관심종목 등록(필수, 최소 3종목 — 물어보기 첫 화면이
// 이 종목들로 채워진다) → ③ 착륙. The alerts/board steps are gone (FLAG-1 dead branches).
// No skip: the ask-feed entry is watchlist-derived, so an empty watchlist means an empty
// product — registration IS the onboarding.

import { useMemo, useState } from "react";
import type { Features } from "@/lib/features";
import { PRESETS } from "@/lib/presets";
import { Button } from "./ui";

type StepKey = "intro" | "watch" | "land";
type Pick = { market: string; ticker: string; name?: string };

const MIN_TICKERS = 3;
const STEPS: StepKey[] = ["intro", "watch", "land"];

const keyOf = (p: Pick) => `${p.market}:${p.ticker}`;

export default function Onboarding({ onDone }: { features?: Features; onDone: () => void }) {
  const [step, setStep] = useState(0);
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
          <div className="onb-brand"><span className="mascot" aria-hidden /> ValueGraph</div>
          <div className="onb-progress">{STEPS.map((s, i) => <span key={s} className={`onb-dot ${i <= step ? "on" : ""}`} />)}</div>
          <span />
        </div>

        {key === "intro" && (
          <div className="onb-step" data-testid="onb-intro">
            <div className="onb-k">STEP 1 · 소개</div>
            <h2>당신의 리서치 데스크</h2>
            <div className="onb-promises">
              <div className="onb-promise">
                <span className="onb-pi">[n]</span>
                <div><b>물어보면, 출처와 함께 답합니다</b>
                  <p>모든 숫자가 공시·시세·통계의 원본 기록에 [n]으로 연결됩니다 — 근거 없는 수치는 애초에 표시되지 않아요.</p></div>
              </div>
              <div className="onb-promise">
                <span className="onb-pi">✎</span>
                <div><b>물어볼 거리를 먼저 준비해둡니다</b>
                  <p>관심종목을 등록하면, 새 공시·가격·뉴스에서 나온 오늘의 질문거리가 첫 화면에 미리 채워져요.</p></div>
              </div>
              <div className="onb-promise">
                <span className="onb-pi">⏳</span>
                <div><b>전망은 하지 않습니다</b>
                  <p>과거 기록과 현재 데이터만 다룹니다. 예측·목표가·매수 조언은 이 데스크의 일이 아니에요 — 그게 신뢰의 조건입니다.</p></div>
              </div>
            </div>
          </div>
        )}

        {key === "watch" && (
          <div className="onb-step" data-testid="onb-watch">
            <div className="onb-k">STEP 2 · 관심종목 <b>(필수)</b></div>
            <h2>지켜볼 종목을 골라주세요</h2>
            <p className="onb-note">물어보기 첫 화면이 이 종목들의 질문거리로 채워집니다 — 최소 {MIN_TICKERS}종목.</p>
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
            <input className="input onb-search" value={query} placeholder="종목 검색해서 추가 — 예: 삼성전자, NVDA"
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
              {picks.size < MIN_TICKERS ? `${picks.size}/${MIN_TICKERS} 선택됨 — ${MIN_TICKERS - picks.size}개 더 골라주세요` : `${picks.size}종목 선택됨 ✓`}
            </p>
          </div>
        )}

        {key === "land" && (
          <div className="onb-step" data-testid="onb-land">
            <div className="onb-k">STEP 3 · 시작</div>
            <h2>데스크가 준비를 시작합니다</h2>
            <div className="onb-landing">
              <div className="onb-land-row"><b>관심종목</b> {[...picks.values()].map((p) => p.name || p.ticker).join(" · ")}</div>
              <div className="onb-land-bot">✎ 물어보기 첫 화면에 이 종목들의 질문거리가 곧 채워져요 · 모든 답에 출처 첨부</div>
            </div>
            <p className="onb-note">질문거리는 몇 분 안에 준비됩니다 — 그동안 무엇이든 바로 물어보세요.</p>
          </div>
        )}

        <div className="onb-foot">
          {step > 0 ? <Button variant="ghost" onClick={() => setStep((s) => s - 1)} disabled={busy}>이전</Button> : <span />}
          {!last
            ? <Button onClick={() => setStep((s) => s + 1)} disabled={busy || (key === "watch" && picks.size < MIN_TICKERS)}>다음 →</Button>
            : <Button onClick={finish} disabled={busy}>{busy ? "준비 중…" : "물어보기 시작 →"}</Button>}
        </div>
      </div>
    </div>
  );
}
