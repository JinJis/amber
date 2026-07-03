"use client";

// 봇 — 알림 관리 홈 (wireframe 08). Board-scope + widget-scope alerts in one place. Left: alert card
// grid (status toggle, trigger chip, scope badge 보드/위젯, channel mini-badges, next/last fire,
// paused state). Right: recent delivery feed (per-channel preview + freshness + "대시보드에서 보기 ↗").
// Top: connected-channel status + ＋ 새 알림.

import { useCallback, useEffect, useState } from "react";
import { Alert, ChannelStatus, Delivery, CHANNELS, channelMeta, freshnessFromAsOf, targetLabel } from "@/lib/alerts";
import { ChannelIcon } from "./ChannelIcon";
import AlertSheet from "./AlertSheet";
import { Button, Chip, FreshnessDot, GuardrailLabel } from "./ui";

function relTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (Math.abs(diff) < 3600) return `${Math.round(Math.abs(diff) / 60)}분 ${diff >= 0 ? "전" : "후"}`;
  if (Math.abs(diff) < 86400) return `${Math.round(Math.abs(diff) / 3600)}시간 ${diff >= 0 ? "전" : "후"}`;
  return d.toISOString().slice(5, 10).replace("-", "/");
}

export default function BotHome({ onOpenDashboard }: { onOpenDashboard?: (deeplink: string) => void }) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [channels, setChannels] = useState<ChannelStatus[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [sheet, setSheet] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [a, c, d] = await Promise.all([
        fetch("/api/alerts").then((r) => (r.ok ? r.json() : { alerts: [] })),
        fetch("/api/channels").then((r) => (r.ok ? r.json() : { channels: [] })),
        fetch("/api/deliveries?limit=20").then((r) => (r.ok ? r.json() : { deliveries: [] })),
      ]);
      setAlerts(a.alerts ?? []); setChannels(c.channels ?? []); setDeliveries(d.deliveries ?? []);
    } catch {} finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function toggle(a: Alert) {
    await fetch(`/api/alerts/${a.id}/${a.status === "active" ? "pause" : "resume"}`, { method: "POST" });
    load();
  }
  async function remove(a: Alert) {
    if (!confirm(`'${a.name}' 알림을 삭제할까요?`)) return;
    await fetch(`/api/alerts/${a.id}`, { method: "DELETE" });
    load();
  }
  function openDeep(link: string) {
    if (onOpenDashboard) onOpenDashboard(link);
    else if (typeof window !== "undefined") window.location.assign(link);
  }

  const active = alerts.filter((a) => a.status === "active").length;
  const paused = alerts.length - active;
  const connectedKinds = channels.filter((c) => c.connected).map((c) => c.channel);

  return (
    <div className="bot-home">
      <div className="bot-home-head">
        <div className="bot-home-title"><h3>알림봇</h3><span className="sub">활성 {active} · 일시정지 {paused}</span></div>
        <div className="bot-home-actions">
          {CHANNELS.map((c) => {
            const on = connectedKinds.includes(c.kind);
            return <Chip key={c.kind} tone={on ? "accent" : "default"} title={on ? "연결됨" : "연결 필요"}><ChannelIcon kind={c.kind} /> {c.label}{on ? " ✓" : ""}</Chip>;
          })}
          <Button size="sm" onClick={() => setSheet(true)}>＋ 새 알림</Button>
        </div>
      </div>

      <GuardrailLabel>알림은 사실과 출처만 — 매수/매도·목표가·전망·점수는 보내지 않아요.</GuardrailLabel>

      <div className="bot-home-body">
        {/* LEFT — alert cards */}
        <div className="bot-grid">
          {loading && <div className="muted-note">불러오는 중…</div>}
          {!loading && alerts.length === 0 && (
            <div className="bot-empty">
              아직 알림이 없어요. 대시보드의 보드/위젯에서 🔔 을 누르거나,
              <Button size="sm" variant="ghost" onClick={() => setSheet(true)}>＋ 새 알림</Button> 으로 시작하세요.
            </div>
          )}
          {alerts.map((a) => (
            <div key={a.id} className={`bot-card ${a.status === "paused" ? "paused" : ""}`}>
              <div className="bot-card-head">
                <span className="bot-card-name">{a.name}</span>
                <button type="button" className={`bot-sw ${a.status === "active" ? "on" : ""}`}
                  title={a.status === "active" ? "켜짐 — 누르면 일시정지" : "일시정지 — 누르면 재개"}
                  onClick={() => toggle(a)}><span className="bot-sw-knob" /></button>
              </div>
              <div className="bot-card-chips">
                <Chip tone="ink">{a.trigger_label}</Chip>
                <Chip tone="accent">{targetLabel(a.params)}</Chip>
                <Chip title={a.scope === "widget" ? "위젯 스코프" : "보드 스코프"}>{a.scope === "widget" ? "위젯" : "보드"}</Chip>
              </div>
              <div className="bot-card-foot">
                <span className="bot-card-chan">{a.channels.map((k) => <span key={k} className="bot-mini" title={channelMeta(k).label}><ChannelIcon kind={k} /></span>)}</span>
                <span className="bot-card-sched mono">
                  {a.status === "paused" ? "일시정지됨 · 탭하여 재개"
                    : <>다음 {relTime(a.next_fire_at)} · 마지막 {relTime(a.last_fired_at)}</>}
                </span>
                <button type="button" className="bot-card-del" title="삭제" onClick={() => remove(a)}>✕</button>
              </div>
            </div>
          ))}
        </div>

        {/* RIGHT — delivery feed */}
        <aside className="bot-feed">
          <div className="bot-feed-l">최근 발송 <span className="mono">· {deliveries.length}건</span></div>
          {deliveries.length === 0 && <div className="muted-note">발송 내역이 아직 없어요.</div>}
          {deliveries.map((d) => {
            const cm = channelMeta(d.channel); const al = alerts.find((a) => a.id === d.alert_id);
            return (
              <div key={d.id} className="bot-feed-item">
                <div className="bot-feed-head">
                  <span className="bot-feed-ic"><ChannelIcon kind={d.channel} /></span>
                  <span className="bot-feed-meta mono">{cm.label} · {al?.name ?? "알림"} · {relTime(d.sent_at)}</span>
                  {d.status === "simulated" && <span className="bot-feed-sim" title="채널 미연결 — 보낼 메시지를 기록만">시뮬</span>}
                </div>
                <div className="bot-feed-title">{d.payload.title}</div>
                <div className="bot-feed-body">{d.payload.body}</div>
                <div className="bot-feed-foot">
                  <FreshnessDot f={freshnessFromAsOf(d.payload.as_of)} /> {d.payload.source} · as_of {d.payload.as_of}
                  <button type="button" className="bot-feed-link" onClick={() => openDeep(d.payload.deeplink)}>대시보드에서 보기 ↗</button>
                </div>
              </div>
            );
          })}
        </aside>
      </div>

      {sheet && (
        <AlertSheet channels={channels} onClose={() => setSheet(false)} onChannelsChanged={load}
          onCreated={() => { setSheet(false); load(); }} />
      )}
    </div>
  );
}
