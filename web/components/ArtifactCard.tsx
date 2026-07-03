"use client";

import { useEffect, useRef, useState } from "react";
import { CadenceTag, FreshnessDot } from "./ui";
import { TradeChart } from "./TradeChart";
import { ComputationPanel } from "./ComputationPanel";
import { demoWidget } from "./DemoWidgets";
import type { Citation } from "./SourceCard";
import type { Artifact, ArtifactCandle, ArtifactSeries, ChartAnnotations } from "../lib/types";
import { currencyOf, fmt, fmtBig, fmtPrice, fmtVol } from "../lib/format";

// Types live in lib/types.ts (FE-01); re-exported here for back-compat (importers use
// `import { Artifact } from "./ArtifactCard"`).
export type {
  Artifact, ArtifactMarker, ArtifactPriceLine, ChartAnnotations, ChartOverlay, OverlayLine,
} from "../lib/types";

// U3-02 / PH-VIZ-1: render a connector-backed Artifact as an interactive card. Time-series
// and price (candlestick) artifacts delegate to <TradeChart> (TradingView Lightweight
// Charts); a 차트/표 toggle keeps the extracted-figures table; KPI/table artifacts render
// as a sourced matrix.

const STROKES = ["#5A5A62", "#A6A6AC", "#1FA463", "#D9A300"]; // table-view legend swatches

// Responsive matrix for bare (dashboard) widgets: shows as many rows as fit the widget height
// (more rows as it grows, fewer when small — never broken), all columns (scroll-x if narrow).
function ResponsiveTable({ head, rows }: { head: string[]; rows: string[][] }) {
  const ref = useRef<HTMLDivElement>(null);
  const [vis, setVis] = useState(rows.length);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const rowH = 29, headH = 30;
      setVis(Math.max(1, Math.floor((el.clientHeight - headH) / rowH)));
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    return () => ro.disconnect();
  }, []);
  const shown = rows.slice(0, vis);
  const more = rows.length - shown.length;
  return (
    <div className="bc-tablewrap" ref={ref}>
      <table className="artifact-table kpi-table">
        <thead><tr>{head.map((h, i) => <th key={i} className={i === 0 ? "" : "mono"}>{h}</th>)}</tr></thead>
        <tbody>
          {shown.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} className={ci === 0 ? "" : "mono"}>{c}</td>)}</tr>)}
        </tbody>
      </table>
      {more > 0 && <div className="bc-more">외 {more}개 더 · 위젯을 키우면 더 보여요</div>}
    </div>
  );
}

// PH-DATA-5: a table/KPI artifact (no time series) — render the header-first matrix as a
// card so a pinned KPI card shows on the Board too. Pin/remove reuse the chart-card chrome.
function TableArtifact(
  { a, onPin, onRemove, hideTitle, bare }: { a: Artifact; onPin?: (spec: Artifact) => void; onRemove?: () => void; hideTitle?: boolean; bare?: boolean },
) {
  const [pinned, setPinned] = useState(false);
  const t = a.table ?? [];
  const [head, ...rows] = t;
  if (!head || rows.length === 0) return null;
  if (bare) return <ResponsiveTable head={head} rows={rows} />;  // dashboard widget: content only
  return (
    <div className="artifact kpi-card">
      <div className="artifact-head">
        {!hideTitle && <span className="artifact-title">{a.title}</span>}
        <FreshnessDot f={a.freshness ?? undefined} />
        {!hideTitle && <CadenceTag c={a.cadence} />}
        <span className="grow" />
        {onPin && (
          <button type="button" className="artifact-toggle" disabled={pinned}
            onClick={() => { onPin(a); setPinned(true); }}>{pinned ? "✓ 대시보드" : "＋ 대시보드"}</button>
        )}
        {onRemove && (
          <button type="button" className="artifact-toggle" onClick={onRemove} title="보드에서 제거">✕</button>
        )}
      </div>
      <table className="artifact-table kpi-table">
        <thead><tr>{head.map((h, i) => <th key={i} className={i === 0 ? "" : "mono"}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={ri}>{r.map((cell, ci) => <td key={ci} className={ci === 0 ? "" : "mono"}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
      <ComputationPanel comp={a.computation} />
      <div className="artifact-foot">
        <span className="artifact-src">
          {a.source || "출처"}{a.as_of ? <span className="mono"> · as of {a.as_of}</span> : null}
          <span className="kpi-evlabel"> · 각 수치는 공시 원문에 인용</span>
        </span>
      </div>
    </div>
  );
}

// CE-4: a 종목 내러티브 (관전 포인트) card — structured, sourced sections. Pinnable like other cards.
function NarrativeArtifact(
  { a, onPin, onRemove, hideTitle, bare }: { a: Artifact; onPin?: (spec: Artifact) => void; onRemove?: () => void; hideTitle?: boolean; bare?: boolean },
) {
  const [pinned, setPinned] = useState(false);
  const secs = a.sections ?? [];
  if (secs.length === 0) return null;
  if (bare) return (
    <div className="bc-narrwrap narrative-body">
      {secs.map((s, i) => <div key={i} className="narrative-sec"><div className="narrative-h">{s.heading}</div><p className="narrative-p">{s.body}</p></div>)}
    </div>
  );
  return (
    <div className="artifact narrative-card">
      <div className="artifact-head">
        {!hideTitle && <span className="artifact-title">{a.title}</span>}
        <FreshnessDot f={a.freshness ?? undefined} />
        {!hideTitle && <CadenceTag c={a.cadence} />}
        <span className="grow" />
        {onPin && (
          <button type="button" className="artifact-toggle" disabled={pinned}
            onClick={() => { onPin(a); setPinned(true); }}>{pinned ? "✓ 대시보드" : "＋ 대시보드"}</button>
        )}
        {onRemove && (
          <button type="button" className="artifact-toggle" onClick={onRemove} title="보드에서 제거">✕</button>
        )}
      </div>
      <div className="narrative-body">
        {secs.map((s, i) => (
          <div key={i} className="narrative-sec">
            <div className="narrative-h">{s.heading}</div>
            <p className="narrative-p">{s.body}</p>
          </div>
        ))}
      </div>
      <div className="artifact-foot">
        <span className="artifact-src">출처는 답변의 [n] 인용을 따릅니다 · 전망·매수의견 없음</span>
      </div>
    </div>
  );
}

// currency for a ticker — KR 6-digit codes are KRW, else USD.
export function ArtifactCard(
  { a, onPin, onRemove, onRefresh, onEvidence, onAnnotate, hideTitle, bare }:
  { a: Artifact; onPin?: (spec: Artifact) => void; onRemove?: () => void; onRefresh?: () => Promise<void> | void;
    onEvidence?: (c: Citation) => void;
    // PH-VIZ-5: persist the user's drawings (provided for already-pinned Board cards).
    onAnnotate?: (ann: ChartAnnotations | null) => void;
    // hideTitle: the board widget card header already shows the title — suppress the duplicate here.
    hideTitle?: boolean;
    // bare: dashboard-widget mode — render ONLY the content (no inner card head/foot/border),
    // chart fills the widget height, table shows as many rows as fit. Trust line lives on the
    // board card. Keeps widgets clean (Datadog-style).
    bare?: boolean },
) {
  const [table, setTable] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [busy, setBusy] = useState(false);
  // PH-VIZ-5: the user's drawings — seeded from the spec so a pinned chart keeps them.
  const [userAnn, setUserAnn] = useState<ChartAnnotations | null>(a.user_annotations ?? null);
  const draw = (next: ChartAnnotations | null) => { setUserAnn(next); onAnnotate?.(next); };
  // Load a GENEROUS price history independently of the agent's narrow fetch, so range buttons
  // + scrolling show real data and the table is full OHLCV (not just the close window).
  const [bars, setBars] = useState<ArtifactCandle[] | null>(null);
  const ticker = a.ticker || "";
  useEffect(() => {
    if (!ticker || (a.candles?.length ?? 0) === 0) return;  // only for price (candlestick) charts
    const market = (a.args?.market as string) || (/^\d/.test(ticker) ? "KR" : "US");
    const end = new Date().toISOString().slice(0, 10);
    const start = `${new Date().getUTCFullYear() - 8}-01-01`;  // ~8y back covers 1Y/5Y + scrolling
    let cancel = false;
    (async () => {
      try {
        const q = `ticker=${encodeURIComponent(ticker)}&market=${market}&interval=day&start_date=${start}&end_date=${end}`;
        const r = await fetch(`/api/prices?${q}`);
        if (!r.ok) return;
        const d = await r.json();
        const rows: ArtifactCandle[] = (d.prices || [])
          .filter((p: any) => p?.time)
          .map((p: any) => ({ time: String(p.time).slice(0, 10), open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume }));
        if (!cancel && rows.length) setBars(rows);
      } catch { /* keep the agent's candles on failure */ }
    })();
    return () => { cancel = true; };
  }, [ticker, a.args]);

  const currency = currencyOf(ticker);
  // KR shows a 6-digit code by default → resolve the company NAME for the title.
  const [coName, setCoName] = useState<string | null>(null);
  useEffect(() => {
    if (!ticker || currency !== "KRW") return;
    let cancel = false;
    (async () => {
      try {
        const r = await fetch(`/api/company/search?q=${encodeURIComponent(ticker)}&market=KR&limit=5`);
        if (!r.ok) return;
        const items = ((await r.json())?.results ?? []) as { name?: string; ticker?: string }[];
        const hit = items.find((x) => String(x.ticker) === ticker) || items[0];
        if (!cancel && hit?.name) setCoName(hit.name);
      } catch { /* fall back to the code */ }
    })();
    return () => { cancel = true; };
  }, [ticker, currency]);
  // display title with the KR code swapped for the name ("005930 주가" → "삼성전자 주가")
  const displayTitle = coName && ticker ? a.title.replace(ticker, coName) : a.title;
  const [rowLimit, setRowLimit] = useState(30);  // OHLCV table: show recent N, expand for more
  // Load a GENEROUS financials history for a revenue/income artifact so the chart + table show
  // the full period range (left-scroll has data), independent of the agent's narrow fetch.
  const [finSeries, setFinSeries] = useState<ArtifactSeries[] | null>(null);
  useEffect(() => {
    if (!ticker || !(a.tool || "").endsWith("__income_statements")) return;
    const market = (a.args?.market as string) || (/^\d/.test(ticker) ? "KR" : "US");
    let cancel = false;
    (async () => {
      try {
        const r = await fetch(`/api/financials?ticker=${encodeURIComponent(ticker)}&market=${market}&period=annual&limit=40`);
        if (!r.ok) return;
        const rows = ((await r.json())?.income_statements ?? []) as any[];
        const sorted = rows.filter((x) => x.report_period).sort((x, y) => (String(x.report_period) < String(y.report_period) ? -1 : 1));
        const pick = (k: string) => sorted.filter((x) => x[k] != null).map((x) => ({ x: String(x.report_period), y: x[k] as number }));
        const s: ArtifactSeries[] = [];
        const rev = pick("revenue"), ni = pick("net_income");
        if (rev.length) s.push({ label: "매출", points: rev });
        if (ni.length) s.push({ label: "순이익", points: ni });
        if (!cancel && s.length) setFinSeries(s);
      } catch { /* keep the agent's series on failure */ }
    })();
    return () => { cancel = true; };
  }, [ticker, a.tool, a.args]);

  // PH-DEMO: high-impact dashboard widgets (stat / heatmap / feed / calendar).
  const demo = demoWidget(a);
  if (demo) {
    if (bare) return <div className="dw-wrap">{demo}</div>;
    return (
      <div className="artifact">
        <div className="artifact-head">
          {!hideTitle && <span className="artifact-title">{a.title}</span>}
          <FreshnessDot f={a.freshness ?? undefined} />
          {a.live && <span className="dw-livechip"><i />LIVE</span>}
          <span className="grow" />
          {onPin && (
            <button type="button" className="artifact-toggle" disabled={pinned}
              onClick={() => { onPin(a); setPinned(true); }}>{pinned ? "✓ 대시보드" : "＋ 대시보드"}</button>
          )}
          {onRemove && <button type="button" className="artifact-toggle" onClick={onRemove} title="보드에서 제거">✕</button>}
        </div>
        <div className="dw-wrap">{demo}</div>
        <div className="artifact-foot">
          <span className="artifact-src">{a.source || "출처"}{a.as_of ? <span className="mono"> · as of {a.as_of}</span> : null}</span>
        </div>
      </div>
    );
  }
  // CE-4: a narrative artifact carries structured sections instead of a chart/table.
  if (a.kind === "narrative" && (a.sections?.length ?? 0) > 0) {
    return <NarrativeArtifact a={a} onPin={onPin} onRemove={onRemove} hideTitle={hideTitle} bare={bare} />;
  }
  // a KPI / table artifact carries a matrix instead of time series — render that shape.
  if ((a.kind === "kpi" || a.kind === "table" || (a.series?.length ?? 0) === 0) && a.table?.length) {
    return <TableArtifact a={a} onPin={onPin} onRemove={onRemove} hideTitle={hideTitle} bare={bare} />;
  }
  const series = finSeries ?? a.series ?? [];  // prefer the fuller fetched financials history (default [])
  const xs = Array.from(new Set(series.flatMap((s) => (s.points ?? []).map((p) => p.x)))).sort();
  const hasCandles = (a.candles?.length ?? 0) > 0;
  const hasOverlays = (a.overlays?.length ?? 0) > 0;  // PH-VIZ-4: technical-only chart
  // No data yet (a freshly added / templated widget before refresh, or feed/calendar): draw an
  // honest gap with the trust line — never crash, never fabricate. The board card header owns ↻.
  if (xs.length === 0 && !hasCandles && !hasOverlays) {
    if (bare) return <div className="artifact-empty">아직 데이터를 불러오지 않았어요{a.tool ? " — ↻ 로 가져옵니다." : "."}</div>;
    return (
      <div className="artifact">
        <div className="artifact-head">
          {!hideTitle && <span className="artifact-title">{displayTitle}</span>}
          <FreshnessDot f={a.freshness ?? "gap"} />
          {onRefresh && (
            <button type="button" className="artifact-toggle" disabled={busy}
              onClick={async () => { setBusy(true); try { await onRefresh(); } finally { setBusy(false); } }}>
              {busy ? "…" : "↻ 새로고침"}
            </button>
          )}
        </div>
        <div className="artifact-empty">아직 데이터를 불러오지 않았어요{a.tool ? " — ↻ 새로고침으로 출처에서 가져옵니다." : "."}</div>
        <div className="artifact-foot">
          <span className="artifact-src">{a.source || "출처"}{a.as_of ? <span className="mono"> · as of {a.as_of}</span> : null}</span>
          <FreshnessDot f={a.freshness ?? "gap"} />
        </div>
      </div>
    );
  }

  // bare (dashboard widget): just the chart, filling the widget height — clean, no chrome.
  if (bare) {
    return (
      <div className="bc-chartfill">
        <TradeChart a={a} bars={bars} series={finSeries} currency={currency} onEvidence={onEvidence} compact fillHeight />
      </div>
    );
  }

  return (
    <div className="artifact">
      <div className="artifact-head">
        {!hideTitle && <span className="artifact-title">{displayTitle}</span>}
        <FreshnessDot f={a.freshness ?? undefined} />
        {!hideTitle && <CadenceTag c={a.cadence} />}
        {series.length > 0 && (
          <button type="button" className="artifact-toggle" onClick={() => setTable((t) => !t)}>
            {table ? "📈 차트" : "⇄ 표로"}
          </button>
        )}
        {onRefresh && (
          <button type="button" className="artifact-toggle" disabled={busy}
            onClick={async () => { setBusy(true); try { await onRefresh(); } finally { setBusy(false); } }}>
            {busy ? "…" : "↻ 새로고침"}
          </button>
        )}
        {onPin && (
          <button type="button" className="artifact-toggle" disabled={pinned}
            onClick={() => { onPin({ ...a, user_annotations: userAnn ?? undefined }); setPinned(true); }}>
            {pinned ? "✓ 대시보드" : "＋ 대시보드"}
          </button>
        )}
        {onRemove && (
          <button type="button" className="artifact-toggle" onClick={onRemove} title="보드에서 제거">✕</button>
        )}
      </div>

      {table ? (
        hasCandles ? (
          // OHLCV table (candlestick) — newest first, full history; 더보기 reveals older rows.
          (() => {
            const all = [...(bars ?? a.candles ?? [])].reverse();
            const shown = all.slice(0, rowLimit);
            return (
              <>
                <table className="artifact-table">
                  <thead><tr><th>날짜</th><th>시가</th><th>고가</th><th>저가</th><th>종가</th><th>거래량</th></tr></thead>
                  <tbody>
                    {shown.map((c) => (
                      <tr key={c.time}>
                        <td className="mono">{c.time}</td>
                        <td className="mono">{fmtPrice(c.open)}</td>
                        <td className="mono">{fmtPrice(c.high)}</td>
                        <td className="mono">{fmtPrice(c.low)}</td>
                        <td className="mono">{fmtPrice(c.close)}</td>
                        <td className="mono">{fmtVol(c.volume)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {all.length > shown.length && (
                  <button type="button" className="table-more" onClick={() => setRowLimit((n) => n + 120)}>
                    ↓ 과거 더보기 ({shown.length.toLocaleString()}/{all.length.toLocaleString()})
                  </button>
                )}
              </>
            );
          })()
        ) : (
          <table className="artifact-table">
            <thead><tr><th>기간</th>{series.map((s) => <th key={s.label}>{s.label}</th>)}</tr></thead>
            <tbody>
              {[...xs].reverse().map((x) => (
                <tr key={x}>
                  <td className="mono">{x}</td>
                  {series.map((s) => {
                    const pt = (s.points ?? []).find((p) => p.x === x);
                    return <td key={s.label} className="mono">{fmt(pt?.y, s.unit, currency)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : (
        <TradeChart a={a} bars={bars} series={finSeries} currency={currency} onEvidence={onEvidence} userAnn={userAnn} onDraw={draw} />
      )}

      <ComputationPanel comp={a.computation} />
      <div className="artifact-foot">
        {!hasCandles && series.length > 0 && (
          <div className="artifact-legend">
            {series.map((s, i) => (
              <span key={s.label}><i style={{ background: STROKES[i % STROKES.length] }} /> {s.label}</span>
            ))}
          </div>
        )}
        {hasOverlays && (
          <div className="artifact-legend">
            {(a.overlays ?? []).flatMap((o) => o.lines).map((l) => (
              <span key={l.label}><i style={{ background: l.color ?? "#86868C" }} /> {l.label}</span>
            ))}
          </div>
        )}
        <span className="artifact-src">
          {a.source || "출처"}{a.as_of ? <span className="mono"> · as of {a.as_of}</span> : null}
          {a.has_gap ? <span className="artifact-gap"> · 일부 구간 공백</span> : null}
        </span>
      </div>
    </div>
  );
}
