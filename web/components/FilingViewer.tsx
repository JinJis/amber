"use client";

// In-app source viewer — renders the ORIGINAL source as the real document and highlights the exact
// cited element, then lets the user scroll/zoom freely. Two source kinds, one mechanism:
//   • a filing  → US SEC iXBRL primary doc / KR OpenDART document.xml (via /api/evidence/html)
//   • any other page with a source URL (BLS/DBnomics/FRED series page, a news article, …)
//     → fetched + sanitized server-side same-origin (via /api/evidence/url)
// Either way the HTML is sanitized server-side (scripts stripped + strict CSP → no egress); we drop
// it into a sandboxed iframe (allow-same-origin, NO allow-scripts) so nothing in it runs, while the
// parent reaches into the same-origin document to highlight + scroll.
// No localStorage/sessionStorage. 204 → caller shows the external "원문 보기" link.

import { useEffect, useRef, useState } from "react";
import { Citation } from "./SourceCard";

type Target = { concept?: string; value?: string; text?: string };

// Map the agent's gateway-relative /evidence?… params → the HTML viewer route the browser fetches.
export function evidenceHtmlSrc(c: Citation): string | null {
  const q = c.evidence_image_url?.split("?")[1];
  if (!q) return null;
  const p = new URLSearchParams(q);
  const market = p.get("market"), accession = p.get("accession");
  if (!market || !accession) return null;
  const out = new URLSearchParams({ market, accession });
  // US needs the CIK to resolve the primary doc; figure citations carry it, filing-prose (RAG)
  // citations don't — but their canonical SEC url embeds it (/edgar/data/{cik}/…).
  let cik = p.get("cik");
  if (!cik && market.toUpperCase() === "US" && c.url) {
    const m = c.url.match(/edgar\/data\/(\d+)/);
    if (m) cik = m[1];
  }
  if (cik) out.set("cik", cik);
  return `/api/evidence/html?${out.toString()}`;
}

// A non-filing citation that carries an external http(s) source page (macro series page, news
// article, …) → the in-app sanitized-source route. The data plane fetches it SSRF-safe; 204 when it
// can't be shown (the caller then degrades to the external link).
export function sourceUrlSrc(c: Citation): string | null {
  if (!c.url || !/^https?:\/\//i.test(c.url)) return null;
  return `/api/evidence/url?u=${encodeURIComponent(c.url)}`;
}

// The viewer's src: the filing HTML if this is a filing-backed citation, else the external source
// page if one is available. Either renders the REAL source in-app; null → no in-app preview.
export function viewerSrc(c: Citation): string | null {
  return evidenceHtmlSrc(c) || sourceUrlSrc(c);
}

function targetOf(c: Citation): Target {
  const p = new URLSearchParams(c.evidence_image_url?.split("?")[1] || "");
  return {
    concept: p.get("concept") || undefined,
    value: p.get("value") || undefined,
    text: p.get("text") || c.snippet || undefined,
  };
}

// the value formatted with thousands commas at each plausible statement scale (ones/천/백만/억)
function valueCandidates(value: string): string[] {
  const v = Math.abs(parseFloat(value));
  if (!isFinite(v) || v === 0) return [];
  const out: string[] = [];
  for (const scale of [1, 1_000, 1_000_000, 100_000_000]) {
    const q = v / scale;
    if (q >= 1 && Math.abs(q - Math.round(q)) < 0.5) {
      const s = Math.round(q).toLocaleString("en-US");
      if (!out.includes(s)) out.push(s);
    }
  }
  return out;
}

// Number-like tokens in a snippet (e.g. "323.048", "3.2%", "1,234.5", "-0.4") — used to highlight
// the exact figure on a non-filing source page (macro series tables, where a us-gaap concept and
// statement-scale rounding don't apply, so the literal value as printed is the best anchor).
function numberTokens(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(/-?\d[\d,]*(?:\.\d+)?%?/g)) {
    const tok = m[0];
    // skip trivially-short tokens (a lone "1"/"20" matches everywhere); keep decimals/percents/long
    if (tok.length >= 4 || tok.includes(".") || tok.includes("%")) {
      if (!out.includes(tok)) out.push(tok);
    }
  }
  return out;
}

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

// Find the tightest element whose text contains `needle` (handles text split across <span>s,
// since the enclosing <p>/<td> still contains the whole phrase).
function findByText(doc: Document, needle: string, minLen = 6): Element | null {
  const n = norm(needle);
  if (n.length < minLen || !doc.body) return null;
  let best: Element | null = null;
  doc.body.querySelectorAll("*").forEach((el) => {
    if (norm(el.textContent || "").includes(n)) {
      if (!best || (el.textContent || "").length < (best!.textContent || "").length) best = el;
    }
  });
  return best;
}

// Locate + highlight the cited element. Returns true if something was highlighted.
function highlight(doc: Document, t: Target): boolean {
  const style = doc.createElement("style");
  style.textContent =
    ".vg-hl{background:#fde68a !important;box-shadow:0 0 0 2px #f59e0b;border-radius:2px;scroll-margin-top:30vh;}";
  (doc.head || doc.body)?.appendChild(style);

  const mark = (el: Element | null): boolean => {
    if (!el) return false;
    el.classList.add("vg-hl");
    el.scrollIntoView({ block: "center" });
    return true;
  };

  // 1) US iXBRL — target the element tagged with the cited us-gaap concept (semantic, exact)
  if (t.concept) {
    const tags = t.concept.split(",").map((s) => s.split(":").pop()!.trim().toLowerCase()).filter(Boolean);
    const named = Array.from(doc.querySelectorAll("[name]"));
    const cands = t.value ? valueCandidates(t.value).map((s) => s.replace(/\s/g, "")) : [];
    for (const tag of tags) {
      const ms = named.filter((el) => (el.getAttribute("name") || "").split(":").pop()?.toLowerCase() === tag);
      if (!ms.length) continue;
      const byVal = cands.length
        ? ms.find((m) => cands.some((c) => (m.textContent || "").replace(/\s/g, "").includes(c)))
        : null;
      if (mark(byVal || ms[0])) return true;
    }
  }
  // 2) Text passage — find the leading slice of the cited snippet in the document body
  if (t.text) {
    const words = t.text.split(/\s+/).filter(Boolean);
    for (const n of [10, 8, 6, 4]) {
      const el = findByText(doc, words.slice(0, n).join(" "));
      if (el && mark(el)) return true;
    }
  }
  // 3) Exact figure from the snippet — on a non-filing source page (macro tables, news), the value
  // is printed literally (e.g. "323.048", "3.2%"); match it verbatim before falling back to scales.
  if (t.text) {
    for (const tok of numberTokens(t.text)) {
      const el = findByText(doc, tok, 3);
      if (el && mark(el)) return true;
    }
  }
  // 4) Value alone — the formatted number as a last resort
  if (t.value) {
    for (const cand of valueCandidates(t.value)) {
      const el = findByText(doc, cand);
      if (el && mark(el)) return true;
    }
  }
  return false;
}

export function FilingViewer({ c }: { c: Citation }) {
  const filingSrc = evidenceHtmlSrc(c);
  const src = filingSrc || sourceUrlSrc(c);   // a filing, or any external source page
  const isFiling = !!filingSrc;
  const icon = isFiling ? "📄" : "🌐";          // filing vs an external web/data source page
  const [html, setHtml] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "none">(src ? "loading" : "none");
  const [hit, setHit] = useState<boolean | null>(null);
  // KR (OpenDART) filing HTML uses large base fonts → it renders oversized at 100%; start a bit
  // smaller for readability. US (SEC iXBRL) and external pages read fine at 100%. User can still zoom.
  const market = new URLSearchParams(c.evidence_image_url?.split("?")[1] || "").get("market")?.toUpperCase();
  const [zoom, setZoom] = useState(market === "KR" ? 0.8 : 1);
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    if (!src) return;
    let alive = true;
    (async () => {
      try {
        const r = await fetch(src);
        if (!alive) return;
        if (r.status !== 200) return setState("none");
        const text = await r.text();
        if (!alive) return;
        setHtml(text);
        setState("ready");
      } catch {
        if (alive) setState("none");
      }
    })();
    return () => { alive = false; };
  }, [src]);

  const onLoad = () => {
    const doc = frameRef.current?.contentDocument;
    if (!doc) return;
    try { setHit(highlight(doc, targetOf(c))); } catch { setHit(false); }
  };

  useEffect(() => {
    const doc = frameRef.current?.contentDocument;
    if (doc?.body) (doc.body.style as unknown as { zoom: string }).zoom = String(zoom);
  }, [zoom, html]);

  if (state === "none") {
    return (
      <div className="fv-empty">
        <div className="fv-empty-quote">{c.snippet ? `“${c.snippet}”` : "원문 미리보기를 불러올 수 없습니다."}</div>
        {c.url ? (
          <a className="fv-empty-link" href={c.url} target="_blank" rel="noreferrer">원문 보기 ↗</a>
        ) : null}
      </div>
    );
  }

  return (
    <div className="fv">
      <div className="fv-bar mono">
        <span className="fv-status">
          {state === "loading" ? "원문 불러오는 중…" : hit ? `${icon} 원문 · 인용 부분 하이라이트됨` : `${icon} 원문`}
        </span>
        <span className="fv-zoom">
          <button onClick={() => setZoom((z) => Math.max(0.6, +(z - 0.1).toFixed(2)))} aria-label="축소">－</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom((z) => Math.min(2, +(z + 0.1).toFixed(2)))} aria-label="확대">＋</button>
        </span>
        {/* the external "원문 보기 ↗" action lives once, in the SourceViewer side panel (sv-ctx-actions) */}
      </div>
      {state === "loading" ? (
        <div className="fv-loading"><span className="fv-spinner" /> 원문 불러오는 중…</div>
      ) : (
        <iframe
          ref={frameRef}
          className="fv-frame"
          title="원문"
          sandbox="allow-same-origin"
          srcDoc={html ?? ""}
          onLoad={onLoad}
        />
      )}
    </div>
  );
}
