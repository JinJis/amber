"use client";

// In-app 8-K presentation-deck viewer — renders the REAL slide PDF (the same deck filed on EDGAR,
// parsed by Document AI into the cited RAG chunk) with pdf.js, then highlights the cited passage in
// the text layer and scrolls to it. The deck PDF is fetched same-origin from /api/evidence/deck
// (→ studio-api → gateway → datasets cache). Slides render to <canvas>; a transparent text layer on
// top carries selectable spans we mark. Graceful: any failure → the snippet + the external link.

import { useEffect, useRef, useState } from "react";
import { Citation } from "./SourceCard";

// A deck citation carries a synthetic accession `DECK:{ticker}:{accession}` in its evidence params.
export function deckSrc(c: Citation): string | null {
  const q = c.evidence_image_url?.split("?")[1];
  if (!q) return null;
  const accession = new URLSearchParams(q).get("accession");
  if (!accession || !accession.startsWith("DECK:")) return null;
  return `/api/evidence/deck?accession=${encodeURIComponent(accession)}`;
}

const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();

// Mark the spans in a rendered text layer that together contain the cited snippet; return the first.
function highlightLayer(layer: HTMLElement, snippet: string): HTMLElement | null {
  const needleWords = norm(snippet).split(" ").filter((w) => w.length > 2).slice(0, 8);
  if (needleWords.length < 2) return null;
  const spans = Array.from(layer.querySelectorAll("span"));
  let first: HTMLElement | null = null;
  for (const span of spans) {
    const t = norm(span.textContent || "");
    // a span is part of the citation if it shares ≥2 of the leading content words
    const hits = needleWords.filter((w) => t.includes(w)).length;
    if (hits >= 2 || (t.length > 8 && needleWords.some((w) => t.includes(w) && w.length > 5))) {
      span.classList.add("dv-hl");
      if (!first) first = span;
    }
  }
  return first;
}

export function DeckViewer({ c }: { c: Citation }) {
  const src = deckSrc(c);
  const [state, setState] = useState<"loading" | "ready" | "none">(src ? "loading" : "none");
  const [hit, setHit] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!src) return;
    let alive = true;
    const wrap = wrapRef.current;
    (async () => {
      try {
        const pdfjs: any = await import("pdfjs-dist");
        // Load the worker from a CDN matching the installed version — avoids webpack trying to bundle
        // the .mjs worker (which breaks the Next build). Self-host later if offline is required.
        pdfjs.GlobalWorkerOptions.workerSrc =
          `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;
        const res = await fetch(src);
        if (!alive) return;
        if (res.status !== 200) return setState("none");
        const data = await res.arrayBuffer();
        const pdf = await pdfjs.getDocument({ data }).promise;
        if (!alive || !wrap) return;
        wrap.innerHTML = "";
        const snippet = (c.snippet || "").trim();
        let scrolled = false;
        const N = Math.min(pdf.numPages, 80);
        for (let n = 1; n <= N; n++) {
          const page = await pdf.getPage(n);
          if (!alive) return;
          const viewport = page.getViewport({ scale: 1.4 });
          const pageDiv = document.createElement("div");
          pageDiv.className = "dv-page";
          pageDiv.style.width = `${viewport.width}px`;
          pageDiv.style.height = `${viewport.height}px`;
          const canvas = document.createElement("canvas");
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          pageDiv.appendChild(canvas);
          wrap.appendChild(pageDiv);
          await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
          // text layer (for highlight + selection) over the canvas
          try {
            const layer = document.createElement("div");
            layer.className = "dv-textlayer";
            layer.style.setProperty("--scale-factor", "1.4");  // pdf.js positions spans by this
            pageDiv.appendChild(layer);
            const tl = new pdfjs.TextLayer({
              textContentSource: await page.getTextContent(), container: layer, viewport,
            });
            await tl.render();
            if (snippet && !scrolled) {
              const target = highlightLayer(layer, snippet);
              if (target) {
                setHit(true);
                scrolled = true;
                requestAnimationFrame(() => target.scrollIntoView({ block: "center" }));
              }
            }
          } catch { /* text layer optional — slides still render */ }
        }
        if (alive) setState("ready");
      } catch {
        if (alive) setState("none");
      }
    })();
    return () => { alive = false; };
  }, [src, c.snippet]);

  if (state === "none") {
    return (
      <div className="fv-empty">
        <div className="fv-empty-quote">{c.snippet ? `“${c.snippet}”` : "발표자료를 불러올 수 없습니다."}</div>
        {c.url ? <a className="fv-empty-link" href={c.url} target="_blank" rel="noreferrer">원문 보기 ↗</a> : null}
      </div>
    );
  }
  return (
    <div className="dv">
      <div className="fv-bar mono">
        <span className="fv-status">{state === "loading" ? "발표자료 불러오는 중…" : hit ? "📊 발표자료 · 인용 부분 하이라이트됨" : "📊 발표자료(슬라이드)"}</span>
      </div>
      {state === "loading" && <div className="fv-loading"><span className="fv-spinner" /> 슬라이드 렌더링 중…</div>}
      <div className="dv-pages" ref={wrapRef} />
    </div>
  );
}
