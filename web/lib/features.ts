// Feature flags — toggle whole product surfaces on/off from the (server-side) env. Read once in the
// server component (page.tsx) and passed to the client tree via FeaturesProvider (no flash). The
// product is CHAT-FIRST: both surfaces default OFF (FLAG-1) — set FEATURE_DASHBOARD/FEATURE_ALERTS to
// true/1/on to bring them back. A value of false/0/off/no keeps them hidden. Restart web to apply.

export type Features = {
  dashboard: boolean;  // 대시보드 (Board) tab + pin-to-dashboard actions
  alerts: boolean;     // 알림봇 (notifications) tab + alert bells + the studio-api scheduler
};

const OFF = new Set(["false", "0", "off", "no", "disabled"]);
const ON = new Set(["true", "1", "on", "yes", "enabled"]);

function flag(v: string | undefined, dflt: boolean): boolean {
  if (v == null || v.trim() === "") return dflt;
  const t = v.trim().toLowerCase();
  if (OFF.has(t)) return false;
  if (ON.has(t)) return true;
  return dflt;
}

/** Read the feature flags from server env (call from a server component only). Chat-first → default OFF. */
export function getFeatures(): Features {
  return {
    dashboard: flag(process.env.FEATURE_DASHBOARD, false),
    alerts: flag(process.env.FEATURE_ALERTS, false),
  };
}
