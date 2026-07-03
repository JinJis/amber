// Feature flags — toggle whole product surfaces on/off from the (server-side) env. Read once in the
// server component (page.tsx) and passed to the client tree via FeaturesProvider (no flash). Default
// ON when unset; a value of false/0/off/no disables. Restart the web service to apply a change.

export type Features = {
  dashboard: boolean;  // 대시보드 (Board) tab + pin-to-dashboard actions
  alerts: boolean;     // 알림봇 (notifications) tab + alert bells
};

const OFF = new Set(["false", "0", "off", "no", "disabled"]);

function flag(v: string | undefined, dflt = true): boolean {
  if (v == null || v.trim() === "") return dflt;
  return !OFF.has(v.trim().toLowerCase());
}

/** Read the feature flags from server env (call from a server component only). */
export function getFeatures(): Features {
  return {
    dashboard: flag(process.env.FEATURE_DASHBOARD),
    alerts: flag(process.env.FEATURE_ALERTS),
  };
}
