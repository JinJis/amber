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

// --- the OG preview card (SH-OG) ------------------------------------------------------------
// The link-unfurl image. Fixed 1200×630 (1.91:1 — the ratio X/Threads/Telegram/KakaoTalk crop to,
// so nothing breaks), with MEASURE-BASED layout (real text width, never char-count) so the title
// and lead wrap cleanly, ellipsize on overflow, and never spill past the footer. Editorial mono
// aesthetic — a clean, sourced "receipt" that stands out in a feed of screenshots.
export const OG = { W: 1200, H: 630 };

export type OgCard = {
  title: string;             // the question / artifact title (the hook)
  lead: string;              // plain-text body excerpt
  sources: string[];         // distinct source names (SEC EDGAR · DART …)
  sourceCount: number;       // total cited sources (for the "외 N곳" overflow)
  as_of?: string | null;
  history?: boolean;         // history kinds carry the descriptive label
};

/** Build the OG card model from a whole-answer share (pure — unit-tested). */
export function ogCardForAnswer(a: {
  title: string; content: string; citations?: { source?: string; as_of?: string; used?: boolean; index?: number }[];
  artifacts?: Artifact[];
}): OgCard {
  const cits = (a.citations ?? []).filter((c) => c.used || c.index != null);
  const sources = [...new Set(cits.map((c) => c.source).filter(Boolean) as string[])];
  const asOf = cits.map((c) => c.as_of).filter(Boolean).sort().slice(-1)[0] ?? null;
  return {
    title: a.title || "ValueGraph 리서치",
    lead: plainText(a.content),
    sources,
    sourceCount: sources.length,
    as_of: asOf,
    history: (a.artifacts ?? []).some(isHistoryKind),
  };
}

/** Build the OG card model from a single-artifact share (pure — unit-tested). */
export function ogCardForArtifact(a: Artifact): OgCard {
  return {
    title: a.title || "ValueGraph 자료",
    lead: shareCardLines(a, 6).join("  ·  "),
    sources: a.source ? [a.source] : [],
    sourceCount: a.source ? 1 : 0,
    as_of: a.as_of ?? null,
    history: isHistoryKind(a),
  };
}

// --- the canvas draw (thin; not unit-tested — jsdom has no real 2D context) ----------------
const INK = "#17181B", SUB = "#55565C", MUTED = "#8C8C93", LINE = "#E4E4E8", BG = "#FFFFFF";

/** Greedy word-wrap by MEASURED width; breaks over-long tokens (URLs / space-less CJK runs) by
 *  character, and ellipsizes the last line when the text overflows `maxLines`. Canvas-only. */
function wrapMeasured(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines: number): string[] {
  const lines: string[] = [];
  let line = "";
  const fits = (s: string) => ctx.measureText(s).width <= maxWidth;
  const words = (text || "").split(/\s+/).filter(Boolean);
  let overflow = false;
  for (let wi = 0; wi < words.length; wi++) {
    const word = words[wi];
    if (!fits(word)) {                 // a single token too wide → break by character
      if (line) { lines.push(line); line = ""; }
      let chunk = "";
      for (const ch of word) {
        if (!fits(chunk + ch) && chunk) { lines.push(chunk); chunk = ch; } else chunk += ch;
        if (lines.length >= maxLines) { overflow = true; break; }
      }
      if (lines.length >= maxLines) { line = line || chunk; break; }
      line = chunk;
    } else if (!line) {
      line = word;
    } else if (fits(line + " " + word)) {
      line += " " + word;
    } else {
      lines.push(line); line = word;
      if (lines.length >= maxLines) { overflow = wi < words.length; break; }
    }
  }
  if (line && lines.length < maxLines) lines.push(line);
  else if (line) overflow = true;
  if (overflow && lines.length) {      // ellipsize the last visible line
    let last = lines[Math.min(maxLines, lines.length) - 1];
    while (last.length && !fits(last + "…")) last = last.slice(0, -1);
    lines[Math.min(maxLines, lines.length) - 1] = last.replace(/[\s·]+$/, "") + "…";
  }
  return lines.slice(0, maxLines);
}

const SANS = `"Space Grotesk", "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", ui-sans-serif, system-ui, sans-serif`;
const MONO = `"Space Mono", ui-monospace, monospace`;

/** Render the OG preview PNG for a share. One renderer for answers + artifacts (via OgCard). */
export async function renderOgCard(card: OgCard, shortLink: string): Promise<Blob> {
  const { W, H } = OG;
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  const PX = 76, PY = 60, CW = W - PX * 2;
  ctx.textBaseline = "top";

  // surface + a crisp left accent rail (editorial pop against the grayscale brand)
  ctx.fillStyle = BG; ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = INK; ctx.fillRect(0, 0, 12, H);

  // header: brand mark (left) + trust chip (right)
  const brandY = PY;
  ctx.fillStyle = INK; ctx.fillRect(PX, brandY + 2, 22, 22);
  ctx.font = `700 26px ${SANS}`; ctx.fillStyle = INK;
  ctx.fillText("ValueGraph", PX + 34, brandY);
  const chip = "✓ 출처와 함께";
  ctx.font = `500 22px ${MONO}`;
  const cw = ctx.measureText(chip).width, chipX = W - PX - cw - 28, chipY = brandY - 4;
  ctx.strokeStyle = LINE; ctx.lineWidth = 1.5;
  roundRect(ctx, chipX, chipY, cw + 28, 38, 19); ctx.stroke();
  ctx.fillStyle = SUB; ctx.fillText(chip, chipX + 14, chipY + 7);

  // footer strip geometry (reserve space so the lead never collides with it)
  const stripH = card.history ? 132 : 92;
  const stripTop = H - PY - stripH;

  // title — the hook. Bold, up to 3 lines.
  const titleTop = brandY + 66;
  ctx.font = `700 54px ${SANS}`; ctx.fillStyle = INK;
  const titleLines = wrapMeasured(ctx, card.title, CW, 3);
  const titleLH = 66;
  titleLines.forEach((ln, i) => ctx.fillText(ln, PX, titleTop + i * titleLH));
  const titleBottom = titleTop + titleLines.length * titleLH;

  // lead — fills the space between the title and the footer, ellipsized.
  const leadTop = titleBottom + 22;
  ctx.font = `400 29px ${SANS}`; ctx.fillStyle = SUB;
  const leadLH = 42;
  const leadMax = Math.max(0, Math.floor((stripTop - 18 - leadTop) / leadLH));
  if (leadMax > 0 && card.lead) {
    wrapMeasured(ctx, card.lead, CW, Math.min(leadMax, 4))
      .forEach((ln, i) => ctx.fillText(ln, PX, leadTop + i * leadLH));
  }

  // footer: divider → (history label) → sources + as_of (left) · short link (right)
  ctx.strokeStyle = LINE; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(PX, stripTop); ctx.lineTo(W - PX, stripTop); ctx.stroke();
  let fy = stripTop + 20;
  if (card.history) {
    ctx.font = `700 22px ${MONO}`; ctx.fillStyle = INK;
    ctx.fillText("⏳ 과거 기록 · 전망 아님", PX, fy); fy += 34;
  }
  const srcHead = card.sources.slice(0, 3).join(" · ") || "출처 포함";
  const extra = card.sourceCount > 3 ? ` 외 ${card.sourceCount - 3}곳` : "";
  const srcLine = `출처 ${srcHead}${extra}${card.as_of ? ` · ${card.as_of}` : ""}`;
  ctx.font = `400 24px ${MONO}`; ctx.fillStyle = MUTED;
  ctx.fillText(wrapMeasured(ctx, srcLine, CW - 260, 1)[0] ?? srcLine, PX, fy);
  ctx.font = `700 24px ${MONO}`; ctx.fillStyle = INK;
  const link = shortLink || "valuegraph";
  const lw = ctx.measureText(link).width;
  ctx.fillText(link, W - PX - lw, fy);

  return await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/png"));
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
