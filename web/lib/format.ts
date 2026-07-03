/**
 * Locale-aware number / currency / volume formatting (FE-02).
 *
 * Shared by the artifact + chart surfaces so big-figure abbreviation (KRW 조/억/만 · USD $T/B/M),
 * ratios, prices, and volumes read the same everywhere. Previously these lived inside ArtifactCard,
 * which coupled TradeChart (it imported `fmtBig`) to a component module; they're plain functions, so
 * they belong in lib.
 */

export function currencyOf(ticker?: string | null): "KRW" | "USD" {
  return /^\d/.test(ticker || "") ? "KRW" : "USD";
}

// Big-number abbreviation so tables/axes stay readable — KRW in 조/억/만, USD in $T/B/M.
export function fmtBig(y: number | null | undefined, currency: "KRW" | "USD" = "USD"): string {
  if (y == null) return "—";
  const a = Math.abs(y), sign = y < 0 ? "-" : "";
  if (currency === "KRW") {
    if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(a >= 1e13 ? 1 : 2)}조`;
    if (a >= 1e8) return `${sign}${Math.round(a / 1e8).toLocaleString()}억`;
    if (a >= 1e4) return `${sign}${Math.round(a / 1e4).toLocaleString()}만`;
    return `${sign}${Math.round(a).toLocaleString()}`;
  }
  if (a >= 1e12) return `${sign}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  return `${sign}$${a.toLocaleString()}`;
}

// price = full number (prices are small); volume = compact count.
export function fmtPrice(y: number | null | undefined) {
  return y == null ? "—" : y.toLocaleString(undefined, { maximumFractionDigits: 2 });
}
export function fmtVol(y: number | null | undefined) {
  if (y == null) return "—";
  const a = Math.abs(y);
  if (a >= 1e9) return (a / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return (a / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return (a / 1e3).toFixed(0) + "K";
  return String(Math.round(a));
}

// financials/series table cell: ratio → %, large currency → abbreviated.
export function fmt(y: number | null | undefined, unit?: string | null, currency: "KRW" | "USD" = "USD") {
  if (y == null) return "—";
  if (unit === "ratio") return (y * 100).toFixed(1) + "%";
  return fmtBig(y, currency);
}
