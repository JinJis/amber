"use client";

// 온보딩 — first-run flow. Chat-first (FLAG-1): the 알림(채널) step and the 대시보드 template landing
// only appear when their features are on; with both off the flow is 시장 → 관심 → 착륙(탐색으로 시작),
// creating the picked @관심 groups and dropping the user straight into chat. On finish it also applies a
// recommended dashboard template + a first alert *only when those surfaces are enabled*. Marks the user
// onboarded server-side.

import { useState } from "react";
import { CHANNELS, ChannelKind } from "@/lib/alerts";
import type { Features } from "@/lib/features";
import { PRESETS } from "@/lib/presets";
import { ChannelIcon } from "./ChannelIcon";
import { Button } from "./ui";

type StepKey = "market" | "watch" | "alerts" | "land";

export default function Onboarding({ features, onDone }: { features: Features; onDone: () => void }) {
  // Build the visible steps from the flags — the 알림 step is skipped entirely when alerts are off.
  const steps: StepKey[] = ["market", "watch", ...(features.alerts ? (["alerts"] as StepKey[]) : []), "land"];
  const [step, setStep] = useState(0);
  const key = steps[step];
  const last = step === steps.length - 1;

  const [market, setMarket] = useState<"KR" | "US" | "both">("both");
  const [groups, setGroups] = useState<Set<string>>(new Set(["semi"]));
  const [chans, setChans] = useState<Set<ChannelKind>>(new Set(features.alerts ? ["telegram"] : []));
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
      // 1) create @관심 groups + members (always — watchlists are the chat-first substrate)
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
      // 2) non-empty landing — only if the 대시보드 surface is enabled
      if (features.dashboard) {
        const tpl = picked[0]?.tpl ?? (market === "KR" ? "dt_semi" : market === "US" ? "dt_bigtech" : "dt_macro");
        await fetch("/api/board/from-template", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template_id: tpl }) }).catch(() => {});
      }
      // 3) first recommended alert — only if the 알림봇 surface is enabled and a channel was picked
      if (features.alerts && chans.size && firstGroupName) {
        await fetch("/api/alerts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          name: `${firstGroupName} 실적·금리 알림`, scope: "board", trigger_type: "earnings",
          params: { target: "@" + firstGroupName }, schedule: { freq: "event" }, channels: [...chans],
        }) }).catch(() => {});
      }
      await fetch("/api/onboarded", { method: "POST" }).catch(() => {});
    } finally { onDone(); }
  }

  const stepNo = (k: StepKey) => steps.indexOf(k) + 1;

  return (
    <div className="onb-backdrop">
      <div className="onb">
        <div className="onb-top">
          <div className="onb-brand"><span className="mascot" aria-hidden /> ValueGraph</div>
          <div className="onb-progress">{steps.map((s, i) => <span key={s} className={`onb-dot ${i <= step ? "on" : ""}`} />)}</div>
          <button className="onb-skip" onClick={skip} disabled={busy}>건너뛰기</button>
        </div>

        {key === "market" && (
          <div className="onb-step">
            <div className="onb-k">STEP {stepNo("market")} · 시장</div>
            <h2>어느 시장을 보시나요?</h2>
            <div className="onb-row">
              {([["KR", "🇰🇷 한국"], ["US", "🇺🇸 미국"], ["both", "둘 다"]] as const).map(([v, l]) => (
                <button key={v} className={`onb-pick ${market === v ? "on" : ""}`} onClick={() => setMarket(v)}>{l}</button>
              ))}
            </div>
            <p className="onb-note">활성화할 데이터 소스를 자동으로 맞춰드려요.</p>
          </div>
        )}

        {key === "watch" && (
          <div className="onb-step">
            <div className="onb-k">STEP {stepNo("watch")} · 관심</div>
            <h2>추천 관심 그룹 담기</h2>
            <div className="onb-grid">
              {PRESETS.map((p) => (
                <button key={p.id} className={`onb-card ${groups.has(p.id) ? "on" : ""}`} onClick={() => toggleGroup(p.id)}>
                  <span className="onb-card-n">{p.name}</span>
                  <span className="onb-card-c">{p.items.length}종목</span>
                </button>
              ))}
            </div>
            <p className="onb-note">@그룹으로 탐색에서 호출하고, 데스크가 이 그룹의 변화를 매일 챙겨줘요.</p>
          </div>
        )}

        {key === "alerts" && (
          <div className="onb-step">
            <div className="onb-k">STEP {stepNo("alerts")} · 알림</div>
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

        {key === "land" && (
          <div className="onb-step">
            <div className="onb-k">STEP {stepNo("land")} · 착륙</div>
            <h2>{features.dashboard ? "비어있지 않은 대시보드" : "바로 탐색으로"}</h2>
            <div className="onb-landing">
              <div className="onb-land-row"><b>시장</b> {market === "both" ? "한국 + 미국" : market === "KR" ? "한국" : "미국"}</div>
              <div className="onb-land-row"><b>관심</b> {PRESETS.filter((p) => groups.has(p.id)).map((p) => p.name).join(" · ") || "없음"}</div>
              {features.alerts && (
                <div className="onb-land-row"><b>알림</b> {[...chans].map((k) => CHANNELS.find((c) => c.kind === k)?.label).join(" · ") || "없음"}</div>
              )}
              <div className="onb-land-bot">✎ 탐색에서 관심 그룹으로 바로 질문할 수 있어요 · 출처 첨부됨</div>
            </div>
            <p className="onb-note">{features.dashboard ? "추천 템플릿으로 채운 대시보드로 들어갑니다." : "관심 그룹을 만들고 탐색(챗)으로 들어갑니다."}</p>
          </div>
        )}

        <div className="onb-foot">
          {step > 0 ? <Button variant="ghost" onClick={() => setStep((s) => s - 1)} disabled={busy}>이전</Button> : <span />}
          {!last
            ? <Button onClick={() => setStep((s) => s + 1)} disabled={busy}>다음 →</Button>
            : <Button onClick={finish} disabled={busy}>{busy ? "준비 중…" : (features.dashboard ? "대시보드 시작 →" : "탐색 시작 →")}</Button>}
        </div>
      </div>
    </div>
  );
}
