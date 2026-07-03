"use client";

// 알림 설정 시트 — wireframe 05. Opened from the dashboard board-bell (scope 보드 전체) or a widget
// toolbar bell (scope 이 위젯, bound to a pin). Left = config, right = live preview that updates as
// you change trigger / target / channels. Creating an alert fires it once so the first delivery is
// visible (trust: every message carries source · as_of · 대시보드에서 보기 deep link).

import { useMemo, useState } from "react";
import {
  Alert, AlertParams, AlertScope, ChannelKind, ChannelStatus, CHANNELS, SourceSpec,
  TriggerType, TRIGGERS, channelMeta, previewMessage, triggerMeta,
} from "@/lib/alerts";
import { ChannelIcon } from "./ChannelIcon";
import { Button, FreshnessDot, Modal } from "./ui";

export type AlertDraft = {
  scope?: AlertScope;
  board_id?: string | null;
  pin_id?: string | null;
  name?: string;
  trigger_type?: TriggerType;
  params?: AlertParams;
  schedule?: { freq?: string; time?: string };
  source_spec?: SourceSpec | null;
};

const FREQ_LABEL: Record<string, string> = {
  daily: "매일", weekly: "주간", monthly: "월간", interval: "주기", realtime: "실시간", event: "이벤트 시",
};

function defaultSchedule(t: TriggerType): { freq: string; time?: string } {
  if (t === "price_threshold" || t === "filing_news") return { freq: "realtime" };
  if (t === "digest") return { freq: "weekly", time: "08:00" };
  if (t === "rate" || t === "earnings") return { freq: "event" };
  return { freq: "daily", time: "08:00" };
}

function ConnectForm({ kind, busy, onConnect }: { kind: ChannelKind; busy: boolean; onConnect: (config: any) => void }) {
  const [a, setA] = useState(""); const [b, setB] = useState("");
  const isTg = kind === "telegram", isEmail = kind === "email";
  const submit = () => onConnect(isTg ? { bot_token: a.trim(), chat_id: b.trim() } : isEmail ? { webhook_url: a.trim(), to: b.trim() } : { webhook_url: a.trim() });
  return (
    <div className="alert-connect">
      <input className="input alert-connect-in" value={a} disabled={busy}
        placeholder={isTg ? "봇 토큰 (@BotFather)" : "웹훅 URL"} onChange={(e) => setA(e.target.value)} />
      {(isTg || isEmail) && (
        <input className="input alert-connect-in" value={b} disabled={busy}
          placeholder={isTg ? "chat id" : "받는 메일 주소"} onChange={(e) => setB(e.target.value)} />
      )}
      <Button size="sm" disabled={busy || !a.trim()} onClick={submit}>연결</Button>
    </div>
  );
}

export default function AlertSheet({
  initial, channels, boardName, widgetName, onClose, onCreated, onChannelsChanged,
}: {
  initial?: AlertDraft;
  channels: ChannelStatus[];
  boardName?: string;
  widgetName?: string;
  onClose: () => void;
  onCreated: (alert: Alert) => void;
  onChannelsChanged?: () => void;
}) {
  const [scope, setScope] = useState<AlertScope>(initial?.scope ?? "board");
  const [trigger, setTrigger] = useState<TriggerType>(initial?.trigger_type ?? "rate");
  const [target, setTarget] = useState(initial?.params?.target ?? "");
  const [name, setName] = useState(initial?.name ?? "");
  const [sel, setSel] = useState<Set<ChannelKind>>(
    new Set(channels.find((c) => c.connected)?.channel ? [channels.find((c) => c.connected)!.channel] : ["telegram"]),
  );
  const [quiet, setQuiet] = useState(false);
  const [connecting, setConnecting] = useState<ChannelKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const widgetScopeAllowed = !!initial?.pin_id;

  const status = useMemo(() => new Map(channels.map((c) => [c.channel, c])), [channels]);
  const params = (): AlertParams => ({ ...(initial?.params ?? {}), target: target.trim() || initial?.params?.target });
  const sched = initial?.schedule ?? defaultSchedule(trigger);
  const meta = triggerMeta(trigger);
  const toggle = (k: ChannelKind) => setSel((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; });

  async function connect(kind: ChannelKind, config: any) {
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/channels", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channel: kind, config }) });
      if (!r.ok) throw new Error();
      setConnecting(null); setSel((p) => new Set([...p, kind])); onChannelsChanged?.();
    } catch { setErr("채널 연결에 실패했어요."); } finally { setBusy(false); }
  }

  async function create() {
    if (sel.size === 0) { setErr("받을 채널을 하나 이상 선택하세요."); return; }
    setBusy(true); setErr(null);
    const p = params();
    const alertName = name.trim() || `${meta.label} · ${p.target || (scope === "widget" ? widgetName : boardName) || "대시보드"}`;
    try {
      const r = await fetch("/api/alerts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: alertName, scope, board_id: initial?.board_id ?? null,
          pin_id: scope === "widget" ? initial?.pin_id ?? null : null,
          trigger_type: trigger, params: p, schedule: sched, channels: [...sel],
          quiet_hours: quiet ? { start: "22:00", end: "07:00" } : null,
          source_spec: initial?.source_spec ?? null,
        }),
      });
      if (!r.ok) throw new Error();
      const alert = (await r.json()) as Alert;
      try { await fetch(`/api/alerts/${alert.id}/fire`, { method: "POST" }); } catch {}
      onCreated(alert);
    } catch { setErr("알림 생성에 실패했어요."); setBusy(false); }
  }

  const preview = previewMessage(trigger, params(), name, initial?.source_spec);
  const selArr = [...sel];

  return (
    <Modal
      className="alert-sheet"
      onClose={onClose}
      title={<><span className="alert-glyph">🔔</span> 알림 설정</>}
      footer={
        <>
          <span className="muted-note grow">봇 관리에서 언제든 끄거나 채널을 바꿀 수 있어요.</span>
          <Button variant="ghost" onClick={onClose} disabled={busy}>취소</Button>
          <Button onClick={create} disabled={busy || sel.size === 0}>{busy ? "켜는 중…" : `알림 켜기 · ${sel.size}개 채널`}</Button>
        </>
      }
    >
      <div className="alert-sheet-sub">대시보드{boardName ? `: ${boardName}` : ""}</div>

        {/* scope toggle */}
        <div className="alert-scope">
          <button type="button" className={`alert-scope-btn ${scope === "board" ? "on" : ""}`} onClick={() => setScope("board")}>보드 전체 알림</button>
          <button type="button" className={`alert-scope-btn ${scope === "widget" ? "on" : ""}`}
            disabled={!widgetScopeAllowed} onClick={() => widgetScopeAllowed && setScope("widget")}
            title={widgetScopeAllowed ? "" : "위젯에서 열어야 위젯 스코프를 선택할 수 있어요"}>
            이 위젯{widgetName ? ` · ${widgetName}` : ""}
          </button>
        </div>

        <div className="alert-sheet-body">
          {/* LEFT — config */}
          <div className="alert-cfg">
            <div className="alert-sec-l">1 · 무엇을 감시할까요</div>
            <div className="alert-trigger-grid">
              {TRIGGERS.map((t) => (
                <button key={t.type} type="button" className={`alert-trigger ${trigger === t.type ? "on" : ""}`}
                  onClick={() => setTrigger(t.type)}>{t.label}</button>
              ))}
            </div>

            <div className="alert-sec-l">2 · 대상과 조건</div>
            <input className="input" value={target} placeholder="대상 — @관심그룹 · 티커(예: 005930.KS) · 주제"
              onChange={(e) => setTarget(e.target.value)} />
            <div className="alert-cond">{meta.hint} · <span className="mono">{FREQ_LABEL[sched.freq ?? "daily"]}{sched.time ? ` ${sched.time}` : ""}</span></div>

            <div className="alert-sec-l">3 · 어디로 받을까요</div>
            <div className="alert-chan-list">
              {CHANNELS.map((c) => {
                const st = status.get(c.kind); const connected = st?.connected; const on = sel.has(c.kind);
                return (
                  <div key={c.kind} className={`alert-chan ${on ? "on" : ""}`}>
                    <div className="alert-chan-main">
                      <button type="button" className="alert-chan-pick" onClick={() => connected && toggle(c.kind)}
                        disabled={!connected} title={connected ? "" : "먼저 연결하세요"}>
                        <span className="alert-chan-ic"><ChannelIcon kind={c.kind} /></span>
                        <span className="alert-chan-name">{c.label}</span>
                      </button>
                      <div className="alert-chan-right">
                        {connected ? (
                          <>
                            <span className="alert-chan-badge ok"><FreshnessDot f="fresh" /> 연결됨</span>
                            <button type="button" className={`alert-toggle ${on ? "on" : ""}`}
                              onClick={() => toggle(c.kind)} aria-label={`${c.label} ${on ? "끄기" : "켜기"}`} />
                          </>
                        ) : (
                          <button type="button" className="alert-connect-btn"
                            onClick={() => setConnecting(connecting === c.kind ? null : c.kind)}>
                            {connecting === c.kind ? "닫기" : "연결하기 ↗"}
                          </button>
                        )}
                      </div>
                    </div>
                    {!connected && connecting === c.kind && (
                      <ConnectForm kind={c.kind} busy={busy} onConnect={(cfg) => connect(c.kind, cfg)} />
                    )}
                  </div>
                );
              })}
            </div>
            <label className="alert-quiet">
              <input type="checkbox" checked={quiet} onChange={(e) => setQuiet(e.target.checked)} />
              조용한 시간 — 22:00–07:00 묶어서 아침에
            </label>
          </div>

          {/* RIGHT — preview */}
          <div className="alert-prev">
            <div className="alert-prev-l">받게 될 메시지</div>
            {selArr.length === 0 && <div className="alert-prev-empty">받을 채널을 선택하면 미리보기가 표시됩니다.</div>}
            {selArr.map((k) => {
              const cm = channelMeta(k);
              return (
                <div key={k} className="alert-prev-card">
                  <div className="alert-prev-head"><span className="alert-prev-ic"><ChannelIcon kind={k} /></span>{cm.label} · 미리보기</div>
                  <div className="alert-prev-title">{preview.title}</div>
                  <div className="alert-prev-body">{preview.body}</div>
                  <div className="alert-prev-foot"><FreshnessDot f="fresh" /> {preview.source} · as_of {preview.as_of} · 대시보드에서 보기 ↗</div>
                </div>
              );
            })}
            <ul className="alert-prev-sum">
              <li>{meta.hint} · {FREQ_LABEL[sched.freq ?? "daily"]}</li>
              <li>모든 수치에 as_of · 출처 딥링크</li>
            </ul>
          </div>
        </div>

        {err && <div className="err">{err}</div>}
    </Modal>
  );
}
