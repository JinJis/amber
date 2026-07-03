// Shared types + catalogs for notification alerts (F3). One source of truth for the alert sheet
// (F5), bot home (F8), and the dashboard board/widget bells — and so the live message preview
// mirrors studio-api's render_message (studio-api/studioapi/alerts.py).

export type TriggerType =
  | "earnings" | "rate" | "macro_indicator" | "filing_news" | "price_threshold" | "digest";
export type ChannelKind = "telegram" | "slack" | "kakao" | "email";
export type AlertScope = "board" | "widget";

export type AlertParams = { target?: string; threshold?: string; level?: string; group?: string; ticker?: string; [k: string]: any };
export type AlertSchedule = { freq?: "daily" | "weekly" | "monthly" | "interval" | "realtime" | "event"; time?: string; every_minutes?: number };
export type SourceSpec = { tool?: string; args?: any; source?: string; deeplink?: string };

export type Alert = {
  id: string;
  name: string;
  scope: AlertScope;
  board_id: string | null;
  pin_id: string | null;
  trigger_type: TriggerType;
  trigger_label: string;
  params: AlertParams;
  schedule: AlertSchedule;
  channels: ChannelKind[];
  quiet_hours: { start: string; end: string } | null;
  status: "active" | "paused";
  source_spec: SourceSpec | null;
  last_fired_at: string | null;
  next_fire_at: string | null;
  created_at: string | null;
};

export type DeliveryPayload = { title: string; body: string; as_of: string; source: string; deeplink: string };
export type Delivery = {
  id: string;
  alert_id: string;
  channel: ChannelKind;
  status: "sent" | "simulated" | "failed";
  payload: DeliveryPayload;
  sent_at: string | null;
};

export type ChannelStatus = { channel: ChannelKind; connected: boolean; verified: boolean; id: string | null };

// Trigger catalog — labels + upstream source line, mirroring _TRIGGER_META in studio-api.
export const TRIGGERS: { type: TriggerType; label: string; source: string; hint: string }[] = [
  { type: "rate", label: "미 연준/한은 금리", source: "FOMC · CME FedWatch · 한은", hint: "발표 D-1 + 직후 요약" },
  { type: "earnings", label: "실적 발표", source: "DART · SEC EDGAR", hint: "공시 접수 시" },
  { type: "macro_indicator", label: "경제지표(CPI·고용)", source: "FRED · BLS", hint: "지표 발표 시" },
  { type: "filing_news", label: "공시·뉴스", source: "DART · SEC EDGAR", hint: "새 공시/뉴스" },
  { type: "price_threshold", label: "가격·밸류 임계치", source: "Yahoo Finance · KIS", hint: "임계치 도달 시" },
  { type: "digest", label: "정기 요약", source: "ValueGraph", hint: "주기적 요약" },
];

export const CHANNELS: { kind: ChannelKind; label: string; icon: string }[] = [
  { kind: "telegram", label: "Telegram", icon: "✈" },
  { kind: "slack", label: "Slack", icon: "#" },
  { kind: "kakao", label: "카카오톡", icon: "K" },
  { kind: "email", label: "이메일", icon: "✉" },
];

export const triggerMeta = (t: TriggerType) => TRIGGERS.find((x) => x.type === t) ?? TRIGGERS[0];
export const channelMeta = (k: ChannelKind) => CHANNELS.find((x) => x.kind === k) ?? CHANNELS[0];

export function targetLabel(params: AlertParams): string {
  if (params.target) return params.target;
  if (params.ticker) return params.ticker;
  if (params.group) return "@" + params.group.replace(/^@/, "");
  return "관심 대상";
}

// Mirror of studio-api render_message — the LIVE preview before an alert exists.
export function previewMessage(trigger: TriggerType, params: AlertParams, name?: string, spec?: SourceSpec | null): DeliveryPayload {
  const meta = triggerMeta(trigger);
  const target = targetLabel(params);
  let title = `🔔 ${target}`, body = "출처 기반 사실 업데이트를 보냅니다.";
  if (trigger === "rate") { title = `🔔 ${target} — 금리 발표 모니터`; body = "예정된 금리 결정과 직전/직후 사실을 추적합니다. 점도표·성명서 원문은 대시보드에서."; }
  else if (trigger === "earnings") { title = `🔔 ${target} 실적 — 공시 추적`; body = "실적 발표가 가까워지면 접수된 공시의 사실을 정리해 보냅니다."; }
  else if (trigger === "macro_indicator") { title = `🔔 ${target} — 경제지표 업데이트`; body = "발표된 지표 수치를 원 출처(FRED·BLS) 그대로 전달합니다."; }
  else if (trigger === "filing_news") { title = `🔔 ${target} — 새 공시·뉴스`; body = "새 공시/뉴스 제목과 원문 링크를 그대로 전달합니다."; }
  else if (trigger === "price_threshold") { title = `🔔 ${target} — 가격·밸류 임계치`; body = `설정한 임계치(${params.threshold ?? "—"}) 도달 여부를 감시합니다.`; }
  else { title = `🔔 ${name || target} — 정기 요약`; body = `${target} 관련 사실을 주기적으로 정리해 보냅니다.`; }
  const today = new Date().toISOString().slice(0, 10);
  return { title, body, source: spec?.source ?? meta.source, as_of: today, deeplink: spec?.deeplink ?? "/" };
}

export function freshnessFromAsOf(as_of?: string): string {
  if (!as_of) return "gap";
  const days = (Date.now() - new Date(as_of).getTime()) / 86_400_000;
  if (isNaN(days)) return "gap";
  return days < 30 ? "fresh" : days < 90 ? "aging" : "stale";
}

// --- periodicity (cadence) -------------------------------------------------
// Mirrors datasets Cadence: intraday|daily|event|scheduled|streaming = periodic; one_shot = not.
// `cadence` rides on every pin spec (stamped from the catalog), so the dashboard can gate the
// notification bot — only a periodic datasource can carry one. Source of truth is the catalog.
export const CADENCE_LABEL: Record<string, string> = {
  intraday: "실시간", daily: "일간", event: "공시·이벤트", scheduled: "정기 발표",
  streaming: "뉴스 피드", one_shot: "단발성",
};
export const cadenceLabel = (c?: string | null): string => (c ? CADENCE_LABEL[c] ?? c : "");

// A datasource (and so a pinned widget) is alertable iff it recurs. Unknown cadence (e.g. a pin
// made before this concept existed) is treated as one-shot → no bell, until it refreshes.
export function isPeriodic(spec: any): boolean {
  const c = spec?.cadence as string | undefined;
  return !!c && c !== "one_shot";
}

// Pick the natural alert trigger from DECLARED metadata (category + cadence) — not name-guessing.
// Replaces the old triggerForSpec regex (keeps invariant #9: classification is data, not logic).
export function triggerFromMeta(spec: any): TriggerType {
  const cadence = spec?.cadence as string | undefined;
  const category = spec?.category as string | undefined;
  if (!cadence || cadence === "one_shot") return "digest";  // not alertable; harmless default
  if (category === "market") return "price_threshold";
  if (category === "news" || cadence === "streaming") return "filing_news";
  if (category === "macro" || cadence === "scheduled") return "macro_indicator";
  if (category === "fundamentals" || category === "valuation") return "earnings";
  if (category === "filings" || category === "gurus") return "filing_news";
  if (cadence === "daily" || cadence === "intraday") return "price_threshold";
  if (cadence === "event") return "earnings";
  return "digest";
}
