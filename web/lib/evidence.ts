// LG — pure helpers for the trust layer. Kept DOM-free so vitest covers the logic without
// rendering: trust summary (판정 헤더), evidence partition, and the in-prose numeral
// annotation (LG-4: the ledger lives inside the answer body as highlighted numerals).

import type { Citation, Msg } from "./types";

// One row of the Figure Ledger (mirrors agent-engine audit_ledger output).
export type LedgerRow = {
  raw: string;              // the numeral as written ("391.0B", "8.5%")
  value: number;
  pct?: boolean;
  span?: [number, number];  // offsets into the ORIGINAL markdown content
  citation_idx?: number | null;
  supported: boolean;
};

export function evidenceOf(m: Msg): Citation[] {
  const cites = m.citations ?? [];
  if (m.used && m.used.length) return cites.filter((c) => c.index != null && m.used!.includes(c.index));
  return cites;
}

export function citationByIndex(m: Msg | null, n: number | null | undefined): Citation | null {
  if (!m || n == null) return null;
  return (m.citations ?? []).find((c) => c.index === n) ?? null;
}

export type TrustSummary = {
  checked: number;
  unsupported: number;
  sources: number;
  freshness: { fresh: number; aging: number; stale: number };
  allClear: boolean;        // every checked numeral traced (or nothing to check)
  conceptual: boolean;      // an answer with no claim numerals at all
};

export function trustSummary(msg: Msg | null): TrustSummary {
  const a = msg?.audit ?? null;
  const used = msg ? evidenceOf(msg) : [];
  const freshness = { fresh: 0, aging: 0, stale: 0 };
  for (const c of used) {
    const f = (c.freshness || "").toLowerCase();
    if (f in freshness) freshness[f as keyof typeof freshness]++;
  }
  const checked = a?.checked ?? 0;
  const unsupported = a?.unsupported?.length ?? 0;
  return {
    checked,
    unsupported,
    sources: used.length,
    freshness,
    allClear: unsupported === 0,
    conceptual: checked === 0 && used.length === 0,
  };
}

/** LG-4 — the ledger lives IN the prose now: wrap each audited numeral in a markdown link
 *  `[raw](#num-i)` (i = ledger row index) so the renderer turns it into a highlighted,
 *  hoverable span with the 원자료-대조 popup. Inserts from the END so earlier spans stay
 *  valid; a row whose span no longer matches the text is skipped (defensive — e.g. a
 *  reloaded message whose content drifted). */
export function annotateNumerals(md: string, rows: LedgerRow[] | undefined): string {
  if (!md || !rows?.length) return md;
  const tagged = rows
    .map((r, i) => ({ r, i }))
    .filter(({ r }) => Array.isArray(r.span) && r.span.length === 2)
    .sort((a, b) => b.r.span![0] - a.r.span![0]);
  let out = md;
  for (const { r, i } of tagged) {
    const [s, e] = r.span!;
    const slice = out.slice(s, e);
    if (slice.trim() !== r.raw.trim()) continue;
    out = `${out.slice(0, s)}[${slice}](#num-${i})${out.slice(e)}`;
  }
  return out;
}
