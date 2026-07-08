// SH-2b — client-side share-card image: compose an artifact into a grayscale card PNG at an
// aspect preset, with the provenance strip BAKED IN (non-removable). Canvas 2D only (no libs,
// no data egress) — same approach as the TradeChart PNG export. Numbers are already audit-gated
// upstream (a blocked share never reaches here), so the image can't carry an unsupported figure.
//
// Pure helpers (presets, line extraction, wrapping) are exported for unit tests; the canvas draw
// is a thin shell around them.

import type { Artifact } from "./types";

export type PresetKey = "1:1" | "4:5" | "16:9";

export const PRESETS: Record<PresetKey, { w: number; h: number; label: string }> = {
  "1:1": { w: 1080, h: 1080, label: "정사각 (카톡·Threads)" },
  "4:5": { w: 1080, h: 1350, label: "세로 (Threads)" },
  "16:9": { w: 1200, h: 675, label: "가로 (X·블로그)" },
};

// history kinds must always carry the descriptive label (ROADMAP §2 invariant).
export function isHistoryKind(a: Artifact): boolean {
  return a.kind === "base_rates" || a.kind === "analogue";
}

/** The card body as a list of lines — a compact, kind-aware summary. Pure (unit-tested). */
export function shareCardLines(a: Artifact, max = 8): string[] {
  const out: string[] = [];
  const push = (s: string | null | undefined) => { if (s && out.length < max) out.push(String(s)); };

  if (a.kind === "note" && Array.isArray(a.blocks)) {
    for (const b of a.blocks as { kind?: string; note?: string | null; payload?: Record<string, unknown> }[]) {
      if (b.kind === "text") push(String((b.payload?.md ?? "")).split("\n")[0]);
      else {
        const p = b.payload ?? {};
        const head = String(p.title ?? p.raw ?? p.source ?? "근거");
        push(`· ${head}${b.note ? ` — ${b.note}` : ""}`);
      }
    }
    return out.slice(0, max);
  }
  if (a.kind === "quote" && a.passage) {
    push(`“${a.passage}”`);
    if (a.doc_title) push(`— ${a.doc_title}`);
    return out.slice(0, max);
  }
  if (a.kind === "base_rates" && a.base_rates) {
    const d = a.base_rates;
    push(`사건: ${d.event?.text ?? ""} · n=${d.n}`);
    for (const h of d.horizons ?? []) {
      const md = h.median == null ? "—" : `${h.median > 0 ? "+" : ""}${h.median.toFixed(1)}%`;
      const ps = h.pos_share == null ? "—" : `${h.pos_share}%`;
      push(`${h.h}거래일 후 · 중앙값 ${md} · 상승마감 ${ps}`);
    }
    return out.slice(0, max);
  }
  if ((a.table?.length ?? 0) > 0) {
    for (const row of a.table!) push(row.join("  ·  "));
    return out.slice(0, max);
  }
  // timeseries / compare / kpi without a matrix → last value per series
  for (const s of a.series ?? []) {
    const pts = s.points ?? [];
    const last = pts[pts.length - 1];
    if (last) push(`${s.label}: ${last.y ?? "—"}${last.x ? ` (${last.x})` : ""}`);
  }
  if (out.length === 0) push(a.source || "");
  return out.slice(0, max);
}

/** Greedy word-wrap to a max character width (pure — the canvas uses measured width instead,
 *  but the char cap keeps the tested layout deterministic and bounds runaway lines). */
export function wrap(text: string, maxChars: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    if (!cur) { cur = w; continue; }
    if ((cur + " " + w).length <= maxChars) cur += " " + w;
    else { lines.push(cur); cur = w; }
  }
  if (cur) lines.push(cur);
  return lines;
}

export function provenanceStrip(a: Artifact, shortLink: string): { label: string | null; source: string; brand: string } {
  return {
    label: isHistoryKind(a) ? "과거 기록 · 전망 아님" : null,
    source: `출처 ${a.source || "—"}${a.as_of ? ` · as of ${a.as_of}` : ""}`,
    brand: `ValueGraph · ${shortLink}`,
  };
}

// --- answer share cards (SH-ANSWER) ---------------------------------------------------------
// A whole-answer share bakes the answer's LEAD prose (markdown/marker-stripped) into the card,
// with a provenance strip that counts the cited sources instead of one artifact's source.
export type AnswerCard = { title: string; content: string; sourceCount: number; as_of?: string | null };

/** Strip markdown syntax + {{figure:N}} + [n] markers → plain prose for the card body. Pure. */
export function plainText(md: string): string {
  return (md || "")
    .replace(/\{\{figure:\d+\}\}/g, " ")
    .replace(/\[(\d{1,3})\]/g, "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_`~>#|]/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/** The answer body as lead sentences (bounded). Pure (unit-tested). */
export function answerCardLines(content: string, max = 10): string[] {
  const flat = plainText(content);
  if (!flat) return [];
  // split on sentence-ish boundaries so wrapping breaks read naturally
  const sentences = flat.split(/(?<=[.。!?])\s+/).filter(Boolean);
  return sentences.slice(0, max);
}

// --- the canvas draw (thin; not unit-tested — jsdom has no real 2D context) ----------------
const INK = "#1A1B1E", MUTED = "#86868C", LINE = "#D8D8DC", PANEL = "#ffffff", BG = "#F4F4F6";

/** The shared card canvas: title (≤2 lines) + body lines (height-capped) + baked provenance
 *  strip. `linesFor(maxLines)` supplies the body so artifacts and answers share one draw. */
async function drawCard(
  preset: PresetKey, title: string,
  linesFor: (maxLines: number) => string[],
  strip: { label: string | null; source: string; brand: string },
): Promise<Blob> {
  const { w, h } = PRESETS[preset];
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  const M = Math.round(w * 0.06);            // margin
  const stripH = Math.round(h * 0.16);
  const bodyTop = M + Math.round(h * 0.11);

  // surface
  ctx.fillStyle = BG; ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = PANEL; ctx.fillRect(M / 2, M / 2, w - M, h - M);
  ctx.strokeStyle = LINE; ctx.lineWidth = 2; ctx.strokeRect(M / 2, M / 2, w - M, h - M);

  ctx.textBaseline = "top";
  // title
  ctx.fillStyle = INK;
  const titleSize = Math.round(w * 0.042);
  ctx.font = `600 ${titleSize}px "Space Grotesk", ui-sans-serif, system-ui, sans-serif`;
  const titleLines = wrap(title || "ValueGraph 자료", 28).slice(0, 2);
  titleLines.forEach((ln, i) => ctx.fillText(ln, M, M + i * titleSize * 1.25));

  // body lines (mono for the figures)
  const bodySize = Math.round(w * 0.028);
  const lh = bodySize * 1.7;
  const avail = h - stripH - bodyTop - M;
  const maxLines = Math.max(3, Math.floor(avail / lh));
  const lines = linesFor(maxLines);
  let y = bodyTop;
  for (const raw of lines) {
    for (const ln of wrap(raw, Math.floor((w - 2 * M) / (bodySize * 0.55)))) {
      if (y + lh > h - stripH - M) break;
      ctx.fillStyle = INK;
      ctx.font = `${bodySize}px "Space Mono", ui-monospace, monospace`;
      ctx.fillText(ln, M, y);
      y += lh;
    }
  }

  // provenance strip (baked in, non-removable)
  const sy = h - stripH;
  ctx.strokeStyle = LINE; ctx.beginPath(); ctx.moveTo(M, sy); ctx.lineTo(w - M, sy); ctx.stroke();
  let ly = sy + Math.round(stripH * 0.12);
  const small = Math.round(w * 0.022);
  if (strip.label) {
    ctx.fillStyle = INK;
    ctx.font = `600 ${small}px "Space Mono", ui-monospace, monospace`;
    ctx.fillText(`⏳ ${strip.label}`, M, ly); ly += small * 1.7;
  }
  ctx.fillStyle = MUTED;
  ctx.font = `${small}px "Space Mono", ui-monospace, monospace`;
  ctx.fillText(strip.source, M, ly); ly += small * 1.7;
  ctx.fillStyle = INK;
  ctx.fillText(strip.brand, M, ly);

  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/png"));
}

export function renderShareCard(a: Artifact, preset: PresetKey, shortLink: string): Promise<Blob> {
  return drawCard(preset, a.title || "ValueGraph 자료",
    (n) => shareCardLines(a, n), provenanceStrip(a, shortLink));
}

export function renderAnswerCard(card: AnswerCard, preset: PresetKey, shortLink: string): Promise<Blob> {
  const strip = {
    label: null as string | null,
    source: `출처 ${card.sourceCount}곳${card.as_of ? ` · as of ${card.as_of}` : ""}`,
    brand: `ValueGraph · ${shortLink}`,
  };
  return drawCard(preset, card.title, (n) => answerCardLines(card.content, n), strip);
}
