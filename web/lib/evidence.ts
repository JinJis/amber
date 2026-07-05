// LG (수치 원장) — pure helpers for the evidence panel. Kept DOM-free so vitest covers the
// logic without rendering: trust summary (판정 헤더), ledger row shaping, prose context labels.

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

/** A short human label for a ledger row: the words around its span in the ORIGINAL markdown,
 *  with markdown syntax and anchors stripped. "…의 매출은 391.0B 로 전년…" → "매출은 … 로 전년". */
export function contextLabel(content: string | undefined, row: LedgerRow, width = 26): string {
  if (!content || !row.span) return "";
  const [s, e] = row.span;
  const before = content.slice(Math.max(0, s - width), s);
  const after = content.slice(e, e + width);
  const clean = (t: string) =>
    t.replace(/\[\d+(?:,\s*\d+)*\]/g, " ")      // [n] anchors
      .replace(/[*_`#>|]/g, " ")                 // markdown syntax
      .replace(/\s+/g, " ")
      .trim();
  const b = clean(before);
  const a = clean(after);
  const head = b ? (b.length > width - 6 ? "…" + b.slice(-(width - 6)) : b) : "";
  const tail = a ? (a.length > 12 ? a.slice(0, 12) + "…" : a) : "";
  return [head, "◯", tail].filter(Boolean).join(" ").trim();
}

/** How many ledger rows BEFORE `i` share the same raw string — the nth-occurrence index used
 *  to find the right numeral in the rendered prose when a figure repeats. */
export function occurrenceIndex(rows: LedgerRow[], i: number): number {
  let n = 0;
  for (let j = 0; j < i; j++) if (rows[j].raw === rows[i].raw) n++;
  return n;
}

// --- prose-side highlight (LG-3) — CSS Custom Highlight API, zero DOM mutation -------------
// Finds the nth occurrence of `raw` in the bubble's text nodes and highlights it. Degrades to
// a no-op where the API is unavailable; never touches React-owned DOM.
export function highlightNumeral(bubble: HTMLElement | null, raw: string, nth = 0): boolean {
  try {
    const H = (window as any).Highlight;
    const registry = (CSS as any)?.highlights;
    if (!bubble || !H || !registry) return false;
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
    let seen = 0;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent || "";
      let from = 0;
      let at = text.indexOf(raw, from);
      while (at !== -1) {
        if (seen === nth) {
          const range = document.createRange();
          range.setStart(node, at);
          range.setEnd(node, at + raw.length);
          registry.set("ledger-hot", new H(range));
          (node.parentElement as HTMLElement | null)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return true;
        }
        seen++;
        from = at + raw.length;
        at = text.indexOf(raw, from);
      }
    }
  } catch { /* best-effort only */ }
  return false;
}

export function clearNumeralHighlight(): void {
  try { (CSS as any)?.highlights?.delete("ledger-hot"); } catch { /* no-op */ }
}
