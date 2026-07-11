// Recommended @관심 group presets — shared by Onboarding (step 2) and the Desk Home
// watchlist-nudge quick-add (M-DESK), so both create identical groups.

export type Preset = {
  id: string;
  name: string;
  tpl: string; // dashboard template id (used only when FEATURE_DASHBOARD is on)
  items: { market: string; ticker: string; name?: string }[];
};

export const PRESETS: Preset[] = [
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
