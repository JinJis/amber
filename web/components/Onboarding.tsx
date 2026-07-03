"use client";

// 온보딩 — wireframe 01. First-run, 4 steps: 시장 → 관심 → 알림 → 대시보드(착륙). Skippable; on finish
// it creates the picked @관심 groups, applies a recommended dashboard template (non-empty landing),
// and registers a first alert ("🔔 첫 알림 봇 대기 중"). Marks the user onboarded server-side.

import { useState } from "react";
import { CHANNELS, ChannelKind } from "@/lib/alerts";
import { ChannelIcon } from "./ChannelIcon";
import { Button } from "./ui";

type Preset = { id: string; name: string; tpl: string; items: { market: string; ticker: string; name?: string }[] };

const PRESETS: Preset[] = [
  { id: "semi", name: "반도체", tpl: "dt_semi", items: [
    { market: "KR", ticker: "005930.KS", name: "삼성전자" }, { market: "KR", ticker: "000660.KS", name: "SK하이닉스" },
    { market: "US", ticker: "NVDA", name: "NVIDIA" }, { market: "US", ticker: "TSM", name: "TSMC" }, { market: "US", ticker: "ASML", name: "ASML" }] },
  { id: "ai", name: "AI·빅테크", tpl: "dt_bigtech", items: [
    { market: "US", ticker: "NVDA" }, { market: "US", ticker: "MSFT" }, { market: "US", ticker: "GOOGL" },
    { market: "US", ticker: "AAPL" }, { market: "US", ticker: "META" }, { market: "US", ticker: "AMZN" }] },
  { id: "energy", name: "에너지·원자재", tpl: "dt_energy", items: [
    { market: "US", ticker: "XOM" }, { market: "US", ticker: "CVX" }, { market: "US", ticker: "COP" }, { market: "US", ticker: "SLB" }] },
  { id: "dividend", name: "배당·인컴", tpl: "dt_dividend", items: [
    { market: "US", ticker: "JNJ" }, { market: "US", ticker: "PG" }, { market: "US", ticker: "KO" }, { market: "US", ticker: "PEP" }] },
];

const STEPS = ["시장", "관심", "알림", "착륙"];

export default function Onboarding({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [market, setMarket] = useState<"KR" | "US" | "both">("both");
  const [groups, setGroups] = useState<Set<string>>(new Set(["semi"]));
  const [chans, setChans] = useState<Set<ChannelKind>>(new Set(["telegram"]));
  const [busy, setBusy] = useState(false);

  const toggleGroup = (id: string) => setGroups((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const toggleChan = (k: ChannelKind) => setChans((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; });

  async function skip() {
    setBusy(true);
    try { await fetch("/api/onboarded", { method: "POST" }); } catch {}
    onDone();
  }

  async function finish() {
    setBusy(true);
    const picked = PRESETS.filter((p) => groups.has(p.id));
    const filt = (m: string) => market === "both" || m === market;
    try {
      // 1) create @관심 groups + members
      let firstGroupName: string | null = null;
      for (const p of picked) {
        try {
          const r = await fetch("/api/watchlists", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: p.name }) });
          if (!r.ok) continue;
          const wl = await r.json();
          firstGroupName = firstGroupName ?? p.name;
          for (const it of p.items.filter((i) => filt(i.market))) {
            await fetch(`/api/watchlists/${wl.id}/items`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(it) }).catch(() => {});
          }
        } catch {}
      }
      // 2) non-empty landing: apply a recommended template
      const tpl = picked[0]?.tpl ?? (market === "KR" ? "dt_semi" : market === "US" ? "dt_bigtech" : "dt_macro");
      await fetch("/api/board/from-template", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_id: tpl }) }).catch(() => {});
      // 3) first recommended alert (실적·금리) on the picked group, to the chosen channels
      if (chans.size && firstGroupName) {
        await fetch("/api/alerts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          name: `${firstGroupName} 실적·금리 알림`, scope: "board", trigger_type: "earnings",
          params: { target: "@" + firstGroupName }, schedule: { freq: "event" }, channels: [...chans],
        }) }).catch(() => {});
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
          <button className="onb-skip" onClick={skip} disabled={busy}>건너뛰기</button>
        </div>

        {step === 0 && (
          <div className="onb-step">
            <div className="onb-k">STEP 1 · 시장</div>
            <h2>어느 시장을 보시나요?</h2>
            <div className="onb-row">
              {([["KR", "🇰🇷 한국"], ["US", "🇺🇸 미국"], ["both", "둘 다"]] as const).map(([v, l]) => (
                <button key={v} className={`onb-pick ${market === v ? "on" : ""}`} onClick={() => setMarket(v)}>{l}</button>
              ))}
            </div>
            <p className="onb-note">활성화할 데이터 소스를 자동으로 맞춰드려요.</p>
          </div>
        )}

        {step === 1 && (
          <div className="onb-step">
            <div className="onb-k">STEP 2 · 관심</div>
            <h2>추천 관심 그룹 담기</h2>
            <div className="onb-grid">
              {PRESETS.map((p) => (
                <button key={p.id} className={`onb-card ${groups.has(p.id) ? "on" : ""}`} onClick={() => toggleGroup(p.id)}>
                  <span className="onb-card-n">{p.name}</span>
                  <span className="onb-card-c">{p.items.length}종목</span>
                </button>
              ))}
            </div>
            <p className="onb-note">@그룹으로 탐색에서 호출하고, 알림 스코프로도 써요.</p>
          </div>
        )}

        {step === 2 && (
          <div className="onb-step">
            <div className="onb-k">STEP 3 · 알림</div>
            <h2>알림 받을 곳 연결</h2>
            <div className="onb-row onb-wrap">
              {CHANNELS.map((c) => (
                <button key={c.kind} className={`onb-pick ${chans.has(c.kind) ? "on" : ""}`} onClick={() => toggleChan(c.kind)}>
                  <ChannelIcon kind={c.kind} /> {c.label}
                </button>
              ))}
            </div>
            <p className="onb-note">@관심 그룹의 <b>실적·금리</b> 알림을 첫 봇으로 추천해요. 채널 연결은 봇에서 마무리할 수 있어요.</p>
          </div>
        )}

        {step === 3 && (
          <div className="onb-step">
            <div className="onb-k">STEP 4 · 착륙</div>
            <h2>비어있지 않은 대시보드</h2>
            <div className="onb-landing">
              <div className="onb-land-row"><b>시장</b> {market === "both" ? "한국 + 미국" : market === "KR" ? "한국" : "미국"}</div>
              <div className="onb-land-row"><b>관심</b> {PRESETS.filter((p) => groups.has(p.id)).map((p) => p.name).join(" · ") || "없음"}</div>
              <div className="onb-land-row"><b>알림</b> {[...chans].map((k) => CHANNELS.find((c) => c.kind === k)?.label).join(" · ") || "없음"}</div>
              <div className="onb-land-bot">🔔 첫 알림 봇 대기 중 · 출처 첨부됨</div>
            </div>
            <p className="onb-note">추천 템플릿으로 채운 대시보드로 들어갑니다.</p>
          </div>
        )}

        <div className="onb-foot">
          {step > 0 ? <Button variant="ghost" onClick={() => setStep((s) => s - 1)} disabled={busy}>이전</Button> : <span />}
          {step < 3
            ? <Button onClick={() => setStep((s) => s + 1)} disabled={busy}>다음 →</Button>
            : <Button onClick={finish} disabled={busy}>{busy ? "준비 중…" : "대시보드 시작 →"}</Button>}
        </div>
      </div>
    </div>
  );
}
